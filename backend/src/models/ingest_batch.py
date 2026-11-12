from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config.database import Base


class IngestBatch(Base):
    """One scheduled gateway hand-over (a nightly package) split into segments.

    The batch is the unit the alignment gate reasons about: no segment may be
    missing before its rows are promoted to ``raw_telemetry``.
    """

    __tablename__ = "ingest_batch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"), nullable=False, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="receiving", index=True)
    expected_segments: Mapped[int] = mapped_column(Integer, default=0)
    received_segments: Mapped[int] = mapped_column(Integer, default=0)
    expected_rows: Mapped[int] = mapped_column(Integer, default=0)
    received_rows: Mapped[int] = mapped_column(Integer, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    promoted_rows: Mapped[int] = mapped_column(Integer, default=0)
    plan_json: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    source = relationship("DataSource", lazy="joined")
    segments = relationship(
        "IngestSegment",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="IngestSegment.ordinal",
    )


class IngestSegment(Base):
    """A resumable chunk of a batch; once delivered it is never re-applied."""

    __tablename__ = "ingest_segment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ingest_batch.id"), nullable=False, index=True)
    segment_key: Mapped[str] = mapped_column(String(80), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="missing", index=True)
    expected_rows: Mapped[int] = mapped_column(Integer, default=0)
    received_rows: Mapped[int] = mapped_column(Integer, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    batch = relationship("IngestBatch", back_populates="segments")


class IngestQuarantine(Base):
    """A rejected gateway row, kept verbatim with the reason it was rejected."""

    __tablename__ = "ingest_quarantine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ingest_batch.id"), nullable=False, index=True)
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_segment.id"), nullable=True)
    segment_key: Mapped[str] = mapped_column(String(80), default="")
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    signal_code: Mapped[str] = mapped_column(String(60), default="")
    message: Mapped[str] = mapped_column(String(240), default="")
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IngestSample(Base):
    """Accepted sample staged inside the batch until every segment has arrived.

    Rows only move to ``raw_telemetry`` when the batch becomes complete, which
    is what keeps an incomplete batch out of the alignment engine.
    """

    __tablename__ = "ingest_sample"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ingest_batch.id"), nullable=False, index=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("ingest_segment.id"), nullable=False, index=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signal.id"), nullable=False, index=True)
    ts: Mapped[float] = mapped_column(Float, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0)
