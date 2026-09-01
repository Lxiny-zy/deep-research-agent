import type { AppIconName } from '../components/AppIcon'
import type { WorkflowStep } from '../types'

export type TemplateClone = {
  name: string
  title: string
  description: string
  steps: WorkflowStep[] | null
}

type ChainNode = { label: string; icon: AppIconName; loop?: boolean }

export type TemplateMeta = {
  title: string
  /** Optional user-facing description. Keeps API/internal wording out of the UI. */
  description?: string
  icon: AppIconName
  chain: ChainNode[]
  steps: WorkflowStep[] | null
}

/**
 * Workflows that are meaningful choices for a user. Runtime implementation
 * details (guarded, auto, teams, etc.) stay available to the engine but are
 * intentionally not presented as standalone templates.
 */
export const USER_FACING_BUILTIN_NAMES = ['deep', 'quick', 'hsi_review'] as const

export function isUserFacingBuiltin(name: string): boolean {
  return (USER_FACING_BUILTIN_NAMES as readonly string[]).includes(name)
}

/**
 * 后端把布尔值序列化成 ``"True"``/``"False"``（Python ``str(bool)``），而新的
 * 适配器可能返回真正的布尔。两种形态在这个 UI 边界统一归一化。
 *
 * 注意 ``Boolean("False") === true``——直接判真值会把"否"读成"是"，所以必须
 * 显式比较字符串内容。
 */
function truthyFlag(value: unknown): boolean {
  return value === true || (typeof value === 'string' && value.trim().toLowerCase() === 'true')
}

/** Keep custom workflows visible while hiding internal builtin orchestration. */
export function isCustomWorkflow(item: { custom?: string | boolean }): boolean {
  return truthyFlag(item.custom)
}

/** 是否为后端标记的默认工作流。与 custom 同源，同样两种形态都要认。 */
export function isDefaultWorkflow(item: { default?: string | boolean }): boolean {
  return truthyFlag(item.default)
}

export function isUserFacingWorkflow(item: { name: string; custom?: string | boolean }): boolean {
  return isCustomWorkflow(item) || isUserFacingBuiltin(item.name)
}

const PLAN: ChainNode = { label: '规划', icon: 'route' }
const RESEARCH: ChainNode = { label: '研究', icon: 'search-code' }
const REFLECT: ChainNode = { label: '反思循环', icon: 'refresh', loop: true }
const SYNTH: ChainNode = { label: '综合', icon: 'file' }

const DEEP_STEPS: WorkflowStep[] = [
  { kind: 'agent', agent: 'planner' },
  { kind: 'agent', agent: 'researcher' },
  { kind: 'reflect_loop', reflector: 'reflector', researcher: 'researcher' },
  { kind: 'agent', agent: 'synthesizer' },
]

export const BUILTIN_TEMPLATE_META: Record<string, TemplateMeta> = {
  deep: {
    title: '深度研究',
    description: '规划、检索、反思补洞并综合成可复核报告。',
    icon: 'radar',
    chain: [PLAN, RESEARCH, REFLECT, SYNTH],
    steps: DEEP_STEPS,
  },
  quick: {
    title: '快速查询',
    description: '适合单点事实与轻量问题的快速检索与综合。',
    icon: 'zap',
    chain: [PLAN, RESEARCH, SYNTH],
    steps: [
      { kind: 'agent', agent: 'planner' },
      { kind: 'agent', agent: 'researcher' },
      { kind: 'agent', agent: 'synthesizer' },
    ],
  },
  hsi_review: {
    title: 'HSI 文献审查',
    description: '面向 AI4S / HSI 文献的证据审查与批判性复核。',
    icon: 'book',
    chain: [PLAN, RESEARCH, REFLECT, SYNTH, { label: '批判复核', icon: 'shield' }],
    steps: [...DEEP_STEPS, { kind: 'agent', agent: 'critic' }],
  },
}
