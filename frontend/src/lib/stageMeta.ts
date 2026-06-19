// Agent stage 的展示元数据集中映射：中文名 / 颜色（引用 index.css 变量，单一真相）/ 图标。
// 供 EventTimeline、DagView 等复用，避免散落的配色表。
import type { Stage } from '../types'

export interface StageMeta {
  label: string
  color: string
  icon: string
}

export const STAGE_META: Record<Stage, StageMeta> = {
  PLANNER: { label: '规划', color: 'var(--stage-planner)', icon: '◆' },
  RESEARCHER: { label: '检索', color: 'var(--stage-researcher)', icon: '⌕' },
  REFLECTOR: { label: '反思', color: 'var(--stage-reflector)', icon: '↻' },
  SYNTHESIZER: { label: '综合', color: 'var(--stage-synthesizer)', icon: '✎' },
  ORCHESTRATOR: { label: '编排', color: 'var(--stage-orchestrator)', icon: '✦' },
  COORDINATOR: { label: '协调', color: 'var(--stage-coordinator)', icon: '⚙' },
}
