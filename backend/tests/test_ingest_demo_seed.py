"""Seed + demo replay check for the nightly-batch scene (SQLite, no services)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.config.database import Base
from src.controllers import ingest_controller
from src.models import DataSource, IngestQuarantine, RawTelemetry, Signal, User
from src.services import ingest_service
from src.services.demo_data import SEED_SIGNALS, SEED_SOURCES
from src.services.ingest_demo import DEMO_BATCH_KEY, seed_demo_batch


class FakeState:
    request_id = "test"
    user_claims = {"sub": 1}
    audit_events: list = []


class FakeRequest:
    state = FakeState()
    client = SimpleNamespace(host="127.0.0.1")


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def seed_catalog(db):
    for spec in SEED_SOURCES:
        db.add(DataSource(**spec))
    db.flush()
    sources = {item.code: item for item in db.scalars(select(DataSource))}
    for spec in SEED_SIGNALS:
        db.add(
            Signal(
                code=spec["code"],
                source_id=sources[spec["source"]].id,
                device_code=spec["device_code"],
                device_name=spec["device_name"],
                measure_name=spec["measure_name"],
                tag_path=spec["tag_path"],
                unit=spec["unit"],
                sampling_interval_s=int(spec["cadence"]),
            )
        )
    db.add(User(email="engineer@tsalign.demo", password_hash="x", display_name="周砚舟", role="engineer"))
    db.commit()
    return db.query(User).first()


class DemoBatchTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.engineer = seed_catalog(self.db)
        seed_demo_batch(self.db, self.engineer)

    def tearDown(self):
        self.db.close()

    def test_seed_stops_after_two_segments(self):
        batch = ingest_service.list_batches(self.db)[0]
        self.assertEqual(batch.batch_key, DEMO_BATCH_KEY)
        self.assertEqual(batch.expected_segments, 4)
        self.assertEqual(batch.received_segments, 2)
        self.assertEqual(
            [item.segment_key for item in batch.segments if item.status == "missing"], ["N3", "N4"]
        )
        self.assertGreater(batch.accepted_rows, 0)
        self.assertEqual(batch.rejected_rows, 5)
        self.assertIsNone(batch.promoted_at)
        self.assertEqual(self.db.query(RawTelemetry).count(), 0)

        gate = ingest_service.evaluate_gate(
            self.db,
            [batch.source_id],
            batch.window_start,
            batch.window_end,
        )
        self.assertFalse(gate["can_start"])
        self.assertEqual(gate["blocking_batches"][0]["batch_key"], DEMO_BATCH_KEY)

    def test_replay_missing_segments_closes_then_opens_gate(self):
        batch = ingest_service.get_batch(self.db, ingest_service.list_batches(self.db)[0].id)
        request = FakeRequest()
        first = ingest_controller.replay_segment(request, self.db, batch.id, "N3")
        self.assertEqual(first["batch"]["received_segments"], 3)
        self.assertTrue(first["quarantined"])
        self.assertFalse(first["promoted"])
        second = ingest_controller.replay_segment(request, self.db, batch.id, "N4")
        self.assertTrue(second["promoted"])
        self.assertGreater(second["promoted_rows"], 0)
        self.assertEqual(self.db.query(RawTelemetry).count(), second["promoted_rows"])

        detail = ingest_service.detail(self.db, batch.id)
        self.assertTrue(detail["gate"]["can_start"])
        self.assertEqual(detail["status"], "complete")

        # repeated delivery of an already-received segment adds no samples
        published = self.db.query(RawTelemetry).count()
        again = ingest_controller.replay_segment(request, self.db, batch.id, "N4")
        self.assertTrue(again["duplicate_delivery"])
        self.assertEqual(again["accepted_rows"], 0)
        self.assertEqual(self.db.query(RawTelemetry).count(), published)

    def test_quarantine_reasons_are_labelled(self):
        detail = ingest_service.detail(self.db, ingest_service.list_batches(self.db)[0].id)
        reasons = {item["reason"] for item in detail["quarantine"]}
        self.assertIn("invalid_row", reasons)
        self.assertIn("missing_field", reasons)
        self.assertIn("unknown_signal", reasons)
        self.assertIn("bad_timestamp", reasons)
        self.assertIn("bad_value", reasons)
        self.assertEqual(detail["quarantine_reasons"][0]["label"], detail["quarantine"][0]["reason_label"])


if __name__ == "__main__":
    unittest.main()
