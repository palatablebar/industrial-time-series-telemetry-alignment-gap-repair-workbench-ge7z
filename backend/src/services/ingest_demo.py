"""Deterministic demo payloads for the resumable intake screen.

The seeded night batch deliberately stops after segment N2 so the UI can show
"received / missing" progress and a closed alignment gate. The remaining segments
can be fetched as generated rows and delivered through the same public endpoint
that a real gateway would use, which keeps the resume path honest.
"""
from __future__ import annotations

import math
import random
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import IngestSegmentStatus
from src.models import DataSource, IngestBatch, IngestSegment, Signal
from src.services import ingest_service
from src.utils.timeutil import iso, parse_iso, to_epoch

DEMO_BATCH_KEY = "GW-CHZ-02-NIGHT-20261110B"
DEMO_SOURCE_CODE = "GW-CHZ-02"
DEMO_WINDOW_START = "2026-11-10T11:00:00"
DEMO_WINDOW_END = "2026-11-10T13:00:00"
DEMO_SEGMENTS = [
    ("N1", "2026-11-10T11:00:00", "2026-11-10T11:30:00"),
    ("N2", "2026-11-10T11:30:00", "2026-11-10T12:00:00"),
    ("N3", "2026-11-10T12:00:00", "2026-11-10T12:30:00"),
    ("N4", "2026-11-10T12:30:00", "2026-11-10T13:00:00"),
]
# rows with a deliberate defect, quarantined individually so the rest still lands
_IN_WINDOW_TS = round(to_epoch(parse_iso("2026-11-10T11:45:00")), 4)
DEMO_BAD_ROWS = [
    {"signal_code": "M-07.CUR", "ts": "not-a-timestamp", "value": 42.1, "seq": 9901},
    {"signal_code": "M-07.CUR", "ts": _IN_WINDOW_TS, "value": "hot", "seq": 9902},
    {"signal_code": "M-07.NOT-ON-GATEWAY", "ts": _IN_WINDOW_TS, "value": 1.0, "seq": 9903},
    {"note": "gateway lost the sensor id for this row", "ts": _IN_WINDOW_TS, "value": 3.0},
    "corrupt line from DCS export",
]


def _demo_shape(code: str, index: int) -> tuple[float, float]:
    """Stable per-signal baseline/amplitude so the replay looks like plant data."""
    digest = sum(ord(char) for char in code)
    base = 8.0 + (digest % 83) * 0.9
    scale = 1.5 + (digest % 7) * 0.4
    period = 37.0 + (digest % 11) * 3.0
    value = base + scale * math.sin(2 * math.pi * index / (period * 20.0))
    return round(value, 5), round(scale * 0.004, 5)


def demo_rows(
    db: Session, source_code: str, window_start: datetime, window_end: datetime, *, with_bad_rows: bool = False
) -> list[dict]:
    source = db.scalar(select(DataSource).where(DataSource.code == source_code))
    if source is None:
        return []
    signals = list(
        db.scalars(select(Signal).where(Signal.source_id == source.id).order_by(Signal.id))
    )
    start = to_epoch(window_start)
    end = to_epoch(window_end)
    rows: list[dict] = []
    seeded = random.Random(f"{source_code}-{iso(window_start)}-{iso(window_end)}")
    for signal in signals:
        cadence = max(1, int(signal.sampling_interval_s or 5))
        index = 0
        ts = start
        while ts < end:
            value, noise = _demo_shape(signal.code, index)
            rows.append(
                {
                    "signal_code": signal.code,
                    "ts": round(ts + seeded.uniform(0.0, 0.004), 4),
                    "value": round(value + seeded.gauss(0.0, noise), 5),
                    "seq": index,
                }
            )
            index += 1
            ts = start + index * cadence
    return rows + (list(DEMO_BAD_ROWS) if with_bad_rows else [])


def demo_rows_for_segment(
    db: Session, batch: IngestBatch, segment: IngestSegment, *, with_bad_rows: bool = False
) -> list[dict]:
    start, end = ingest_service.segment_window_start(batch, segment), ingest_service.segment_window_end(batch, segment)
    return demo_rows(
        db, batch.source.code, start, end, with_bad_rows=with_bad_rows
    )


def seed_demo_batch(db: Session, engineer) -> dict:
    """Create the incomplete night batch once; never clobber operator progress."""
    existing = db.scalar(select(IngestBatch).where(IngestBatch.batch_key == DEMO_BATCH_KEY))
    if existing is not None:
        return {
            "created": False,
            "batch_key": DEMO_BATCH_KEY,
            "received_segments": existing.received_segments,
            "missing_segments": [
                segment.segment_key
                for segment in existing.segments
                if segment.status == IngestSegmentStatus.MISSING.value
            ],
        }

    source = db.scalar(select(DataSource).where(DataSource.code == DEMO_SOURCE_CODE))
    if source is None:
        return {"created": False, "batch_key": DEMO_BATCH_KEY, "reason": "source_missing"}
    payload = SimpleNamespace(
        source_id=source.id,
        batch_key=DEMO_BATCH_KEY,
        window_start=DEMO_WINDOW_START,
        window_end=DEMO_WINDOW_END,
        segments=[
            SimpleNamespace(
                key=key,
                window_start=start,
                window_end=end,
                expected_rows=0,
            )
            for key, start, end in DEMO_SEGMENTS
        ],
        segment_count=0,
        note="夜间回传第二批：N1/N2 已到，N3/N4 尚未到齐，用于演示分批续传。",
    )
    batch = ingest_service.create_batch(db, payload, engineer)

    for key, _start, _end in DEMO_SEGMENTS[:2]:
        segment = next(item for item in batch.segments if item.segment_key == key)
        rows = demo_rows_for_segment(db, batch, segment, with_bad_rows=(key == "N2"))
        ingest_service.deliver_segment(
            db, batch.id, key, SimpleNamespace(rows=rows, note="seed replay")
        )

    db.refresh(batch)
    return {
        "created": True,
        "batch_key": batch.batch_key,
        "status": batch.status,
        "received_segments": batch.received_segments,
        "missing_segments": [
            segment.segment_key
            for segment in batch.segments
            if segment.status == IngestSegmentStatus.MISSING.value
        ],
    }
