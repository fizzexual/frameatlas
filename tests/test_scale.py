"""Real 18,000-frame test across the public MCP stdio entry point."""
import base64
import hashlib
import io
import json
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

import pytest
from PIL import Image

from conftest import make_video
from frameatlas.dataset import Dataset
from frameatlas.extract import atomic_json, extract


@pytest.mark.scale
def test_18000_frames_exact_300_seconds_and_all_mcp_images(tmp_path, request):
    target = request.config.getoption("--scale-dir")
    root = Path(target).resolve() if target else tmp_path
    if target:
        root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    video = make_video(root / "300s-60fps.mp4", count=18000)
    manifest = extract(video, root / "dataset", reserve_bytes=0)
    dataset = Dataset(root / "dataset")
    assert manifest["frame_count"] == 18000
    assert manifest["unique_rgb_frames"] == 18000
    assert Fraction(manifest["duration"]) == 300
    assert manifest["video"]["average_frame_rate"] == "60"
    assert Fraction(dataset.frame(17999)["relative_timestamp"]) == Fraction(17999, 60)
    verified = dataset.verify()
    extracted_at = time.monotonic()
    process = subprocess.Popen([sys.executable, "-m", "frameatlas", "mcp", str(dataset.root)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8",
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    request_id = 0
    def send(method, params=None, notification=False):
        nonlocal request_id
        request_id += 1
        message = {"jsonrpc":"2.0", "method":method, "params":params or {}}
        if not notification:
            message["id"] = request_id
        process.stdin.write(json.dumps(message)+"\n")
        process.stdin.flush()
        if notification:
            return None
        line = process.stdout.readline()
        assert line, "MCP subprocess exited before response"
        response = json.loads(line)
        assert response["id"] == request_id
        assert "error" not in response
        assert not response["result"].get("isError")
        return response["result"]
    received = []
    try:
        send("initialize", {"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"scale-validator","version":"1"}})
        send("notifications/initialized", notification=True)
        for start in range(0, 18000, 8):
            result = send("tools/call", {"name":"get_batch","arguments":{"start":start,"count":8,"session":"transport-validation"}})
            content = result["content"]
            for offset in range(8):
                metadata = json.loads(content[offset*2]["text"])
                assert metadata["index"] == start + offset
                block = content[offset*2+1]
                assert block["type"] == "image" and block["mimeType"] == "image/png"
                with Image.open(io.BytesIO(base64.b64decode(block["data"]))) as image:
                    assert image.size == (96,54)
                    assert hashlib.sha256(image.convert("RGB").tobytes()).hexdigest() == dataset.frame(start+offset)["rgb_sha256"]
                received.append(metadata["index"])
            batch = json.loads(content[-1]["text"])
            assert batch["next_start"] == (start+8 if start+8<18000 else None)
        coverage_result = send("tools/call", {"name":"review_coverage","arguments":{"session":"transport-validation"}})
        coverage = json.loads(coverage_result["content"][0]["text"])
        assert received == list(range(18000))
        assert coverage["native_images_delivered"] == 18000
        assert coverage["frames_with_recorded_observations"] == 0
        assert not coverage["native_delivery_and_declared_review_complete"]
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        errors = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
    assert process.returncode == 0, errors
    assert not errors, errors
    report = {"status":"passed", "source":"synthetic 96x54 RGB video", "duration_seconds":300,
              "native_fps":60, "frames_extracted":18000, "frames_verified":verified,
              "mcp_images_received_and_rgb_verified":len(received), "mcp_batch_calls":2250,
              "coverage":coverage, "ai_understanding_tested":False,
              "extraction_and_verify_seconds":round(extracted_at-started,3),
              "total_seconds":round(time.monotonic()-started,3), "dataset":str(dataset.root)}
    atomic_json(root / "validation-report.json", report)
