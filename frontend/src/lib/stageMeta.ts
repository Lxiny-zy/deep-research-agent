// Agent stage 的展示元数据集中映射：中文名 / 颜色（引用 index.css 变量，单一真相）/ 图标。
// 供 EventTimeline、DagView 等复用，避免散落的配色表。
import type { Stage } from '../types'
import type { AppIconName } from '../components/AppIcon'

export interface StageMeta {
  label: string
  color: string
  icon: AppIconName
}

// Editorial is intentionally monochrome. Opacity provides the small amount of
// hierarchy needed by the timeline without introducing semantic accent hues.
const INK = 'var(--editorial-ink, #1C1C1C)'
const INK_80 = 'var(--editorial-ink-80, rgba(28, 28, 28, 0.8))'
const INK_60 = 'var(--editorial-ink-60, rgba(28, 28, 28, 0.6))'
const INK_40 = 'var(--editorial-ink-40, rgba(28, 28, 28, 0.4))'

export const STAGE_META: Record<string, StageMeta> = {
  INTENT: { label: '意图', color: INK_60, icon: 'target' },
  PLANNER: { label: '规划', color: INK_80, icon: 'route' },
  RESEARCHER: { label: '检索', color: INK_60, icon: 'search-code' },
  REFLECTOR: { label: '反思', color: INK_60, icon: 'refresh' },
  SYNTHESIZER: { label: '综合', color: INK, icon: 'file' },
  ORCHESTRATOR: { label: '编排', color: INK_40, icon: 'workflow' },
  COORDINATOR: { label: '协调', color: INK_60, icon: 'waypoints' },
}

const FALLBACK_META: StageMeta = {
  label: '执行',
  color: INK_40,
  icon: 'activity',
}

export function getStageMeta(stage: Stage): StageMeta {
  return STAGE_META[stage] ?? { ...FALLBACK_META, label: stage || FALLBACK_META.label }
}
