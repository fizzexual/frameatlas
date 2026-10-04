"""Delivery accounting is distinct from an agent's declared observations."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .dataset import Dataset


class Coverage:
    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.path = dataset.root / "coverage.sqlite"
        if self.path.is_symlink():
            raise ValueError("Coverage database cannot be a symlink.")
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS deliveries (session TEXT, frame_index INTEGER, kind TEXT, delivered_at TEXT, PRIMARY KEY(session,frame_index,kind))")
            db.execute("CREATE TABLE IF NOT EXISTS observations (session TEXT, frame_index INTEGER, note TEXT, observed_at TEXT, PRIMARY KEY(session,frame_index))")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA trusted_schema=OFF")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def session(value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise ValueError("Session must be a nonempty string of at most 100 characters.")
        return value

    def delivered(self, session: str, indices: list[int], kind: str):
        session = self.session(session)
        if kind not in {"native", "preview", "crop", "crop_preview", "display_resampled", "display_resampled_preview"}:
            raise ValueError("Invalid delivery kind.")
        for index in indices:
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.dataset.count:
                raise ValueError("Invalid delivery index.")
        timestamp = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO deliveries VALUES (?,?,?,?)", [(session, n, kind, timestamp) for n in indices])

    def observe(self, session: str, start: int, count: int, note: str) -> dict:
        session = self.session(session)
        rows = self.dataset.frames(start, count)
        if count > 64 or len(rows) != count or not isinstance(note, str) or not note.strip() or len(note) > 4000:
            raise ValueError("Observations require 1–64 existing frames and a nonempty note of at most 4000 characters.")
        indices = [row["index"] for row in rows]
        with self.connect() as db:
            for index in indices:
                found = db.execute("SELECT 1 FROM deliveries WHERE session=? AND frame_index=? AND kind IN ('native','preview','display_resampled','display_resampled_preview')", (session, index)).fetchone()
                if found is None:
                    raise ValueError(f"Frame {index} has not been delivered individually in this session. A contact sheet or metadata list does not qualify.")
            stamp = datetime.now(timezone.utc).isoformat()
            db.executemany("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)", [(session, index, note, stamp) for index in indices])
        return self.report(session)

    def report(self, session: str = "agent") -> dict:
        session = self.session(session)
        with self.connect() as db:
            native = db.execute("SELECT count(*) FROM deliveries WHERE session=? AND kind='native'", (session,)).fetchone()[0]
            delivered = db.execute("SELECT count(DISTINCT frame_index) FROM deliveries WHERE session=? AND kind IN ('native','preview','display_resampled','display_resampled_preview')", (session,)).fetchone()[0]
            native_indices = {r[0] for r in db.execute("SELECT frame_index FROM deliveries WHERE session=? AND kind='native'", (session,))}
            observations = db.execute("SELECT count(*) FROM observations WHERE session=?", (session,)).fetchone()[0]
            viewed = {r[0] for r in db.execute("SELECT frame_index FROM observations WHERE session=?", (session,))}
            gaps = [n for n in range(self.dataset.count) if n not in viewed]
            notes = [{"frame_index": n, "note": note} for n, note in db.execute("SELECT frame_index,note FROM observations WHERE session=? ORDER BY frame_index DESC LIMIT 8", (session,))]
        return {"session": session, "total_frames": self.dataset.count,
                "individual_images_delivered": delivered, "native_images_delivered": native,
                "frames_with_recorded_observations": observations, "first_unobserved_frame": gaps[0] if gaps else None,
                "next_unobserved_frames": gaps[:64], "all_frames_native_delivered": native == self.dataset.count,
                "next_native_undelivered_frames": [n for n in range(self.dataset.count) if n not in native_indices][:64],
                "all_frames_have_observations": observations == self.dataset.count,
                "native_delivery_and_declared_review_complete": native == observations == self.dataset.count,
                "recent_notes": notes,
                "meaning": "Delivery records track image responses served, not client display acknowledgments. Observations are agent declarations, not independent proof of attention, understanding, or human-equivalent perception."}
