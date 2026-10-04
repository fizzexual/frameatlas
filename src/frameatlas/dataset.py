from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
from fractions import Fraction
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

from .extract import file_hash


class Dataset:
    def __init__(self, path: Path):
        self.root = path.expanduser().resolve(strict=True)
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("schema_version") != 1 or self.manifest.get("status") != "complete":
            raise ValueError("Dataset is not a complete FrameAtlas v1 extraction.")
        self.count = self.manifest["frame_count"]
        if not isinstance(self.count, int) or self.count < 1:
            raise ValueError("Invalid frame count.")
        self.db_path = self.safe_path(self.manifest["database"])

    def safe_path(self, name: str) -> Path:
        path = (self.root / name).resolve(strict=True)
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("Dataset path escapes its root or is not a file.")
        return path

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
        db.execute("PRAGMA trusted_schema=OFF")
        try:
            yield db
        finally:
            db.close()

    def frame(self, index: int) -> dict:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.count:
            raise ValueError(f"Frame index must be between 0 and {self.count - 1}.")
        with self.connect() as db:
            row = db.execute("SELECT payload FROM frames WHERE frame_index=?", (index,)).fetchone()
        if row is None:
            raise ValueError(f"Frame {index} is missing from the index.")
        payload = json.loads(row[0])
        if payload["index"] != index:
            raise ValueError("Frame index identity mismatch.")
        return payload

    def frames(self, start: int = 0, count: int = 32) -> list[dict]:
        if isinstance(start, bool) or isinstance(count, bool) or not isinstance(start, int) or not isinstance(count, int) or start < 0 or start >= self.count or not 1 <= count <= 200:
            raise ValueError("Use a valid start and a count between 1 and 200.")
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM frames WHERE frame_index>=? ORDER BY frame_index LIMIT ?", (start, count)).fetchall()
        payloads = [json.loads(row[0]) for row in rows]
        if [row["index"] for row in payloads] != list(range(start, min(start + count, self.count))):
            raise ValueError("Frame range is incomplete or has mismatched identities.")
        return payloads

    def at_time(self, seconds: float) -> dict:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0 or not seconds < float("inf"):
            raise ValueError("Time must be finite and nonnegative.")
        with self.connect() as db:
            row = db.execute("SELECT frame_index FROM frames ORDER BY abs(relative_seconds-?), frame_index LIMIT 1", (seconds,)).fetchone()
        return self.frame(row[0])

    def native_image(self, row: dict) -> Image.Image:
        path = self.safe_path(row["image"])
        if file_hash(path) != row["png_sha256"]:
            raise ValueError(f"PNG integrity check failed for frame {row['index']}.")
        with Image.open(path) as image:
            result = image.convert("RGB")
        if result.size != (row["width"], row["height"]):
            raise ValueError("Frame dimensions do not match the index.")
        return result

    def image_bytes(self, index: int, *, max_width: int | None = None,
                    region: list[int] | None = None, display: bool = False) -> tuple[dict, bytes, str]:
        row = self.frame(index)
        if not isinstance(display, bool):
            raise ValueError("display must be a boolean.")
        if max_width is not None and (isinstance(max_width, bool) or not isinstance(max_width, int) or not 32 <= max_width <= 8192):
            raise ValueError("max_width must be between 32 and 8192.")
        image = self.native_image(row)
        kind = "native"
        if region is not None:
            if not isinstance(region, list) or len(region) != 4 or any(isinstance(n, bool) or not isinstance(n, int) for n in region):
                raise ValueError("Region must be [x, y, width, height] in native pixels.")
            x, y, width, height = region
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > image.width or y + height > image.height:
                raise ValueError("Region extends beyond the native image.")
            image = image.crop((x, y, x + width, y + height))
            kind = "crop"
        if display:
            if region:
                raise ValueError("Display rotation and native-coordinate region cropping cannot be combined.")
            rotation = row.get("rotation_degrees", 0)
            if rotation:
                image = image.rotate(rotation, expand=True)
                if rotation % 90:
                    kind = "display_resampled"
            sar = Fraction(self.manifest["video"].get("sample_aspect_ratio") or "1")
            if sar != 1:
                if round(rotation) % 180:
                    image = image.resize((image.width, max(1, round(image.height * sar))))
                else:
                    image = image.resize((max(1, round(image.width * sar)), image.height))
            if sar != 1:
                kind = "display_resampled"
        if max_width and image.width > max_width:
            image.thumbnail((max_width, 8192), Image.Resampling.LANCZOS)
            kind = "preview" if kind == "native" else kind + "_preview"
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", compress_level=1)
        return {**row, "delivered_width": image.width, "delivered_height": image.height,
                "delivery_kind": kind, "display_orientation_applied": display}, buffer.getvalue(), kind

    def sheet(self, start: int, count: int = 32, columns: int = 4, cell_width: int = 320) -> bytes:
        if any(isinstance(n, bool) or not isinstance(n, int) for n in (count, columns, cell_width)) or not 1 <= count <= 64 or not 1 <= columns <= 8 or not 96 <= cell_width <= 640:
            raise ValueError("Contact sheets allow 1–64 frames, 1–8 columns and 96–640 pixel cells.")
        rows = self.frames(start, count)
        height = round(cell_width * 9 / 16) + 32
        sheet = Image.new("RGB", (columns * cell_width, ((len(rows) + columns - 1) // columns) * height), "#0b1016")
        draw = ImageDraw.Draw(sheet)
        for offset, row in enumerate(rows):
            x, y = (offset % columns) * cell_width, (offset // columns) * height
            with Image.open(self.safe_path(row["thumbnail"])) as image:
                thumb = ImageOps.contain(image, (cell_width - 8, height - 36))
                sheet.paste(thumb, (x + (cell_width - thumb.width) // 2, y + 4))
            draw.text((x + 6, y + height - 25), f"#{row['index']}  {row['relative_seconds']:.6f}s", fill="#e6edf5")
        buffer = io.BytesIO()
        sheet.save(buffer, format="PNG", compress_level=1)
        return buffer.getvalue()

    def comparison(self, first: int, second: int, width: int = 640) -> bytes:
        if isinstance(width, bool) or not isinstance(width, int) or not 96 <= width <= 1280:
            raise ValueError("Comparison width must be between 96 and 1280.")
        a = self.native_image(self.frame(first))
        b = self.native_image(self.frame(second))
        if a.size != b.size:
            raise ValueError("Cannot compute a pixel difference between changing dimensions.")
        difference = ImageChops.difference(a, b)
        height = max(1, round(width * a.height / a.width))
        result = Image.new("RGB", (width * 3, height + 32), "#0b1016")
        draw = ImageDraw.Draw(result)
        for i, (image, label) in enumerate([(a, f"frame {first}"), (b, f"frame {second}"), (difference, "absolute RGB difference")]):
            result.paste(image.resize((width, height)), (i * width, 0))
            draw.text((i * width + 8, height + 8), label, fill="white")
        buffer = io.BytesIO()
        result.save(buffer, format="PNG", compress_level=1)
        return buffer.getvalue()

    def verify(self) -> dict:
        if file_hash(self.safe_path(self.manifest["index"])) != self.manifest["index_sha256"]:
            raise ValueError("JSONL index hash does not match the manifest.")
        with self.connect() as db:
            total, lowest, highest = db.execute("SELECT count(*),min(frame_index),max(frame_index) FROM frames").fetchone()
        if (total, lowest, highest) != (self.count, 0, self.count - 1):
            raise ValueError("Database frame range is incomplete.")
        previous = None
        first = Fraction(self.manifest["first_timestamp"])
        seen = 0
        with self.safe_path(self.manifest["index"]).open(encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                row = self.frame(index)
                if json.loads(line) != row:
                    raise ValueError(f"JSONL and SQLite disagree at frame {index}.")
                stamp = Fraction(row["pts"]) * Fraction(row["time_base"])
                relative = stamp - first
                if stamp != Fraction(row["timestamp"]) or relative != Fraction(row["relative_timestamp"]) or float(relative) != row["relative_seconds"] or (previous is not None and stamp < previous):
                    raise ValueError(f"Timestamp integrity check failed at frame {index}.")
                with self.connect() as db:
                    if db.execute("SELECT relative_seconds FROM frames WHERE frame_index=?", (index,)).fetchone()[0] != float(relative):
                        raise ValueError("SQLite time index disagrees with exact timestamps.")
                image = self.native_image(row)
                if hashlib.sha256(image.tobytes()).hexdigest() != row["rgb_sha256"]:
                    raise ValueError(f"Decoded RGB integrity check failed at frame {index}.")
                with Image.open(self.safe_path(row["thumbnail"])) as thumbnail:
                    thumbnail.verify()
                previous = stamp
                seen += 1
        if seen != self.count or str(previous) != self.manifest["last_timestamp"]:
            raise ValueError("JSONL frame count or final timestamp disagrees with manifest.")
        return {"status": "passed", "frames_verified": self.count, "all_png_hashes_verified": True,
                "all_rgb_hashes_verified": True, "exact_timestamps_verified": True, "jsonl_sqlite_agree": True}


def image_content(data: bytes) -> dict:
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Image exceeds the 10 MiB tool budget. Request a smaller preview or native region tiles.")
    return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(data).decode("ascii")}
