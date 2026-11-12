"""Domain enums shared by API services and the Vue front-end constants/enums.ts."""
from enum import StrEnum


class Role(StrEnum):
    ENGINEER = "engineer"
    ANALYST = "analyst"
    VIEWER = "viewer"


class SourceProtocol(StrEnum):
    OPCUA = "opcua"
    DCS = "dcs"
    CSV = "csv"


class GridSize(StrEnum):
    S1 = "1"
    S5 = "5"
    S60 = "60"

    @classmethod
    def values(cls) -> list[int]:
        return [1, 5, 60]


class AggStrategy(StrEnum):
    MEAN = "mean"
    LAST = "last"
    LINEAR = "linear"


class FillStrategy(StrEnum):
    LOCF = "locf"
    LINEAR = "linear"
    SEASONAL_NAIVE = "seasonal_naive"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GapSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IngestBatchStatus(StrEnum):
    RECEIVING = "receiving"
    COMPLETE = "complete"


class IngestSegmentStatus(StrEnum):
    MISSING = "missing"
    RECEIVED = "received"


class QuarantineReason(StrEnum):
    INVALID_ROW = "invalid_row"
    MISSING_FIELD = "missing_field"
    BAD_TIMESTAMP = "bad_timestamp"
    BAD_VALUE = "bad_value"
    UNKNOWN_SIGNAL = "unknown_signal"
    SIGNAL_SOURCE_MISMATCH = "signal_source_mismatch"
    OUT_OF_WINDOW = "out_of_window"
    DUPLICATE_TS = "duplicate_ts"


QUARANTINE_LABELS: dict[str, str] = {
    QuarantineReason.INVALID_ROW.value: "行不是 JSON 对象",
    QuarantineReason.MISSING_FIELD.value: "缺少必填字段",
    QuarantineReason.BAD_TIMESTAMP.value: "时间戳无法解析",
    QuarantineReason.BAD_VALUE.value: "数值无法解析",
    QuarantineReason.UNKNOWN_SIGNAL.value: "测点不在档案中",
    QuarantineReason.SIGNAL_SOURCE_MISMATCH.value: "测点不属于该网关",
    QuarantineReason.OUT_OF_WINDOW.value: "时间戳落在批次窗口外",
    QuarantineReason.DUPLICATE_TS.value: "同测点同时间戳重复",
}


class FailureReason(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    TIMESTAMP_DISORDER = "timestamp_disorder"
    NO_DATA = "no_data"
    BATCH_INCOMPLETE = "batch_incomplete"
    INTERNAL_ERROR = "internal_error"
