export interface NavItem {
  path: string
  label: string
  numeral: string
  hint: string
}

export const NAV_ITEMS: NavItem[] = [
  { path: '/ingest', label: '回传批次', numeral: 'I', hint: 'nightly hand-over' },
  { path: '/sources', label: '数据源与测点', numeral: 'II', hint: 'gateway ledger' },
  { path: '/jobs/new', label: '对齐任务配置', numeral: 'III', hint: 'grid alignment' },
  { path: '/gaps', label: '缺口事件', numeral: 'IV', hint: 'gap registry' },
  { path: '/reports', label: '质量报告', numeral: 'V', hint: 'quality ephemeris' },
]

export const FORMULA_TEXT =
  '质量分 = 100 × (0.5×覆盖率 + 0.3×(1−异常率) + 0.2×漂移分)；漂移分 = exp(−max|drift_ms|/1000)；异常率 = 对齐后 |稳健 z| > 3 的比例（z 由窗口稳健中位数 / MAD 估计）'
