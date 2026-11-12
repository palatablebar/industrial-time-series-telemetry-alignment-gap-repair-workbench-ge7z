"""Ingest batch endpoints (create → deliver segments → gate)."""
from sqlalchemy.orm import Session

from src.constants.enums import IngestSegmentStatus
from src.middlewares.audit_logger import record
from src.services import ingest_service


def create_batch(request, db: Session, payload, user) -> dict:
    batch = ingest_service.create_batch(db, payload, user)
    record(
        request,
        "ingest_batch.create",
        "ingest_batch",
        batch.id,
        after={
            "batch_key": batch.batch_key,
            "source": batch.source.code,
            "segments": batch.expected_segments,
        },
    )
    return {
        "batch": ingest_service.to_dict(db, batch),
        "gate": ingest_service.evaluate_gate(
            db, [batch.source_id], batch.window_start, batch.window_end
        ),
    }


def list_batches(db: Session) -> dict:
    items = [ingest_service.to_dict(db, batch) for batch in ingest_service.list_batches(db)]
    return {
        "items": items,
        "incomplete_count": sum(1 for item in items if item["status"] == "receiving"),
        "total": len(items),
    }


def batch_detail(db: Session, batch_id: int) -> dict:
    return {"batch": ingest_service.detail(db, batch_id)}


def deliver_segment(request, db: Session, batch_id: int, segment_key: str, payload) -> dict:
    batch, segment, outcome = ingest_service.deliver_segment(db, batch_id, segment_key, payload)
    record(
        request,
        "ingest_segment.deliver",
        "ingest_batch",
        batch.id,
        after={
            "segment": segment_key,
            "accepted": outcome["accepted_rows"],
            "rejected": outcome["rejected_rows"],
            "duplicate_delivery": outcome["duplicate_delivery"],
            "promoted_rows": outcome["promoted_rows"],
        },
    )
    return {
        "batch": ingest_service.to_dict(db, batch),
        "segment": ingest_service.segment_to_dict(batch, segment),
        "duplicate_delivery": outcome["duplicate_delivery"],
        "accepted_rows": outcome["accepted_rows"],
        "rejected_rows": outcome["rejected_rows"],
        "quarantined": [ingest_service.quarantine_to_dict(item) for item in outcome["quarantined"]],
        "promoted": outcome["promoted"],
        "promoted_rows": outcome["promoted_rows"],
        "message": outcome["message"],
    }


def replay_segment(request, db: Session, batch_id: int, segment_key: str) -> dict:
    """Simulate a gateway (re)delivery for the demo screen; same ingest path."""
    from src.services import ingest_sim

    batch = ingest_service.get_batch(db, batch_id)
    missing_before = [
        item.segment_key
        for item in batch.segments
        if item.status == IngestSegmentStatus.MISSING.value
    ]
    already_received = segment_key not in missing_before
    include_bad_rows = bool(missing_before) and segment_key == missing_before[0]
    payload = ingest_sim.build_replay(
        db, batch_id, segment_key, include_bad_rows=include_bad_rows
    )
    result = deliver_segment(request, db, batch_id, segment_key, payload)
    if already_received:
        result["message"] = f"段 {segment_key} 已到齐，本次重复投递未新增样本。"
    return result
