"""Gateway simulation helper for the demo intake screen.

Real gateways push rows to ``POST /ingest-batches/{id}/segments/{key}``. The demo
screen has no gateway, so the replay route generates the missing segment's rows
deterministically and then runs the exact same delivery path.
"""

from src.constants.enums import IngestSegmentStatus
from src.constants.error_codes import AppError, ErrorCode
from src.services import ingest_demo, ingest_service


class ReplayPayload:
    def __init__(self, rows: list[dict], note: str = "demo gateway replay") -> None:
        self.rows = rows
        self.note = note


def build_replay(db, batch_id: int, segment_key: str, include_bad_rows: bool = False) -> ReplayPayload:
    batch = ingest_service.get_batch(db, batch_id)
    segment = next((item for item in batch.segments if item.segment_key == segment_key), None)
    if segment is None:
        raise AppError(
            ErrorCode.SEGMENT_UNKNOWN,
            f"段 {segment_key} 不在批次 {batch.batch_key} 的段清单内",
            status_code=404,
        )
    if segment.status == IngestSegmentStatus.RECEIVED.value:
        return ReplayPayload(rows=[])
    rows = ingest_demo.demo_rows_for_segment(db, batch, segment, with_bad_rows=include_bad_rows)
    return ReplayPayload(rows=rows)
