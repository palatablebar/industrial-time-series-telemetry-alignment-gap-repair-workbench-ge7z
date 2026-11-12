"""End-to-end API check for the resumable intake gate (SQLite, no services).

Run: ``cd backend && python3 -m unittest tests.test_ingest_api -v``
"""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("DB_HOST", "unused")
os.environ.setdefault("SEED_DEMO", "false")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config.database import Base, get_db
from src.main import app
from src.models import AlignmentJob, DataSource, Signal, User


class IngestApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.engine = create_engine(f"sqlite:///{cls.tmp.name}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, expire_on_commit=False)

        def override():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override
        cls.client = TestClient(app)
        db = cls.Session()
        from src.utils.password import hash_password

        cls.user = User(
            email="engineer@test.demo",
            password_hash=hash_password("Demo@2026"),
            display_name="测试工程师",
            role="engineer",
        )
        cls.source = DataSource(code="GW-API", name="API 网关", protocol="dcs", sample_interval_s=5)
        db.add_all([cls.user, cls.source])
        db.commit()
        db.add(Signal(code="A.X", source_id=cls.source.id, device_code="M-01", measure_name="X", sampling_interval_s=5))
        db.add(AlignmentJob(
            name="night gate job", device_codes=["M-01"],
            window_start=__import__("datetime").datetime(2026, 11, 10, 11, 0),
            window_end=__import__("datetime").datetime(2026, 11, 10, 12, 0),
            grid_s=5, agg_strategy="linear", fill_strategy="linear", status="pending",
        ))
        db.commit()
        cls.source_id = cls.source.id
        cls.job_id = db.query(AlignmentJob).filter(AlignmentJob.name == "night gate job").one().id
        db.close()

        login = cls.client.post("/auth/login", json={"email": "engineer@test.demo", "password": "Demo@2026"})
        cls.token = login.json()["token"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        cls.engine.dispose()
        os.unlink(cls.tmp.name)

    def test_gate_blocks_then_resumes_then_opens(self):
        created = self.client.post(
            "/ingest-batches",
            headers=self.headers,
            json={
                "source_id": self.source_id,
                "batch_key": "API-NIGHT-01",
                "window_start": "2026-11-10T11:00:00",
                "window_end": "2026-11-10T12:00:00",
                "segments": [{"key": "S1"}, {"key": "S2"}],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        batch_id = created.json()["batch"]["id"]

        detail = self.client.get(f"/ingest-batches/{batch_id}", headers=self.headers).json()
        self.assertFalse(detail["gate"]["can_start"])

        # estimate (used by the config screen) reports the closed gate
        est = self.client.post(
            "/alignment-jobs/estimate",
            headers=self.headers,
            json={
                "device_codes": ["M-01"],
                "window_start": "2026-11-10T11:00:00",
                "window_end": "2026-11-10T12:00:00",
                "grid_s": 5,
            },
        ).json()
        self.assertFalse(est["gate"]["can_start"])
        self.assertEqual(est["gate"]["blocking_batches"][0]["missing_segments"][1]["key"], "S2")

        deliver = self.client.post(
            f"/ingest-batches/{batch_id}/segments/S1",
            headers=self.headers,
            json={"rows": [
                {"signal_code": "A.X", "ts": 1794280100.0, "value": 1.0},
                {"signal_code": "A.X", "ts": "bad", "value": 2.0},
            ]},
        )
        self.assertEqual(deliver.status_code, 200, deliver.text)
        body = deliver.json()
        self.assertEqual(body["accepted_rows"], 1)
        self.assertEqual(body["rejected_rows"], 1)
        self.assertFalse(body["promoted"])
        self.assertFalse(body["duplicate_delivery"])

        again = self.client.post(
            f"/ingest-batches/{batch_id}/segments/S1", headers=self.headers, json={"rows": []}
        ).json()
        self.assertTrue(again["duplicate_delivery"])
        self.assertEqual(again["accepted_rows"], 0)

        blocked = self.client.post(
            "/alignment-jobs",
            headers=self.headers,
            json={
                "name": "gate blocked job",
                "device_codes": ["M-01"],
                "window_start": "2026-11-10T11:00:00",
                "window_end": "2026-11-10T12:00:00",
                "grid_s": 5,
            },
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["code"], "TSA-3004")

        missing = self.client.post(
            f"/ingest-batches/{batch_id}/segments/S2",
            headers=self.headers,
            json={"rows": [{"signal_code": "A.X", "ts": 1794280200.0, "value": 3.0}]},
        ).json()
        self.assertTrue(missing["promoted"])
        self.assertEqual(missing["promoted_rows"], 2)

        opened = self.client.get(f"/ingest-batches/{batch_id}", headers=self.headers).json()
        self.assertTrue(opened["gate"]["can_start"])
        self.assertEqual(opened["status"], "complete")

        # the queued overlapping job can now be started
        run = self.client.post(f"/alignment-jobs/{self.job_id}/run", headers=self.headers)
        self.assertEqual(run.status_code, 200, run.text)

    def test_unknown_segment_returns_stable_code(self):
        response = self.client.post(
            "/ingest-batches/1/segments/NOPE", headers=self.headers, json={"rows": []}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "TSA-3005")
