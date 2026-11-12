"""Schemas for resumable gateway batch intake."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class SegmentPlanIn(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    window_start: str | None = None
    window_end: str | None = None
    expected_rows: int = Field(default=0, ge=0)


class IngestBatchCreate(BaseModel):
    source_id: int
    batch_key: str | None = Field(default=None, max_length=120)
    window_start: str
    window_end: str
    segments: list[SegmentPlanIn] = Field(default_factory=list)
    segment_count: int = Field(default=0, ge=0, le=48)
    note: str = ""


class IngestRow(BaseModel):
    signal_code: str | None = None
    ts: float | str | None = None
    value: float | str | None = None
    seq: int | None = 0


class SegmentDelivery(BaseModel):
    rows: list[Any] = Field(default_factory=list)
    note: str = ""


class MissingSegment(BaseModel):
    key: str
    ordinal: int
    window_start: str | None = None
    window_end: str | None = None
    expected_rows: int = 0


class SegmentOut(BaseModel):
    key: str
    ordinal: int
    status: Literal["missing", "received"]
    window_start: str | None = None
    window_end: str | None = None
    expected_rows: int
    received_rows: int
    accepted_rows: int
    rejected_rows: int
    received_at: str | None = None


class QuarantineOut(BaseModel):
    id: int
    segment_key: str
    line_no: int
    reason: str
    reason_label: str
    signal_code: str
    message: str
    raw: dict | None = None


class BlockingBatch(BaseModel):
    id: int
    batch_key: str
    source_code: str
    status: str
    received_segments: int
    expected_segments: int
    received_rows: int
    accepted_rows: int
    rejected_rows: int
    missing_segments: list[MissingSegment]
    reason: str
    summary: str


class IngestGate(BaseModel):
    can_start: bool
    reason: str
    summary: str
    checked_batch_count: int
    blocking_batches: list[BlockingBatch]


class RelatedJob(BaseModel):
    id: int
    name: str
    status: str
    window_start: str
    window_end: str
    grid_s: int
    gate: IngestGate


class IngestBatchOut(BaseModel):
    id: int
    batch_key: str
    source_id: int
    source_code: str
    source_name: str
    window_start: str
    window_end: str
    status: str
    expected_segments: int
    received_segments: int
    expected_rows: int
    received_rows: int
    accepted_rows: int
    rejected_rows: int
    promoted_rows: int
    missing_segments: list[MissingSegment]
    created_at: str
    completed_at: str | None = None
    promoted_at: str | None = None
    note: str = ""


class IngestBatchDetail(IngestBatchOut):
    segments: list[SegmentOut]
    quarantine: list[QuarantineOut]
    quarantine_reasons: list[dict]
    gate: IngestGate
    related_jobs: list[RelatedJob]


class IngestBatchList(BaseModel):
    items: list[IngestBatchOut]
    incomplete_count: int
    total: int


class DeliveryResult(BaseModel):
    batch: IngestBatchOut
    segment: SegmentOut
    duplicate_delivery: bool
    accepted_rows: int
    rejected_rows: int
    quarantined: list[QuarantineOut]
    promoted: bool
    promoted_rows: int
    message: str
