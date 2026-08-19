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
  icon: AppIconName
  chain: ChainNode[]
  steps: WorkflowStep[] | null
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
    icon: 'radar',
    chain: [PLAN, RESEARCH, REFLECT, SYNTH],
    steps: DEEP_STEPS,
  },
  quick: {
    title: '快速查询',
    icon: 'zap',
    chain: [PLAN, RESEARCH, SYNTH],
    steps: [
      { kind: 'agent', agent: 'planner' },
      { kind: 'agent', agent: 'researcher' },
      { kind: 'agent', agent: 'synthesizer' },
    ],
  },
  reviewed: {
    title: '深度复核',
    icon: 'shield',
    chain: [PLAN, RESEARCH, REFLECT, SYNTH, { label: '评审', icon: 'shield' }],
    steps: [...DEEP_STEPS, { kind: 'agent', agent: 'critic' }],
  },
  auto: {
    title: '自组合',
    icon: 'wand',
    chain: [
      { label: '协调者', icon: 'wand' },
      { label: '动态组队', icon: 'network' },
      { label: '报告', icon: 'file' },
    ],
    steps: null,
  },
  teams: {
    title: '多团队并行',
    icon: 'network',
    chain: [PLAN, { label: '多团队并行', icon: 'branch' }, { label: '归并报告', icon: 'merge' }],
    steps: null,
  },
}
