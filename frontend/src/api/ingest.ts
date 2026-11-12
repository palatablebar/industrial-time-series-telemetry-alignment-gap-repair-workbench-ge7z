import http from './http'
import type { DeliveryResult, IngestBatchDetail, IngestBatchSummary } from '../types/ingest'

export async function fetchIngestBatches(): Promise<{
  items: IngestBatchSummary[]
  incomplete_count: number
  total: number
}> {
  const { data } = await http.get<{ items: IngestBatchSummary[]; incomplete_count: number; total: number }>(
    '/ingest-batches',
  )
  return data
}

export async function fetchIngestBatch(batchId: number): Promise<IngestBatchDetail> {
  const { data } = await http.get<IngestBatchDetail>(`/ingest-batches/${batchId}`)
  return data
}

/** Demo only: gateway stand-in that generates and pushes one missing segment. */
export async function simulateSegment(batchId: number, segmentKey: string): Promise<DeliveryResult> {
  const { data } = await http.post<DeliveryResult>(
    `/ingest-batches/${batchId}/segments/${encodeURIComponent(segmentKey)}/simulate`,
  )
  return data
}
