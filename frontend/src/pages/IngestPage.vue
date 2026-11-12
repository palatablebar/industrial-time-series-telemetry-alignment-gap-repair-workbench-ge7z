<template>
  <div class="page fade-in">
    <PageHeader
      numeral="I"
      eyebrow="nightly hand-over · 夜间回传"
      title="夜间回传批次"
      subtitle="网关整包回传按段接收：段未到齐时已收样本只显示进度、不进入对齐输入；补齐后只接收缺段，重复投递不新增样本，坏行逐行隔离并保留原因。"
    >
      <template #actions>
        <span class="mono head-count">
          {{ store.items.length }} 批 · {{ store.incompleteCount }} 批未到齐
        </span>
      </template>
    </PageHeader>

    <ErrorNotice :error="store.error" />

    <div class="split split--wide-left">
      <aside>
        <div class="panel">
          <div class="panel__title">
            <h2>批次索引</h2>
            <span class="mono panel-note">newest first</span>
          </div>
          <button
            v-for="batch in store.items"
            :key="batch.id"
            class="batch-item"
            :class="{ 'batch-item--active': detail?.id === batch.id }"
            @click="openBatch(batch.id)"
          >
            <span class="batch-item__key mono">{{ batch.batch_key }}</span>
            <span class="batch-item__meta mono">{{ batch.source_code }} · {{ formatDateTime(batch.window_start) }}</span>
            <span class="batch-item__state" :class="`batch-item__state--${batch.status}`">
              <span class="mono">{{ batch.received_segments }}/{{ batch.expected_segments }}</span>
              {{ batch.status === 'complete' ? '段已齐' : '段未齐' }}
            </span>
            <span class="mono batch-item__rows">
              收 {{ batch.received_rows }} · 有效 {{ batch.accepted_rows }} · 隔离 {{ batch.rejected_rows }}
            </span>
          </button>
          <div v-if="store.items.length === 0 && !store.loading" class="empty-line">暂无回传批次。</div>
        </div>
      </aside>

      <section v-if="detail" class="fade-in">
        <div class="batch-head">
          <div>
            <div class="eyebrow">{{ detail.source_code }} · {{ detail.source_name }}</div>
            <h2 class="batch-head__key mono">{{ detail.batch_key }}</h2>
            <p class="batch-head__window mono">
              窗口 {{ formatDateTime(detail.window_start) }} → {{ formatDateTime(detail.window_end) }}
            </p>
          </div>
          <div class="batch-head__badge">
            <span class="state-pill" :class="`state-pill--${detail.status}`">
              {{ detail.status === 'complete' ? '段已到齐' : '等待缺段' }}
            </span>
            <span class="mono batch-head__promoted">
              {{ detail.promoted_rows }} 行已进入对齐输入
            </span>
          </div>
        </div>

        <div class="stat-grid progress-stats">
          <MetricStat label="收到段" :value="detail.received_segments" :digits="0" :unit="`/ ${detail.expected_segments}`" hint="received / expected" />
          <MetricStat label="收到样本" :value="detail.received_rows" :digits="0" unit="行" hint="含坏行" />
          <MetricStat label="有效样本" :value="detail.accepted_rows" :digits="0" unit="行" hint="已通过校验、暂存" />
          <MetricStat label="隔离坏行" :value="detail.rejected_rows" :digits="0" unit="行" hint="逐行保留原因" />
        </div>

        <div class="gate-card" :class="detail.gate.can_start ? 'gate-card--open' : 'gate-card--closed'">
          <div class="gate-card__head">
            <span class="mono gate-card__tag">
              GATE · {{ detail.gate.can_start ? 'OPEN' : 'CLOSED' }}
            </span>
            <span class="mono gate-card__reason">{{ detail.gate.reason }}</span>
          </div>
          <p class="gate-card__summary">{{ detail.gate.summary }}</p>
          <p class="gate-card__note">
            {{
              detail.gate.can_start
                ? '全部段已到齐或没有未完成批次，重叠窗口的对齐任务可以启动。'
                : '对齐引擎在本批补段期间不会读取这些已收样本；补齐后仅补投缺段，已收段无需重发。'
            }}
          </p>
          <div v-if="detail.related_jobs.length" class="gate-card__jobs">
            <span class="mono gate-card__jobs-label">重叠任务</span>
            <RouterLink
              v-for="job in detail.related_jobs"
              :key="job.id"
              :to="job.run_id ? `/runs/${job.run_id}` : '/jobs/new'"
              class="gate-card__job"
            >
              <span class="mono">#{{ job.id }}</span>
              <span class="gate-card__job-name">{{ job.name }}</span>
              <span class="mono gate-card__job-gate">{{ job.gate.can_start ? '可启动' : '被本批拦截' }}</span>
            </RouterLink>
          </div>
        </div>

        <div class="panel">
          <div class="panel__title">
            <h2>段进度</h2>
            <span class="mono panel-note">{{ detail.received_segments }}/{{ detail.expected_segments }} received</span>
          </div>
          <table class="table table--mono">
            <thead>
              <tr>
                <th>序</th>
                <th>段</th>
                <th>时间范围</th>
                <th>状态</th>
                <th>收到</th>
                <th>有效</th>
                <th>隔离</th>
                <th>到达时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="segment in detail.segments" :key="segment.key">
                <td>{{ segment.ordinal }}</td>
                <td>{{ segment.key }}</td>
                <td class="segment-window">{{ clock(segment.window_start) }} → {{ clock(segment.window_end) }}</td>
                <td>
                  <span class="seg-state" :class="`seg-state--${segment.status}`">
                    {{ segment.status === 'received' ? '已到' : '缺段' }}
                  </span>
                </td>
                <td>{{ segment.status === 'received' ? segment.received_rows : '—' }}</td>
                <td>{{ segment.status === 'received' ? segment.accepted_rows : '—' }}</td>
                <td>{{ segment.status === 'received' ? segment.rejected_rows : '—' }}</td>
                <td>{{ segment.received_at ? formatDateTime(segment.received_at) : '—' }}</td>
                <td class="segment-action">
                  <button
                    v-if="auth.canWrite"
                    class="btn btn-quiet"
                    :disabled="busySegment === segment.key"
                    :title="segment.status === 'received' ? '重复投递不会新增样本' : '模拟网关补投该段'"
                    @click="replay(segment.key)"
                  >
                    {{ segment.status === 'received' ? '重复投递' : '补投该段' }}
                  </button>
                  <span v-else class="mono segment-action__ro">只读</span>
                </td>
              </tr>
            </tbody>
          </table>
          <p class="hint mono">
            演示环境用「补投该段」模拟网关投递；真实网关调用
            <code>POST /ingest-batches/{{ detail.id }}/segments/{段标识}</code>。
          </p>
        </div>

        <div v-if="lastResult" class="delivery-result" :class="lastResult.duplicate_delivery ? 'delivery-result--dup' : ''">
          <span class="mono delivery-result__tag">{{ lastResult.duplicate_delivery ? 'DUPLICATE' : 'DELIVERED' }}</span>
          <span class="delivery-result__text">{{ lastResult.message }}</span>
          <span class="mono delivery-result__rows">
            +{{ lastResult.accepted_rows }} 有效 / {{ lastResult.rejected_rows }} 隔离
            <template v-if="lastResult.promoted"> · 已转正 {{ lastResult.promoted_rows }} 行</template>
          </span>
        </div>

        <div class="panel">
          <div class="panel__title">
            <h2>坏行隔离</h2>
            <span class="mono panel-note">{{ detail.quarantine.length }} rows kept with reason</span>
          </div>
          <div v-if="detail.quarantine_reasons.length" class="reason-row">
            <span v-for="reason in detail.quarantine_reasons" :key="reason.reason" class="reason-chip mono">
              {{ reason.label }} <span class="reason-chip__count">{{ reason.count }}</span>
            </span>
          </div>
          <table v-if="detail.quarantine.length" class="table table--mono quarantine-table">
            <thead>
              <tr>
                <th>段</th>
                <th>行号</th>
                <th>测点</th>
                <th>原因</th>
                <th>说明</th>
                <th>原始行</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in detail.quarantine" :key="row.id">
                <td>{{ row.segment_key }}</td>
                <td>{{ row.line_no || '—' }}</td>
                <td>{{ row.signal_code || '—' }}</td>
                <td><span class="reason-tag">{{ row.reason_label }}</span></td>
                <td class="reason-message">{{ row.message }}</td>
                <td class="raw-cell">
                  <button class="btn btn-quiet raw-toggle" @click="toggleRaw(row.id)">
                    {{ openRaw === row.id ? '收起' : '查看' }}
                  </button>
                  <pre v-if="openRaw === row.id" class="raw-json mono">{{ rawJson(row) }}</pre>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="empty-line">本批没有坏行；整包坏行的批次也不会整体失败，只会逐行进入隔离。</p>
        </div>
      </section>
      <div v-else class="loading mono">{{ store.loading ? '读取回传批次…' : '选择一个批次查看进度。' }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '../components/common/PageHeader.vue'
import ErrorNotice from '../components/common/ErrorNotice.vue'
import MetricStat from '../components/common/MetricStat.vue'
import { useAuthStore } from '../stores/authStore'
import { useIngestStore } from '../stores/ingestStore'
import { simulateSegment } from '../api/ingest'
import type { DeliveryResult, QuarantinedRow } from '../types/ingest'
import { formatDateTime } from '../utils/format'

const store = useIngestStore()
const auth = useAuthStore()
const route = useRoute()
const busySegment = ref('')
const lastResult = ref<DeliveryResult | null>(null)
const openRaw = ref<number | null>(null)

const detail = computed(() => store.detail)

function clock(value: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(11, 19)
}

function rawJson(row: QuarantinedRow): string {
  return JSON.stringify(row.raw, null, 2)
}

function toggleRaw(id: number) {
  openRaw.value = openRaw.value === id ? null : id
}

async function openBatch(batchId: number) {
  lastResult.value = null
  openRaw.value = null
  await store.select(batchId)
}

async function replay(segmentKey: string) {
  if (!detail.value) return
  busySegment.value = segmentKey
  try {
    lastResult.value = await simulateSegment(detail.value.id, segmentKey)
    await store.refresh()
    await store.select(detail.value.id)
  } catch (error) {
    store.error = (error as { message?: string }).message ?? '补投失败'
  } finally {
    busySegment.value = ''
  }
}

onMounted(async () => {
  await store.load()
  const requested = Number(route.query.batch)
  if (requested && (!detail.value || detail.value.id !== requested)) {
    await store.select(requested)
  }
})
</script>

<style scoped>
.head-count {
  font-size: 10.5px;
  color: var(--ink-soft);
}
.panel-note {
  font-size: 10px;
  color: var(--ink-faint);
}
.batch-item {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 3px 10px;
  width: 100%;
  text-align: left;
  background: transparent;
  border: 0;
  border-top: 1px solid var(--rule);
  padding: 11px 10px;
  transition: background 160ms var(--ease);
}
.batch-item:hover {
  background: oklch(0.985 0.012 88);
}
.batch-item--active {
  background: oklch(0.985 0.012 88);
  box-shadow: inset 3px 0 0 var(--ochre);
}
.batch-item__key {
  font-size: 11px;
  letter-spacing: 0.04em;
  color: var(--ink-green);
}
.batch-item__meta {
  grid-column: 1 / -1;
  font-size: 9.5px;
  color: var(--ink-faint);
}
.batch-item__rows {
  grid-column: 1 / -1;
  font-size: 9.5px;
  color: var(--ink-faint);
}
.batch-item__state {
  font-size: 10px;
  color: var(--ink-soft);
}
.batch-item__state--receiving {
  color: var(--alert);
}
.batch-item__state--complete {
  color: var(--ok);
}
.batch-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  border-bottom: 1px solid var(--rule-strong);
  padding-bottom: 14px;
}
.batch-head__key {
  font-size: 22px;
  margin-top: 4px;
  color: var(--ink-green);
}
.batch-head__window {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--ink-soft);
}
.batch-head__badge {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}
.state-pill {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  padding: 3px 9px;
  border: 1px solid currentColor;
}
.state-pill--receiving {
  color: var(--alert);
}
.state-pill--complete {
  color: var(--ok);
}
.batch-head__promoted {
  font-size: 10px;
  color: var(--ink-faint);
}
.progress-stats {
  margin: 18px 0 4px;
}
.gate-card {
  border-left: 3px solid var(--rule-strong);
  background: oklch(0.985 0.012 88);
  padding: 12px 16px;
  margin: 18px 0 6px;
}
.gate-card--closed {
  border-left-color: var(--alert);
  background: oklch(0.955 0.02 40 / 0.6);
}
.gate-card--open {
  border-left-color: var(--ok);
  background: oklch(0.975 0.02 150 / 0.45);
}
.gate-card__head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.gate-card__tag {
  font-size: 9.5px;
  letter-spacing: 0.16em;
  color: var(--ink-soft);
}
.gate-card__reason {
  font-size: 9.5px;
  color: var(--ink-faint);
}
.gate-card__summary {
  margin: 8px 0 4px;
  font-size: 12.5px;
}
.gate-card__note {
  margin: 0;
  font-size: 10.5px;
  color: var(--ink-soft);
}
.gate-card__jobs {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 10px;
  border-top: 1px dotted var(--rule-strong);
  padding-top: 8px;
}
.gate-card__jobs-label {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--ink-faint);
}
.gate-card__job {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 11px;
}
.gate-card__job:hover .gate-card__job-name {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.gate-card__job-name {
  flex: 1;
}
.gate-card__job-gate {
  font-size: 9.5px;
  color: var(--ink-soft);
}
.seg-state {
  font-size: 10px;
}
.seg-state--received {
  color: var(--ok);
}
.seg-state--missing {
  color: var(--alert);
}
.segment-window {
  white-space: nowrap;
  font-size: 11px;
}
.segment-action {
  text-align: right;
}
.segment-action__ro {
  font-size: 9.5px;
  color: var(--ink-faint);
}
.hint {
  margin: 10px 0 0;
  font-size: 10px;
  color: var(--ink-faint);
}
.hint code {
  font-family: var(--font-mono);
  color: var(--ink-soft);
}
.delivery-result {
  display: flex;
  align-items: baseline;
  gap: 12px;
  border-left: 3px solid var(--ok);
  background: oklch(0.975 0.02 150 / 0.45);
  padding: 9px 14px;
  margin: 16px 0 0;
  font-size: 11.5px;
}
.delivery-result--dup {
  border-left-color: var(--ochre);
  background: oklch(0.965 0.02 80 / 0.6);
}
.delivery-result__tag {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--ink-soft);
}
.delivery-result__text {
  flex: 1;
}
.delivery-result__rows {
  font-size: 10px;
  color: var(--ink-soft);
}
.reason-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}
.reason-chip {
  font-size: 10px;
  border: 1px solid var(--rule-strong);
  padding: 2px 8px;
  color: var(--ink-soft);
}
.reason-chip__count {
  color: var(--alert);
  margin-left: 4px;
}
.reason-tag {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--alert);
}
.reason-message {
  font-family: var(--font-serif);
  font-size: 11.5px;
  max-width: 34ch;
}
.raw-cell {
  min-width: 64px;
}
.raw-toggle {
  padding: 2px 6px;
  font-size: 10px;
}
.raw-json {
  margin: 6px 0 0;
  padding: 8px 10px;
  background: var(--paper-2);
  border-left: 2px solid var(--rule-strong);
  font-size: 10px;
  white-space: pre-wrap;
  word-break: break-all;
  max-width: 330px;
}
.empty-line {
  font-size: 11.5px;
  color: var(--ink-faint);
  padding: 8px 0;
}
.loading {
  color: var(--ink-faint);
  font-size: 11.5px;
  padding: 20px 0;
}
@media (max-width: 1080px) {
  .progress-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
