import json
from fractions import Fraction

import av
import pytest

from conftest import make_video
from frameatlas.dataset import Dataset
from frameatlas.extract import extract


def test_cfr_exact_timestamps_and_full_integrity(dataset):
    assert dataset.count == 17
    assert dataset.manifest["decoded_eof"] is True
    assert Fraction(dataset.manifest["duration"]) == Fraction(17, 60)
    assert [Fraction(r["relative_timestamp"]) for r in dataset.frames(0, 17)] == [Fraction(n, 60) for n in range(17)]
    assert dataset.verify()["frames_verified"] == 17


def test_b_frame_decoder_flush(tmp_path):
    video = make_video(tmp_path / "buffered.mp4", count=31, b_frames=2)
    with av.open(str(video)) as container:
        assert container.streams.video[0].codec_context.has_b_frames
    manifest = extract(video, tmp_path / "dataset", reserve_bytes=0)
    assert manifest["frame_count"] == 31
    assert Fraction(manifest["last_timestamp"]) == Fraction(30, 60)
    assert Dataset(tmp_path / "dataset").verify()["status"] == "passed"


def test_native_fractional_rate(tmp_path):
    video = make_video(tmp_path / "fractional.mp4", rate=Fraction(60000, 1001))
    extract(video, tmp_path / "dataset", reserve_bytes=0)
    ds = Dataset(tmp_path / "dataset")
    assert ds.manifest["video"]["average_frame_rate"] == "60000/1001"
    assert Fraction(ds.frame(16)["relative_timestamp"]) == Fraction(16 * 1001, 60000)


def test_variable_rate_nonzero_start(tmp_path):
    stamps = [1000, 1010, 1070, 1071, 1200, 1300]
    video = make_video(tmp_path / "vfr.mp4", count=len(stamps), timestamps=stamps, time_base=Fraction(1, 1000))
    extract(video, tmp_path / "dataset", reserve_bytes=0)
    ds = Dataset(tmp_path / "dataset")
    actual = [Fraction(r["timestamp"]) for r in ds.frames(0, len(stamps))]
    assert actual == [Fraction(n, 1000) for n in stamps]
    assert [Fraction(r["relative_timestamp"]) for r in ds.frames(0, len(stamps))] == [Fraction(n - 1000, 1000) for n in stamps]
    assert ds.at_time(.069)["index"] == 2


def test_duplicate_pixels_preserved(tmp_path):
    video = make_video(tmp_path / "duplicates.mp4", duplicate=True)
    manifest = extract(video, tmp_path / "dataset", reserve_bytes=0)
    assert manifest["unique_rgb_frames"] == 1
    assert manifest["frame_count"] == 17
    assert len(list((tmp_path / "dataset" / "frames").rglob("*.png"))) == 17


def test_never_overwrite(dataset, tmp_path):
    original = (dataset.root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="not empty"):
        extract(tmp_path / "input.mp4", dataset.root)
    assert (dataset.root / "manifest.json").read_bytes() == original


def test_disk_reserve_marks_incomplete(tmp_path):
    video = make_video(tmp_path / "source.mp4")
    with pytest.raises(OSError, match="reserve"):
        extract(video, tmp_path / "dataset", reserve_bytes=10**20)
    manifest = json.loads((tmp_path / "dataset" / "manifest.json").read_text())
    assert manifest["status"] == "incomplete"
    assert not manifest["decoded_eof"]
    with pytest.raises(ValueError, match="not a complete"):
        Dataset(tmp_path / "dataset")


def test_corrupt_source_marks_incomplete(tmp_path):
    video = tmp_path / "invalid.mp4"
    video.write_bytes(b"this is not a video")
    with pytest.raises(av.error.FFmpegError):
        extract(video, tmp_path / "dataset")
    assert json.loads((tmp_path / "dataset" / "manifest.json").read_text())["status"] == "incomplete"


def test_missing_clock_and_backwards_clock_are_not_invented(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import frameatlas.extract as module
    source = make_video(tmp_path / "source.mp4")
    real_open = av.open
    for clock, message in [(None, "no exact presentation"), (-1, "backwards")]:
        output = tmp_path / ("missing" if clock is None else "backwards")

        @contextmanager
        def mocked_open(*args, **kwargs):
            with real_open(*args, **kwargs) as container:
                class Wrapper:
                    streams = container.streams
                    def decode(self, stream):
                        for i, frame in enumerate(container.decode(stream)):
                            if i == 1:
                                frame.pts = clock
                            yield frame
                yield Wrapper()

        monkeypatch.setattr(module.av, "open", mocked_open)
        with pytest.raises(ValueError, match=message):
            extract(source, output, reserve_bytes=0)
        assert json.loads((output / "manifest.json").read_text())["status"] == "incomplete"
        monkeypatch.setattr(module.av, "open", real_open)


def test_repeated_timestamps_are_retained(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import frameatlas.extract as module
    source = make_video(tmp_path / "source.mp4")
    real_open = av.open

    @contextmanager
    def mocked_open(*args, **kwargs):
        with real_open(*args, **kwargs) as container:
            class Wrapper:
                streams = container.streams
                def decode(self, stream):
                    previous = None
                    for i, frame in enumerate(container.decode(stream)):
                        original = frame.pts
                        if i == 1:
                            frame.pts = previous
                        previous = original
                        yield frame
            yield Wrapper()

    monkeypatch.setattr(module.av, "open", mocked_open)
    manifest = extract(source, tmp_path / "dataset", reserve_bytes=0)
    assert manifest["frame_count"] == 17
    assert manifest["repeated_timestamp_frames"] == 1
    ds = Dataset(tmp_path / "dataset")
    assert ds.frame(0)["timestamp"] == ds.frame(1)["timestamp"]
    assert ds.frame(0)["image"] != ds.frame(1)["image"]
    assert ds.verify()["status"] == "passed"


def test_source_mutation_never_certified(tmp_path):
    source = make_video(tmp_path / "source.mp4", count=129)
    def mutate(count):
        if count == 128:
            with source.open("ab") as stream:
                stream.write(b"source changed")
    with pytest.raises(ValueError, match="Input changed"):
        extract(source, tmp_path / "dataset", reserve_bytes=0, progress=mutate)
    assert json.loads((tmp_path / "dataset" / "manifest.json").read_text())["status"] == "incomplete"
