"""Decode every frame once; pixels and timestamps come from the same AVFrame."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from typing import Callable

import av
from PIL import Image, ImageChops, ImageStat

from . import __version__


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def rational(value) -> str | None:
    return str(Fraction(value)) if value is not None else None


def extract(video: Path, output: Path, *, video_stream: int = 0,
            thumbnail_width: int = 320, png_compression: int = 1,
            reserve_bytes: int = 256 * 1024 * 1024,
            progress: Callable[[int], None] | None = None) -> dict:
    """An existing nonempty output is never overwritten; failed runs stay explicit."""
    video = video.expanduser().resolve(strict=True)
    if not video.is_file():
        raise ValueError("Input must be a local video file.")
    output = output.expanduser().resolve()
    if output == video or output in video.parents:
        raise ValueError("Dataset output cannot contain the input video.")
    if thumbnail_width < 32 or thumbnail_width > 2048:
        raise ValueError("Thumbnail width must be between 32 and 2048.")
    if png_compression not in range(10) or reserve_bytes < 0:
        raise ValueError("Invalid compression or disk reserve.")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory is not empty; choose a new dataset directory.")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    source_stat = video.stat()
    source_hash = file_hash(video)
    manifest = {
        "schema_version": 1, "tool_version": __version__, "status": "extracting",
        "source": {"name": video.name, "bytes": source_stat.st_size, "sha256": source_hash},
        "frame_count": 0, "index": "frames.jsonl", "database": "index.sqlite",
        "pixel_policy": "Lossless PNG of decoded 8-bit RGB coded pixels. No temporal sampling, rescaling, interpolation or deduplication. RGB conversion is not the original compressed bitstream or an HDR display transform.",
        "coverage_definition": "Every frame emitted by the selected decoder through EOF. Declared frame counts, when present, must agree.",
        "review": "Extraction does not mean an AI has seen or understood the images.",
        "decoder": {"pyav": av.__version__, "libraries": av.library_versions},
    }
    atomic_json(output / "manifest.json", manifest)
    count = 0
    first = previous_time = previous_small = None
    repeated_pts = 0
    corrupt = 0
    total_bytes = 0
    try:
        with av.open(str(video)) as container, closing(sqlite3.connect(output / "index.sqlite")) as db, \
                (output / "frames.jsonl").open("w", encoding="utf-8", newline="\n") as index:
            streams = list(container.streams.video)
            if video_stream < 0 or video_stream >= len(streams):
                raise ValueError("Selected video stream does not exist.")
            stream = streams[video_stream]
            if int(stream.disposition) & 0x400:
                raise ValueError("The selected stream is an attached cover picture, not a video.")
            rate = stream.average_rate
            context = stream.codec_context
            manifest["video"] = {
                "stream_ordinal": video_stream, "stream_index": stream.index,
                "codec": context.name, "coded_width": context.width, "coded_height": context.height,
                "average_frame_rate": rational(rate), "stream_time_base": rational(stream.time_base),
                "declared_frames": stream.frames or None,
                "sample_aspect_ratio": rational(stream.sample_aspect_ratio) if stream.sample_aspect_ratio else "1",
                "metadata": dict(stream.metadata),
                "orientation": "Coded orientation is preserved. Display rotation, when exposed by the decoded frame, is recorded separately.",
            }
            db.execute("CREATE TABLE frames (frame_index INTEGER PRIMARY KEY, relative_seconds REAL NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE INDEX frame_time ON frames(relative_seconds)")
            cuts = []
            unique_hashes = set()
            for frame in container.decode(stream):
                if frame.pts is None or frame.time_base is None:
                    raise ValueError(f"Frame {count} has no exact presentation timestamp; extraction will not invent one from FPS.")
                stamp = Fraction(frame.pts) * frame.time_base
                if first is None:
                    first = stamp
                if previous_time is not None:
                    if stamp < previous_time:
                        raise ValueError(f"Presentation clock goes backwards at frame {count}; dataset is incomplete.")
                    repeated_pts += int(stamp == previous_time)
                if getattr(frame, "is_corrupt", False):
                    corrupt += 1
                    raise ValueError(f"Decoder marked frame {count} corrupt; dataset will not be certified complete.")
                if count % 64 == 0 and shutil.disk_usage(output).free < reserve_bytes:
                    raise OSError("Disk reserve reached. Extracting every native frame can use much more space than the compressed video.")
                image = frame.to_image().convert("RGB")
                pixels_hash = hashlib.sha256(image.tobytes()).hexdigest()
                unique_hashes.add(pixels_hash)
                folder = f"{count // 1000:05d}"
                filename = f"{count:09d}"
                frame_path = Path("frames") / folder / (filename + ".png")
                thumb_path = Path("thumbnails") / folder / (filename + ".jpg")
                (output / frame_path.parent).mkdir(parents=True, exist_ok=True)
                (output / thumb_path.parent).mkdir(parents=True, exist_ok=True)
                image.save(output / frame_path, compress_level=png_compression)
                thumb = image.copy()
                thumb.thumbnail((thumbnail_width, thumbnail_width), Image.Resampling.LANCZOS)
                thumb.save(output / thumb_path, quality=82)
                small = image.convert("L").resize((80, 45), Image.Resampling.BOX)
                delta = None if previous_small is None else ImageStat.Stat(ImageChops.difference(small, previous_small)).mean[0]
                if delta is not None and delta >= 28:
                    cuts.append({"frame_index": count, "relative_timestamp": str(stamp - first), "luma_mae_80x45": delta})
                duration = getattr(frame, "duration", 0)
                rotation = float(getattr(frame, "rotation", 0) or 0)
                row = {
                    "index": count, "pts": frame.pts, "time_base": str(frame.time_base),
                    "timestamp": str(stamp), "relative_timestamp": str(stamp - first),
                    "relative_seconds": float(stamp - first),
                    "duration": str(Fraction(duration) * frame.time_base) if duration else None,
                    "width": image.width, "height": image.height, "rotation_degrees": rotation,
                    "image": frame_path.as_posix(), "thumbnail": thumb_path.as_posix(),
                    "rgb_sha256": pixels_hash, "png_sha256": file_hash(output / frame_path),
                    "key_frame": bool(frame.key_frame), "luma_delta_80x45": delta,
                    "source_pixel_format": frame.format.name,
                }
                payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                index.write(payload + "\n")
                db.execute("INSERT INTO frames VALUES (?, ?, ?)", (count, row["relative_seconds"], payload))
                total_bytes += (output / frame_path).stat().st_size + (output / thumb_path).stat().st_size
                count += 1
                previous_time, previous_small = stamp, small
                if count % 128 == 0:
                    db.commit()
                    index.flush()
                    manifest["frame_count"] = count
                    atomic_json(output / "manifest.json", manifest)
                    if progress:
                        progress(count)
            db.commit()
            if count == 0:
                raise ValueError("The selected stream yielded no video frames.")
            declared = stream.frames or None
            if declared is not None and declared != count:
                raise ValueError(f"Decoded {count} frames, but the stream declares {declared}; dataset is incomplete.")
            last_duration = row["duration"]
            manifest.update({
                "status": "complete", "frame_count": count, "decoded_eof": True,
                "first_timestamp": str(first), "last_timestamp": str(previous_time),
                "presentation_span": str(previous_time - first),
                "last_frame_duration": last_duration,
                "duration": str(previous_time - first + Fraction(last_duration)) if last_duration else None,
                "repeated_timestamp_frames": repeated_pts, "unique_rgb_frames": len(unique_hashes),
                "corrupt_frames": corrupt, "media_bytes": total_bytes,
                "cut_candidates": cuts,
                "cut_note": "Adjacent reduced-luminance changes are navigation hints, not verified scene boundaries or camera motion.",
            })
        current_stat = video.stat()
        if (current_stat.st_size, current_stat.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns) or file_hash(video) != source_hash:
            raise ValueError("Input changed during extraction; dataset is incomplete.")
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
        manifest["index_sha256"] = file_hash(output / "frames.jsonl")
        atomic_json(output / "manifest.json", manifest)
        if progress:
            progress(count)
        return manifest
    except BaseException as error:
        manifest.update({"status": "incomplete", "frame_count": count, "decoded_eof": False,
                         "error": str(error) or type(error).__name__, "elapsed_seconds": round(time.monotonic() - started, 3)})
        atomic_json(output / "manifest.json", manifest)
        raise
