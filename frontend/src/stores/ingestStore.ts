import { defineStore } from 'pinia'
import { fetchIngestBatch, fetchIngestBatches } from '../api/ingest'
import type { IngestBatchDetail, IngestBatchSummary } from '../types/ingest'

interface IngestState {
  items: IngestBatchSummary[]
  incompleteCount: number
  detail: IngestBatchDetail | null
  loading: boolean
  error: string
}

export const useIngestStore = defineStore('ingest', {
  state: (): IngestState => ({
    items: [],
    incompleteCount: 0,
    detail: null,
    loading: false,
    error: '',
  }),
  getters: {
    current: (state): IngestBatchDetail | null => state.detail,
  },
  actions: {
    async load() {
      this.loading = true
      this.error = ''
      try {
        const result = await fetchIngestBatches()
        this.items = result.items
        this.incompleteCount = result.incomplete_count
        if (!this.detail && result.items.length > 0) {
          const preferred = result.items.find((item) => item.status === 'receiving') ?? result.items[0]
          await this.select(preferred.id)
        }
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '回传批次加载失败'
      } finally {
        this.loading = false
      }
    },
    async select(batchId: number) {
      try {
        this.detail = await fetchIngestBatch(batchId)
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '批次详情加载失败'
      }
    },
    async refresh() {
      if (this.detail) await this.select(this.detail.id)
      const result = await fetchIngestBatches().catch(() => null)
      if (result) {
        this.items = result.items
        this.incompleteCount = result.incomplete_count
      }
    },
  },
})
