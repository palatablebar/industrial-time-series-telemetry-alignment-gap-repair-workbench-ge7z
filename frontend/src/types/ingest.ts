export interface MissingSegment {
  key: string
  ordinal: number
  window_start: string | null
  window_end: string | null
  expected_rows: number
}

export interface BlockingBatch {
  id: number
  batch_key: string
  source_code: string
  status: string
  received_segments: number
  expected_segments: number
  received_rows: number
  accepted_rows: number
  rejected_rows: number
  missing_segments: MissingSegment[]
  reason: string
  summary: string
}

export interface IngestGate {
  can_start: boolean
  reason: string
  summary: string
  checked_batch_count: number
  blocking_batches: BlockingBatch[]
}

export interface RelatedJob {
  id: number
  name: string
  status: string
  window_start: string
  window_end: string
  grid_s: number
  run_id: number | null
  gate: IngestGate
}

export interface IngestBatchSummary {
  id: number
  batch_key: string
  source_id: number
  source_code: string
  source_name: string
  window_start: string
  window_end: string
  status: 'receiving' | 'complete'
  expected_segments: number
  received_segments: number
  expected_rows: number
  received_rows: number
  accepted_rows: number
  rejected_rows: number
  promoted_rows: number
  missing_segments: MissingSegment[]
  created_at: string
  completed_at: string | null
  promoted_at: string | null
  note: string
}

export interface IngestSegment {
  key: string
  ordinal: number
  status: 'missing' | 'received'
  window_start: string | null
  window_end: string | null
  expected_rows: number
  received_rows: number
  accepted_rows: number
  rejected_rows: number
  received_at: string | null
}

export interface QuarantinedRow {
  id: number
  segment_key: string
  line_no: number
  reason: string
  reason_label: string
  signal_code: string
  message: string
  raw: Record<string, unknown> | null
}

export interface IngestBatchDetail extends IngestBatchSummary {
  segments: IngestSegment[]
  quarantine: QuarantinedRow[]
  quarantine_reasons: Array<{ reason: string; label: string; count: number }>
  gate: IngestGate
  related_jobs: RelatedJob[]
}

export interface DeliveryResult {
  batch: IngestBatchSummary
  segment: IngestSegment
  duplicate_delivery: boolean
  accepted_rows: number
  rejected_rows: number
  quarantined: QuarantinedRow[]
  promoted: boolean
  promoted_rows: number
  message: string
}
