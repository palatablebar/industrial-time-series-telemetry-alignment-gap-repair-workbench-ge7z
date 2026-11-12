"""Resumable gateway batch intake.

A nightly hand-over arrives as a *batch* that is split into ordered segments.
Rows are validated, then staged in ``ingest_sample``; nothing reaches
``raw_telemetry`` (and therefore nothing reaches the alignment engine) until
every segment of that batch has arrived. Missing segments can be delivered
later without re-sending what was already accepted, repeated deliveries of an
already-received segment are no-ops, and malformed rows are quarantined one by
one with a reason so a single bad line cannot fail the batch.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    QUARANTINE_LABELS,
    FailureReason,
    IngestBatchStatus,
    IngestSegmentStatus,
    QuarantineReason,
)
from src.constants.error_codes import AppError, ErrorCode
from src.models import (
    DataSource,
    IngestBatch,
    IngestQuarantine,
    IngestSample,
    IngestSegment,
    RawTelemetry,
    Signal,
    User,
)
from src.utils.logger import log_event
from src.utils.timeutil import (
    from_epoch,
    iso,
    now_local,
    parse_iso,
    to_epoch,
)

MAX_REASON_MESSAGE = 240


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _default_segment_key(ordinal: int) -> str:
    return f"S{ordinal:02d}"


def _normalise_plan(payload, window_start: datetime, window_end: datetime) -> list[dict]:
    """Build the ordered segment plan, either explicit or an even split."""
    plan: list[dict] = []
    explicit = list(payload.segments or [])
    if explicit:
        for ordinal, item in enumerate(explicit, start=1):
            if not item.key.strip():
                raise AppError(ErrorCode.VALIDATION_FAILED, "段标识不能为空", status_code=422)
            try:
                item_window_start = parse_iso(item.window_start) if item.window_start else None
                item_window_end = parse_iso(item.window_end) if item.window_end else None
            except (ValueError, TypeError) as exc:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED,
                    f"段 {item.key} 的时间窗格式不合法",
                    status_code=422,
                ) from exc
            if item_window_start and item_window_end and item_window_end <= item_window_start:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED,
                    f"段 {item.key} 的结束时间必须晚于开始时间",
                    status_code=422,
                )
            plan.append(
                {
                    "key": item.key.strip(),
                    "ordinal": ordinal,
                    "window_start": item_window_start,
                    "window_end": item_window_end,
                    "expected_rows": int(item.expected_rows or 0),
                }
            )
        keys = [item["key"] for item in plan]
        if len(set(keys)) != len(keys):
            raise AppError(ErrorCode.VALIDATION_FAILED, "段标识不能重复", status_code=422)
        return plan

    count = int(payload.segment_count or 0)
    if count <= 0:
        raise AppError(
            ErrorCode.VALIDATION_FAILED,
            "请提供段清单或段数（segment_count ≥ 1）",
            status_code=422,
        )
    span = max(1.0, to_epoch(window_end) - to_epoch(window_start))
    chunk = span / count
    for ordinal in range(1, count + 1):
        start = window_start + timedelta(seconds=(ordinal - 1) * chunk)
        end = window_end if ordinal == count else window_start + timedelta(seconds=ordinal * chunk)
        plan.append(
            {
                "key": _default_segment_key(ordinal),
                "ordinal": ordinal,
                "window_start": start.replace(microsecond=0),
                "window_end": end.replace(microsecond=0),
                "expected_rows": 0,
            }
        )
    return plan


def _source(db: Session, source_id: int) -> DataSource:
    source = db.get(DataSource, source_id)
    if source is None:
        raise AppError(ErrorCode.NOT_FOUND, "数据源不存在", status_code=404)
    return source


def _get_batch(db: Session, batch_id: int) -> IngestBatch:
    batch = db.get(IngestBatch, batch_id)
    if batch is None:
        raise AppError(ErrorCode.NOT_FOUND, "回传批次不存在", status_code=404)
    return batch


def _source_signals(db: Session, source_id: int) -> dict[str, Signal]:
    rows = db.scalars(select(Signal).where(Signal.source_id == source_id))
    return {signal.code: signal for signal in rows}


def _missing_segments(batch: IngestBatch) -> list[dict]:
    plan_windows = {
        item.get("key"): item for item in (batch.plan_json or []) if isinstance(item, dict)
    }
    missing = []
    for segment in batch.segments:
        if segment.status != IngestSegmentStatus.MISSING.value:
            continue
        planned = plan_windows.get(segment.segment_key) or {}
        missing.append(
            {
                "key": segment.segment_key,
                "ordinal": int(segment.ordinal or 0),
                "window_start": planned.get("window_start") or iso(batch.window_start),
                "window_end": planned.get("window_end") or iso(batch.window_end),
                "expected_rows": int(segment.expected_rows or 0),
            }
        )
    return missing


def _plan_windows(batch: IngestBatch, segment: IngestSegment) -> tuple[datetime, datetime]:
    for item in batch.plan_json or []:
        if not isinstance(item, dict) or item.get("key") != segment.segment_key:
            continue
        start, end = batch.window_start, batch.window_end
        try:
            if item.get("window_start"):
                start = parse_iso(item["window_start"])
            if item.get("window_end"):
                end = parse_iso(item["window_end"])
        except (TypeError, ValueError):
            return batch.window_start, batch.window_end
        return start, end
    return batch.window_start, batch.window_end


def segment_window_start(batch: IngestBatch, segment: IngestSegment) -> datetime:
    return _plan_windows(batch, segment)[0]


def segment_window_end(batch: IngestBatch, segment: IngestSegment) -> datetime:
    return _plan_windows(batch, segment)[1]


def _refresh_counters(db: Session, batch: IngestBatch) -> None:
    segments = list(
        db.scalars(select(IngestSegment).where(IngestSegment.batch_id == batch.id).order_by(IngestSegment.ordinal))
    )
    batch.expected_segments = len(segments)
    batch.received_segments = sum(1 for item in segments if item.status == IngestSegmentStatus.RECEIVED.value)
    batch.received_rows = sum(int(item.received_rows or 0) for item in segments)
    batch.accepted_rows = sum(int(item.accepted_rows or 0) for item in segments)
    batch.rejected_rows = sum(int(item.rejected_rows or 0) for item in segments)
    batch.expected_rows = sum(int(item.expected_rows or 0) for item in segments)
    if batch.received_segments >= batch.expected_segments and batch.expected_segments > 0:
        if batch.status != IngestBatchStatus.COMPLETE.value:
            batch.status = IngestBatchStatus.COMPLETE.value
            batch.completed_at = now_local()
    else:
        batch.status = IngestBatchStatus.RECEIVING.value
        batch.completed_at = None


# --------------------------------------------------------------------------
# create / deliver
# --------------------------------------------------------------------------
def create_batch(db: Session, payload, user: User) -> IngestBatch:
    source = _source(db, payload.source_id)
    try:
        window_start = parse_iso(payload.window_start)
        window_end = parse_iso(payload.window_end)
    except (ValueError, TypeError) as exc:
        raise AppError(ErrorCode.VALIDATION_FAILED, "时间窗格式不合法", status_code=422) from exc
    if window_end <= window_start:
        raise AppError(ErrorCode.VALIDATION_FAILED, "批次结束时间必须晚于开始时间", status_code=422)

    plan = _normalise_plan(payload, window_start, window_end)
    key = (payload.batch_key or "").strip() or (
        f"{source.code}-{window_start.strftime('%Y%m%d-%H%M%S')}"
    )
    existing = db.scalar(select(IngestBatch).where(IngestBatch.batch_key == key))
    if existing is not None:
        raise AppError(ErrorCode.CONFLICT, "同批次标识已存在，请更换或续传原批次", status_code=409)

    batch = IngestBatch(
        batch_key=key,
        source_id=source.id,
        window_start=window_start,
        window_end=window_end,
        status=IngestBatchStatus.RECEIVING.value,
        expected_segments=len(plan),
        expected_rows=sum(int(item["expected_rows"] or 0) for item in plan),
        plan_json=[
            {
                "key": item["key"],
                "ordinal": item["ordinal"],
                "window_start": iso(item["window_start"]),
                "window_end": iso(item["window_end"]),
                "expected_rows": int(item["expected_rows"] or 0),
            }
            for item in plan
        ],
        note=payload.note or "",
        created_by=user.id,
    )
    db.add(batch)
    db.flush()
    for item in plan:
        db.add(
            IngestSegment(
                batch_id=batch.id,
                segment_key=item["key"],
                ordinal=item["ordinal"],
                status=IngestSegmentStatus.MISSING.value,
                expected_rows=int(item["expected_rows"] or 0),
            )
        )
    db.commit()
    db.refresh(batch)
    log_event(
        "INGEST_BATCH_CREATED",
        batch_id=batch.id,
        batch_key=batch.batch_key,
        source=source.code,
        segments=batch.expected_segments,
        rows=batch.expected_rows,
    )
    return batch


def _quarantine(
    db: Session,
    batch: IngestBatch,
    segment: IngestSegment,
    line_no: int,
    reason: str,
    row,
    message: str,
    signal_code: str = "",
) -> IngestQuarantine:
    item = IngestQuarantine(
        batch_id=batch.id,
        segment_id=segment.id,
        segment_key=segment.segment_key,
        line_no=line_no,
        reason=reason,
        signal_code=signal_code,
        message=message[:MAX_REASON_MESSAGE],
        raw_json=row if isinstance(row, dict) else {"raw": str(row)[:400]},
    )
    db.add(item)
    return item


def _parse_ts(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        candidate = float(value)
    else:
        text = str(value).strip()
        try:
            candidate = float(text)
        except ValueError:
            try:
                candidate = to_epoch(parse_iso(text))
            except (ValueError, TypeError):
                return None
    return candidate if math.isfinite(candidate) else None


def _parse_value(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return None
    return candidate if math.isfinite(candidate) else None


def deliver_segment(
    db: Session, batch_id: int, segment_key: str, payload
) -> tuple[IngestBatch, IngestSegment, dict]:
    batch = _get_batch(db, batch_id)
    segment = next(
        (item for item in batch.segments if item.segment_key == segment_key), None
    )
    if segment is None:
        raise AppError(
            ErrorCode.SEGMENT_UNKNOWN,
            f"段 {segment_key} 不在批次 {batch.batch_key} 的段清单内",
            status_code=404,
            detail={"expected_segments": [item.segment_key for item in batch.segments]},
        )

    started = datetime.now()
    if segment.status == IngestSegmentStatus.RECEIVED.value:
        # Idempotent re-delivery: never re-stage or duplicate samples.
        _refresh_counters(db, batch)
        db.commit()
        log_event(
            "INGEST_SEGMENT_DUPLICATE",
            batch_id=batch.id,
            segment=segment_key,
            accepted_rows=segment.accepted_rows,
        )
        return batch, segment, {
            "duplicate_delivery": True,
            "accepted_rows": 0,
            "rejected_rows": 0,
            "quarantined": [],
            "promoted": bool(batch.promoted_at),
            "promoted_rows": 0,
            "message": f"段 {segment_key} 已于此前入库，本次重复投递未新增样本。",
        }

    signal_by_code = _source_signals(db, batch.source_id)
    window_start = to_epoch(batch.window_start)
    window_end = to_epoch(batch.window_end)
    rows = payload.rows or []
    accepted = 0
    rejected = 0
    quarantined: list[IngestQuarantine] = []

    # timestamps already staged anywhere in this batch are duplicates too
    existing_staged = db.execute(
        select(IngestSample.signal_id, IngestSample.ts).where(IngestSample.batch_id == batch.id)
    ).all()
    seen = {(int(signal_id), round(float(ts), 6)) for signal_id, ts in existing_staged}

    for line_no, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            quarantined.append(
                _quarantine(db, batch, segment, line_no, QuarantineReason.INVALID_ROW.value, row, "整行不是 JSON 对象")
            )
            rejected += 1
            continue
        code = str(row.get("signal_code") or "").strip()
        if not code or "ts" not in row or "value" not in row:
            quarantined.append(
                _quarantine(
                    db, batch, segment, line_no, QuarantineReason.MISSING_FIELD.value, row,
                    "缺少 signal_code / ts / value", code,
                )
            )
            rejected += 1
            continue
        signal = signal_by_code.get(code)
        if signal is None:
            reason = (
                QuarantineReason.SIGNAL_SOURCE_MISMATCH.value
                if db.scalar(select(exists().where(Signal.code == code)))
                else QuarantineReason.UNKNOWN_SIGNAL.value
            )
            quarantined.append(
                _quarantine(db, batch, segment, line_no, reason, row, f"网关上没有测点 {code}", code)
            )
            rejected += 1
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            quarantined.append(
                _quarantine(
                    db, batch, segment, line_no, QuarantineReason.BAD_TIMESTAMP.value, row,
                    f"时间戳 {str(row.get('ts'))[:40]!r} 无法解析", code,
                )
            )
            rejected += 1
            continue
        value = _parse_value(row.get("value"))
        if value is None:
            quarantined.append(
                _quarantine(
                    db, batch, segment, line_no, QuarantineReason.BAD_VALUE.value, row,
                    f"数值 {str(row.get('value'))[:40]!r} 无法解析", code,
                )
            )
            rejected += 1
            continue
        if ts < window_start or ts > window_end:
            quarantined.append(
                _quarantine(
                    db, batch, segment, line_no, QuarantineReason.OUT_OF_WINDOW.value, row,
                    f"时间戳 {from_epoch(ts).isoformat()} 不在批次窗口内", code,
                )
            )
            rejected += 1
            continue
        key = (signal.id, round(ts, 6))
        if key in seen:
            quarantined.append(
                _quarantine(
                    db, batch, segment, line_no, QuarantineReason.DUPLICATE_TS.value, row,
                    "同测点同时间戳在本批次中已出现，保留先到的一行", code,
                )
            )
            rejected += 1
            continue
        seen.add(key)
        seq = row.get("seq", 0)
        try:
            seq_value = int(seq)
        except (TypeError, ValueError):
            seq_value = 0
        db.add(
            IngestSample(
                batch_id=batch.id,
                segment_id=segment.id,
                signal_id=signal.id,
                ts=float(ts),
                value=float(value),
                seq=seq_value,
            )
        )
        accepted += 1

    segment.status = IngestSegmentStatus.RECEIVED.value
    segment.received_rows = len(rows)
    segment.accepted_rows = accepted
    segment.rejected_rows = rejected
    segment.received_at = started
    db.flush()

    _refresh_counters(db, batch)
    promoted_rows = 0
    promoted = False
    if batch.status == IngestBatchStatus.COMPLETE.value and batch.promoted_at is None:
        promoted_rows, promoted = _promote(db, batch)
    db.commit()
    db.refresh(batch)
    db.refresh(segment)
    log_event(
        "INGEST_SEGMENT_RECEIVED",
        batch_id=batch.id,
        segment=segment_key,
        accepted=accepted,
        rejected=rejected,
        batch_status=batch.status,
        promoted_rows=promoted_rows,
    )
    message = (
        f"段 {segment_key} 入库 {accepted} 行、隔离 {rejected} 行；"
        + (f"批次已补齐，{promoted_rows} 行样本进入对齐输入。" if promoted else _missing_text(batch))
    )
    return batch, segment, {
        "duplicate_delivery": False,
        "accepted_rows": accepted,
        "rejected_rows": rejected,
        "quarantined": quarantined,
        "promoted": promoted,
        "promoted_rows": promoted_rows,
        "message": message,
    }


def _missing_text(batch: IngestBatch) -> str:
    missing = [item.segment_key for item in batch.segments if item.status == IngestSegmentStatus.MISSING.value]
    if not missing:
        return "段已全部到齐。"
    return f"仍缺段：{', '.join(missing)}；已收样本只计入进度，不参与对齐。"


def _promote(db: Session, batch: IngestBatch) -> tuple[int, bool]:
    """Move staged samples into raw_telemetry once the batch is complete."""
    staged = list(
        db.scalars(select(IngestSample).where(IngestSample.batch_id == batch.id).order_by(IngestSample.ts))
    )
    batch.promoted_at = now_local()
    if not staged:
        batch.promoted_rows = 0
        return 0, True
    signal_ids = {item.signal_id for item in staged}
    existing = db.execute(
        select(RawTelemetry.signal_id, RawTelemetry.ts).where(RawTelemetry.signal_id.in_(signal_ids))
    ).all()
    seen = {(int(signal_id), round(float(ts), 6)) for signal_id, ts in existing}
    segment_by_id = {item.id: item for item in batch.segments}
    duplicates_by_segment: dict[int, int] = {}
    promoted = 0
    duplicates = 0
    for sample in staged:
        key = (sample.signal_id, round(float(sample.ts), 6))
        if key in seen:
            duplicates += 1
            segment = segment_by_id.get(sample.segment_id)
            if segment is not None:
                duplicates_by_segment[segment.id] = duplicates_by_segment.get(segment.id, 0) + 1
                _quarantine(
                    db,
                    batch,
                    segment,
                    line_no=0,
                    reason=QuarantineReason.DUPLICATE_TS.value,
                    row={"signal_id": sample.signal_id, "ts": sample.ts, "value": sample.value},
                    message="补期时该测点时间戳已存在于原始遥测，未重复入库",
                )
            continue
        seen.add(key)
        db.add(
            RawTelemetry(
                signal_id=sample.signal_id,
                source_id=batch.source_id,
                ts=sample.ts,
                value=sample.value,
                seq=sample.seq,
            )
        )
        promoted += 1
    for segment_id, count in duplicates_by_segment.items():
        segment = segment_by_id[segment_id]
        segment.rejected_rows = int(segment.rejected_rows or 0) + count
        segment.accepted_rows = max(0, int(segment.accepted_rows or 0) - count)
    batch.promoted_rows = promoted
    db.flush()
    _refresh_counters(db, batch)
    return promoted, True


# --------------------------------------------------------------------------
# gate + projections
# --------------------------------------------------------------------------
def _gate_for_batch(batch: IngestBatch) -> dict:
    missing = _missing_segments(batch)
    if batch.status != IngestBatchStatus.RECEIVING.value:
        return {
            "can_start": True,
            "reason": "ok",
            "summary": f"批次 {batch.batch_key} 已收齐 {batch.received_segments}/{batch.expected_segments} 段，"
            f"{batch.accepted_rows} 行样本可参与对齐。",
            "batch": batch,
            "missing": [],
        }
    return {
        "can_start": False,
        "reason": FailureReason.BATCH_INCOMPLETE.value,
        "summary": f"批次 {batch.batch_key} 仅收到 {batch.received_segments}/{batch.expected_segments} 段，"
        f"{batch.accepted_rows} 行样本只计入进度；缺段 {', '.join(item['key'] for item in missing)}。",
        "batch": batch,
        "missing": missing,
    }


def evaluate_gate(
    db: Session,
    source_ids: list[int],
    window_start: datetime,
    window_end: datetime,
) -> dict:
    """Alignment may only start when no receiving batch overlaps the window."""
    checked = 0
    blocking: list[dict] = []
    if source_ids:
        batches = list(
            db.scalars(
                select(IngestBatch)
                .where(
                    IngestBatch.source_id.in_(source_ids),
                    IngestBatch.status == IngestBatchStatus.RECEIVING.value,
                    IngestBatch.window_start <= window_end,
                    IngestBatch.window_end >= window_start,
                )
                .order_by(IngestBatch.id)
            )
        )
        checked = len(batches)
        blocking = [_gate_for_batch(batch) for batch in batches]

    can_start = not blocking
    if can_start:
        summary = (
            "所选设备的回传批次段已全部到齐或没有未完成批次，可以启动对齐。"
            if checked
            else "所选设备关联的网关没有未完成的回传批次，可以启动对齐。"
        )
        return {
            "can_start": True,
            "reason": "ok",
            "summary": summary,
            "checked_batch_count": checked,
            "blocking_batches": [],
        }

    blocking_payload = []
    for item in blocking:
        batch = item["batch"]
        blocking_payload.append(
            {
                "id": batch.id,
                "batch_key": batch.batch_key,
                "source_code": batch.source.code if batch.source else "",
                "status": batch.status,
                "received_segments": batch.received_segments,
                "expected_segments": batch.expected_segments,
                "received_rows": batch.received_rows,
                "accepted_rows": batch.accepted_rows,
                "rejected_rows": batch.rejected_rows,
                "missing_segments": item["missing"],
                "reason": item["reason"],
                "summary": item["summary"],
            }
        )
    first = blocking_payload[0]
    summary = (
        f"对齐不可启动：{len(blocking)} 个未完成批次缺段。"
        f"批次 {first['batch_key']} 收到 {first['received_segments']}/{first['expected_segments']} 段，"
        f"已收 {first['received_rows']} 行（有效 {first['accepted_rows']} 行，隔离 {first['rejected_rows']} 行），"
        f"还缺 {', '.join(seg['key'] for seg in first['missing_segments'])}。"
        "段未到齐时已收样本只显示进度，不会进入对齐。"
    )
    return {
        "can_start": False,
        "reason": FailureReason.BATCH_INCOMPLETE.value,
        "summary": summary,
        "checked_batch_count": checked,
        "blocking_batches": blocking_payload,
    }


def gate_for_sources(db: Session, source_ids: list[int], window_start: datetime, window_end: datetime) -> dict:
    return evaluate_gate(db, source_ids, window_start, window_end)


def ensure_gate_open(
    db: Session, source_ids: list[int], window_start: datetime, window_end: datetime
) -> dict:
    gate = evaluate_gate(db, source_ids, window_start, window_end)
    if not gate["can_start"]:
        raise AppError(
            ErrorCode.BATCH_INCOMPLETE,
            gate["summary"],
            status_code=409,
            detail={
                "reason": gate["reason"],
                "blocking_batches": gate["blocking_batches"],
            },
        )
    return gate


def to_dict(db: Session, batch: IngestBatch) -> dict:
    source = batch.source
    return {
        "id": batch.id,
        "batch_key": batch.batch_key,
        "source_id": batch.source_id,
        "source_code": source.code if source else "",
        "source_name": source.name if source else "",
        "window_start": iso(batch.window_start),
        "window_end": iso(batch.window_end),
        "status": batch.status,
        "expected_segments": int(batch.expected_segments or 0),
        "received_segments": int(batch.received_segments or 0),
        "expected_rows": int(batch.expected_rows or 0),
        "received_rows": int(batch.received_rows or 0),
        "accepted_rows": int(batch.accepted_rows or 0),
        "rejected_rows": int(batch.rejected_rows or 0),
        "promoted_rows": int(batch.promoted_rows or 0),
        "missing_segments": _missing_segments(batch),
        "created_at": iso(batch.created_at),
        "completed_at": iso(batch.completed_at),
        "promoted_at": iso(batch.promoted_at),
        "note": batch.note or "",
    }


def list_batches(db: Session) -> list[IngestBatch]:
    return list(db.scalars(select(IngestBatch).order_by(IngestBatch.id.desc())))


def get_batch(db: Session, batch_id: int) -> IngestBatch:
    return _get_batch(db, batch_id)


def segment_to_dict(batch: IngestBatch, segment: IngestSegment) -> dict:
    return {
        "key": segment.segment_key,
        "ordinal": int(segment.ordinal or 0),
        "status": segment.status,
        "window_start": iso(segment_window_start(batch, segment)),
        "window_end": iso(segment_window_end(batch, segment)),
        "expected_rows": int(segment.expected_rows or 0),
        "received_rows": int(segment.received_rows or 0),
        "accepted_rows": int(segment.accepted_rows or 0),
        "rejected_rows": int(segment.rejected_rows or 0),
        "received_at": iso(segment.received_at),
    }


def quarantine_to_dict(item: IngestQuarantine) -> dict:
    return {
        "id": item.id,
        "segment_key": item.segment_key or "",
        "line_no": int(item.line_no or 0),
        "reason": item.reason,
        "reason_label": QUARANTINE_LABELS.get(item.reason, item.reason),
        "signal_code": item.signal_code or "",
        "message": item.message or "",
        "raw": item.raw_json,
    }


def quarantine_of(db: Session, batch_id: int) -> list[IngestQuarantine]:
    return list(
        db.scalars(
            select(IngestQuarantine)
            .where(IngestQuarantine.batch_id == batch_id)
            .order_by(IngestQuarantine.segment_id, IngestQuarantine.line_no, IngestQuarantine.id)
        )
    )


def detail(db: Session, batch_id: int) -> dict:
    batch = _get_batch(db, batch_id)
    gate = evaluate_gate(db, [batch.source_id], batch.window_start, batch.window_end)
    reasons: dict[str, int] = {}
    for item in quarantine_of(db, batch.id):
        reasons[item.reason] = reasons.get(item.reason, 0) + 1
    return {
        **to_dict(db, batch),
        "segments": [segment_to_dict(batch, item) for item in batch.segments],
        "quarantine": [quarantine_to_dict(item) for item in quarantine_of(db, batch.id)],
        "quarantine_reasons": [
            {"reason": key, "label": QUARANTINE_LABELS.get(key, key), "count": count}
            for key, count in sorted(reasons.items())
        ],
        "gate": gate,
        "related_jobs": _related_jobs(db, batch),
    }


def _related_jobs(db: Session, batch: IngestBatch) -> list[dict]:
    from src.models import AlignmentJob, AlignmentRun  # local import keeps the module graph acyclic

    source = batch.source
    if source is None:
        return []
    signals = list(db.scalars(select(Signal).where(Signal.source_id == source.id)))
    device_codes = sorted({signal.device_code for signal in signals})
    if not device_codes:
        return []
    jobs = list(
        db.scalars(
            select(AlignmentJob)
            .where(
                AlignmentJob.window_start <= batch.window_end,
                AlignmentJob.window_end >= batch.window_start,
            )
            .order_by(AlignmentJob.id.desc())
        )
    )
    payload = []
    for job in jobs:
        if not set(job.device_codes or []).intersection(device_codes):
            continue
        job_gate = evaluate_gate(db, [source.id], job.window_start, job.window_end)
        run = db.scalar(select(AlignmentRun).where(AlignmentRun.job_id == job.id))
        payload.append(
            {
                "id": job.id,
                "name": job.name,
                "status": job.status,
                "window_start": iso(job.window_start),
                "window_end": iso(job.window_end),
                "grid_s": job.grid_s,
                "run_id": run.id if run else None,
                "gate": job_gate,
            }
        )
    return payload
