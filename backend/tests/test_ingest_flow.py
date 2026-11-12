"""Resumable ingest flow checks (stdlib unittest, SQLite in-memory, no services).

Run: ``cd backend && python3 -m unittest tests.test_ingest_flow -v``
"""
from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.config.database import Base
from src.models import (
    DataSource,
    IngestBatch,
    IngestQuarantine,
    IngestSample,
    RawTelemetry,
    Signal,
    User,
)
from src.services import ingest_service
from src.services.ingest_demo import demo_rows, demo_rows_for_segment


def make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def fresh_db():
    db = make_session()
    source = DataSource(
        code="GW-T", name="测试网关", protocol="dcs", sample_interval_s=5,
        drift_hint_ms=0, status="online", workshop="", description="",
    )
    db.add(source)
    db.flush()
    for code, device in (("T.A", "M-01"), ("T.B", "M-01"), ("OTHER.C", "M-02")):
        db.add(
            Signal(
                code=code,
                source_id=source.id if code != "OTHER.C" else source.id + 1,
                device_code=device,
                device_name=device,
                measure_name=code,
                sampling_interval_s=5,
            )
        )
    # second source for the mismatch case
    db.add(DataSource(code="GW-OTHER", name="其他网关", protocol="dcs", sample_interval_s=5))
    db.commit()
    return db, source


def make_batch(db, source, segments=("A", "B", "C")):
    segment_windows = {
        "A": ("2026-11-10T11:00:00", "2026-11-10T11:20:00"),
        "B": ("2026-11-10T11:20:00", "2026-11-10T11:40:00"),
        "C": ("2026-11-10T11:40:00", "2026-11-10T12:00:00"),
    }
    payload = SimpleNamespace(
        source_id=source.id,
        batch_key="TEST-BATCH",
        window_start="2026-11-10T11:00:00",
        window_end="2026-11-10T12:00:00",
        segments=[
            SimpleNamespace(
                key=key,
                window_start=segment_windows[key][0],
                window_end=segment_windows[key][1],
                expected_rows=0,
            )
            for key in segments
        ],
        segment_count=0,
        note="",
    )
    user = User(email="t@example.com", password_hash="x", display_name="T", role="engineer")
    db.add(user)
    db.commit()
    return ingest_service.create_batch(db, payload, user)


class IngestFlowTest(unittest.TestCase):
    def setUp(self):
        self.db, self.source = fresh_db()
        self.batch = make_batch(self.db, self.source)

    def tearDown(self):
        self.db.close()

    def deliver(self, key, rows):
        return ingest_service.deliver_segment(
            self.db, self.batch.id, key, SimpleNamespace(rows=rows, note="")
        )

    def test_partial_delivery_stages_only_and_keeps_gate_closed(self):
        rows = demo_rows(self.db, "GW-T", datetime(2026, 11, 10, 11, 0), datetime(2026, 11, 10, 11, 20))
        _, _, outcome = self.deliver("A", rows)
        self.assertGreater(outcome["accepted_rows"], 0)
        self.assertFalse(outcome["promoted"])

        self.assertEqual(self.db.scalar(select(RawTelemetry.id).limit(1)), None)
        self.assertGreater(self.db.scalar(select(IngestSample.id).limit(1)) or 0, 0)

        gate = ingest_service.evaluate_gate(
            self.db, [self.source.id], self.batch.window_start, self.batch.window_end
        )
        self.assertFalse(gate["can_start"])
        self.assertEqual(gate["reason"], "batch_incomplete")
        missing = [seg["key"] for seg in gate["blocking_batches"][0]["missing_segments"]]
        self.assertEqual(missing, ["B", "C"])

    def segment_rows(self, key):
        batch = ingest_service.get_batch(self.db, self.batch.id)
        segment = next(item for item in batch.segments if item.segment_key == key)
        return demo_rows_for_segment(self.db, batch, segment)

    def test_resume_only_accepts_missing_segments_and_duplicates_add_nothing(self):
        rows_a = self.segment_rows("A")
        self.deliver("A", rows_a)
        accepted_a = self.batch.segments[0].accepted_rows
        self.batch = ingest_service.get_batch(self.db, self.batch.id)
        again = self.deliver("A", rows_a)
        self.assertTrue(again[2]["duplicate_delivery"])
        self.assertEqual(again[2]["accepted_rows"], 0)
        self.batch = ingest_service.get_batch(self.db, self.batch.id)
        self.assertEqual(self.batch.accepted_rows, accepted_a)

        self.deliver("B", self.segment_rows("B"))
        self.deliver("C", self.segment_rows("C"))
        self.batch = ingest_service.get_batch(self.db, self.batch.id)
        self.assertEqual(self.batch.status, "complete")
        self.assertGreater(self.db.query(RawTelemetry).count(), 0)

        # a completed batch still rejects repeat deliveries without adding samples
        published = self.db.query(RawTelemetry).count()
        again = self.deliver("B", self.segment_rows("B"))
        self.assertTrue(again[2]["duplicate_delivery"])
        self.assertEqual(again[2]["accepted_rows"], 0)
        self.assertEqual(self.db.query(RawTelemetry).count(), published)

        gate = ingest_service.evaluate_gate(
            self.db, [self.source.id], self.batch.window_start, self.batch.window_end
        )
        self.assertTrue(gate["can_start"])

    def test_bad_rows_are_quarantined_individually_with_reasons(self):
        rows = demo_rows(self.db, "GW-T", datetime(2026, 11, 10, 11, 0), datetime(2026, 11, 10, 11, 5))
        rows += [
            {"signal_code": "T.A", "ts": "not-a-time", "value": 1.0},
            {"signal_code": "T.A", "ts": 1794300000.0, "value": "nope"},
            {"signal_code": "MISSING", "ts": 1794300000.0, "value": 1.0},
            {"signal_code": "OTHER.C", "ts": 1794300000.0, "value": 1.0},
            {"ts": 1794300000.0, "value": 1.0},
            "broken",
        ]
        _, _, outcome = self.deliver("A", rows)
        reasons = {item.reason for item in outcome["quarantined"]}
        self.assertEqual(
            reasons,
            {"bad_timestamp", "bad_value", "unknown_signal", "signal_source_mismatch", "missing_field", "invalid_row"},
        )
        self.assertEqual(outcome["rejected_rows"], 6)
        self.assertGreater(outcome["accepted_rows"], 0)

    def test_unknown_segment_is_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self.deliver("Z", [])
        self.assertEqual(getattr(ctx.exception, "code", None), "TSA-3005")

    def test_quarantine_reason_labels_survive_detail(self):
        rows = demo_rows(self.db, "GW-T", datetime(2026, 11, 10, 11, 0), datetime(2026, 11, 10, 11, 5))
        rows.append({"signal_code": "T.A", "ts": "bad", "value": 1.0})
        self.deliver("A", rows)
        detail = ingest_service.detail(self.db, self.batch.id)
        self.assertEqual(detail["quarantine"][0]["reason"], "bad_timestamp")
        self.assertEqual(detail["quarantine"][0]["reason_label"], "时间戳无法解析")
        self.assertEqual(detail["quarantine_reasons"][0]["count"], 1)


if __name__ == "__main__":
    unittest.main()
