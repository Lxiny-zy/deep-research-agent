// Agent stage 的展示元数据集中映射：中文名 / 颜色（引用 tokens.css 变量）/ 图标。
// 供 EventTimeline、DagView 等复用，避免散落的配色表。
import type { Stage } from '../types'
import type { AppIconName } from '../components/AppIcon'

export interface StageMeta {
  label: string
  color: string
  icon: AppIconName
}

// 阶段颜色取自设计令牌，浅色 / 深色主题下自动适配。
const PRIMARY = 'var(--primary)'
const INFO = 'var(--info)'
const SUCCESS = 'var(--success)'
const WARNING = 'var(--warning)'
const MUTED = 'var(--text-3)'

export const STAGE_META: Record<string, StageMeta> = {
  INTENT: { label: '意图', color: MUTED, icon: 'target' },
  PLANNER: { label: '规划', color: PRIMARY, icon: 'route' },
  RESEARCHER: { label: '检索', color: INFO, icon: 'search-code' },
  REFLECTOR: { label: '反思', color: WARNING, icon: 'refresh' },
  SYNTHESIZER: { label: '综合', color: SUCCESS, icon: 'file' },
  DELIVERY: { label: '交付', color: INFO, icon: 'file' },
  ORCHESTRATOR: { label: '编排', color: MUTED, icon: 'workflow' },
  COORDINATOR: { label: '协调', color: PRIMARY, icon: 'waypoints' },
}

const FALLBACK_META: StageMeta = {
  label: '执行',
  color: MUTED,
  icon: 'activity',
}

export function getStageMeta(stage: Stage): StageMeta {
  return STAGE_META[stage] ?? { ...FALLBACK_META, label: stage || FALLBACK_META.label }
}
