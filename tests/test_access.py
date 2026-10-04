import base64
import io
import json
import sqlite3

import pytest
from PIL import Image

from frameatlas.coverage import Coverage
from frameatlas.dataset import image_content
from frameatlas.mcp import ToolServer, stdio


@pytest.mark.parametrize("index", [-1, 17, 1.2, True, "1"])
def test_invalid_indices(dataset, index):
    with pytest.raises(ValueError):
        dataset.frame(index)


def test_png_integrity(dataset):
    dataset.safe_path(dataset.frame(0)["image"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        dataset.image_bytes(0)


def test_index_disagreement_detected(dataset):
    with sqlite3.connect(dataset.db_path) as db:
        row = dataset.frame(0)
        row["pts"] += 1
        db.execute("UPDATE frames SET payload=? WHERE frame_index=0", (json.dumps(row),))
    with pytest.raises(ValueError, match="disagree"):
        dataset.verify()


def test_path_confinement(dataset, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("not exposed")
    for name in ["../secret.txt", str(outside)]:
        with pytest.raises(ValueError, match="escapes"):
            dataset.safe_path(name)


def test_native_preview_crop_are_separate(dataset):
    server = ToolServer(dataset)
    server.call("get_frame", {"index": 0})
    server.call("get_frame", {"index": 1, "max_width": 32})
    result = server.call("get_frame", {"index": 2, "region": [0, 0, 16, 16]})
    with Image.open(io.BytesIO(base64.b64decode(result["content"][1]["data"]))) as image:
        assert image.size == (16, 16)
    server.call("list_frames", {"start": 0, "count": 17})
    server.call("get_contact_sheet", {"start": 0, "count": 17})
    server.call("compare_frames", {"first": 0, "second": 1})
    report = server.coverage.report()
    assert report["native_images_delivered"] == 1
    assert report["individual_images_delivered"] == 2
    assert report["frames_with_recorded_observations"] == 0
    with pytest.raises(ValueError, match="not been delivered individually"):
        server.call("record_observation", {"start": 2, "count": 1, "note": "crop only"})
    server.call("record_observation", {"start": 0, "count": 2, "note": "Two synthetic test images, inspected in a unit test."})
    assert server.coverage.report()["first_unobserved_frame"] == 2
    assert not server.coverage.report()["native_delivery_and_declared_review_complete"]
    assert Coverage(dataset).report()["frames_with_recorded_observations"] == 2
    assert Coverage(dataset).report("another-session")["individual_images_delivered"] == 0


def test_end_batch_and_complete_native_delivery(dataset):
    server = ToolServer(dataset)
    for start in range(0, dataset.count, 8):
        result = server.call("get_batch", {"start": start, "count": 8})
        metadata = json.loads(result["content"][-1]["text"])
        assert metadata["returned_indices"] == list(range(start, min(start + 8, dataset.count)))
    assert metadata["next_start"] is None
    assert server.coverage.report()["all_frames_native_delivered"]
    assert not server.coverage.report()["all_frames_have_observations"]


@pytest.mark.parametrize("args", [{"start": 0, "count": 9}, {"start": True}, {"start": 0, "unexpected": 1}, {"start": 0, "display": "yes"}, {"start": 0, "max_width": 12}])
def test_bad_tool_arguments_no_delivery(dataset, args):
    server = ToolServer(dataset)
    with pytest.raises(ValueError):
        server.call("get_batch", args)
    assert server.coverage.report()["native_images_delivered"] == 0


def test_failed_batch_is_not_counted(dataset, monkeypatch):
    original = dataset.image_bytes
    def oversized(index, **kwargs):
        row, data, kind = original(index, **kwargs)
        return row, bytes(11 * 1024 * 1024), kind
    monkeypatch.setattr(dataset, "image_bytes", oversized)
    server = ToolServer(dataset)
    with pytest.raises(ValueError, match="10 MiB"):
        server.call("get_batch", {"start": 0})
    assert server.coverage.report()["native_images_delivered"] == 0


def test_aggregate_budget_is_atomic(dataset, monkeypatch):
    original = dataset.image_bytes
    def oversized(index, **kwargs):
        row, data, kind = original(index, **kwargs)
        return row, bytes(7 * 1024 * 1024), kind
    monkeypatch.setattr(dataset, "image_bytes", oversized)
    server = ToolServer(dataset)
    with pytest.raises(ValueError, match="20 MiB"):
        server.call("get_batch", {"start": 0, "count": 3})
    assert server.coverage.report()["native_images_delivered"] == 0


def test_display_rotation_and_aspect_ratio_are_explicit(dataset, monkeypatch):
    original = dataset.frame
    def rotated(index):
        return {**original(index), "rotation_degrees": 90}
    monkeypatch.setattr(dataset, "frame", rotated)
    metadata, _, kind = dataset.image_bytes(0, display=True)
    assert (metadata["delivered_width"], metadata["delivered_height"]) == (54,96)
    assert kind == "native"  # A 90-degree rotation preserves every pixel without resampling.
    dataset.manifest["video"]["sample_aspect_ratio"] = "2"
    metadata, _, kind = dataset.image_bytes(0, display=True)
    assert (metadata["delivered_width"], metadata["delivered_height"]) == (54,192)
    assert kind == "display_resampled"
    assert dataset.image_bytes(0, display=True, max_width=32)[2] == "display_resampled_preview"


@pytest.mark.parametrize("region", [[0,0,0,1],[-1,0,1,1],[95,0,2,1],"0,0,1,1",[0,0,True,1]])
def test_invalid_regions(dataset, region):
    with pytest.raises(ValueError):
        dataset.image_bytes(0, region=region)


def test_stdio_lifecycle_and_images(dataset):
    requests = [
        {"jsonrpc":"2.0","id":0,"method":"tools/list"},
        {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}}},
        {"jsonrpc":"2.0","method":"notifications/initialized"},
        {"jsonrpc":"2.0","id":2,"method":"tools/list"},
        {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_batch","arguments":{"start":0,"count":2}}},
        {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_batch","arguments":{"start":0,"count":9}}},
        {"jsonrpc":"2.0","id":5,"method":"nonexistent"},
    ]
    output = io.StringIO()
    stdio(dataset, io.StringIO("\n".join(json.dumps(r) for r in requests)+"\n"), output)
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["id"] for r in responses] == [0,1,2,3,4,5]
    assert "error" in responses[0]
    assert responses[1]["result"]["protocolVersion"] == "2025-06-18"
    assert len(responses[2]["result"]["tools"]) == 9
    images = [c for c in responses[3]["result"]["content"] if c["type"] == "image"]
    assert len(images) == 2
    assert responses[4]["result"]["isError"]
    assert responses[5]["error"]["code"] == -32601
    assert Coverage(dataset).report()["native_images_delivered"] == 2


def test_broken_stdout_does_not_count_delivery(dataset):
    class Broken(io.StringIO):
        def write(self, value):
            if '"type": "image"' in value:
                raise BrokenPipeError()
            return super().write(value)
    messages = [
        {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}},
        {"jsonrpc":"2.0","method":"notifications/initialized"},
        {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_frame","arguments":{"index":0}}},
    ]
    with pytest.raises(BrokenPipeError):
        stdio(dataset, io.StringIO("\n".join(json.dumps(r) for r in messages)), Broken())
    assert Coverage(dataset).report()["native_images_delivered"] == 0
