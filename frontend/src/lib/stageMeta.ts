// Agent stage 的展示元数据集中映射：中文名 / 颜色（引用 index.css 变量，单一真相）/ 图标。
// 供 EventTimeline、DagView 等复用，避免散落的配色表。
import type { Stage } from '../types'
import type { AppIconName } from '../components/AppIcon'

export interface StageMeta {
  label: string
  color: string
  icon: AppIconName
}

export const STAGE_META: Record<string, StageMeta> = {
  INTENT: { label: '意图', color: 'var(--stage-intent)', icon: 'target' },
  PLANNER: { label: '规划', color: 'var(--stage-planner)', icon: 'route' },
  RESEARCHER: { label: '检索', color: 'var(--stage-researcher)', icon: 'search-code' },
  REFLECTOR: { label: '反思', color: 'var(--stage-reflector)', icon: 'refresh' },
  SYNTHESIZER: { label: '综合', color: 'var(--stage-synthesizer)', icon: 'file' },
  ORCHESTRATOR: { label: '编排', color: 'var(--stage-orchestrator)', icon: 'workflow' },
  COORDINATOR: { label: '协调', color: 'var(--stage-coordinator)', icon: 'waypoints' },
}

const FALLBACK_META: StageMeta = {
  label: '执行',
  color: 'var(--stage-orchestrator)',
  icon: 'activity',
}

export function getStageMeta(stage: Stage): StageMeta {
  return STAGE_META[stage] ?? { ...FALLBACK_META, label: stage || FALLBACK_META.label }
}
