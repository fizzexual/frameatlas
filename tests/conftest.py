from fractions import Fraction
from pathlib import Path

import av
import pytest
from PIL import Image, ImageDraw

from frameatlas.dataset import Dataset
from frameatlas.extract import extract


def pytest_addoption(parser):
    parser.addoption("--run-scale", action="store_true", help="Run the full 18,000-frame extraction/MCP test")
    parser.addoption("--scale-dir", default=None, help="Retain scale artifacts at this new directory")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-scale"):
        for item in items:
            if "scale" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Use --run-scale for the 18,000-frame integration test"))


def make_video(path: Path, count=17, rate=Fraction(60), *, timestamps=None,
               time_base=None, duplicate=False, b_frames=0):
    base = time_base or 1 / rate
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264rgb", rate=rate)
        stream.width, stream.height = 96, 54
        stream.pix_fmt = "rgb24"
        stream.time_base = base
        stream.codec_context.time_base = base
        stream.options = {"crf": "0" if not b_frames else "18", "preset": "ultrafast", "bf": str(b_frames)}
        for index in range(count):
            value = 0 if duplicate else index
            image = Image.new("RGB", (96, 54), (value % 256, value // 256 % 256, (value * 13) % 256))
            draw = ImageDraw.Draw(image)
            # Distinct binary index marker and a moving object, even after lossy encoding.
            for bit in range(16):
                draw.rectangle((bit * 6, 0, bit * 6 + 5, 7), fill="white" if value & (1 << bit) else "black")
            x = value % 80
            draw.rectangle((x, 22, x + 15, 37), fill=(255, 220, 90))
            frame = av.VideoFrame.from_image(image)
            frame.pts = timestamps[index] if timestamps is not None else index
            frame.time_base = base
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.fixture
def dataset(tmp_path):
    video = make_video(tmp_path / "input.mp4")
    extract(video, tmp_path / "dataset", reserve_bytes=0)
    return Dataset(tmp_path / "dataset")
