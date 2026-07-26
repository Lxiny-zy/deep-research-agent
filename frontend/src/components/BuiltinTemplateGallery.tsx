import type { CSSProperties } from 'react'
import { AppIcon, type AppIconName } from './AppIcon'
import type { WorkflowInfo, WorkflowStep } from '../types'

/**
 * 内置工作流模板陈列：只读模板卡（名称 / 描述 / default 徽章 / 迷你流程链）。
 * 流程链是稳定的内置定义（deep_research/workflows.py），此处按名硬编码展示；
 * steps 为可克隆进编排 Studio 的预填链——含运行时控制原语（compose/team_fanout）
 * 的模板无法用自定义步骤表达，steps 为 null，克隆时降级为打开空 Studio。
 */

export type TemplateClone = {
  name: string
  title: string
  description: string
  steps: WorkflowStep[] | null
}

type ChainNode = { label: string; icon: AppIconName; loop?: boolean }

type TemplateMeta = {
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
    chain: [
      PLAN,
      { label: '多团队并行', icon: 'branch' },
      { label: '归并报告', icon: 'merge' },
    ],
    steps: null,
  },
}

const FALLBACK_META: TemplateMeta = { title: '', icon: 'workflow', chain: [], steps: null }

function staggerStyle(index: number): CSSProperties {
  return { '--stagger-i': index } as CSSProperties
}

interface Props {
  templates: WorkflowInfo[]
  onClone: (template: TemplateClone) => void
}

export default function BuiltinTemplateGallery({ templates, onClone }: Props) {
  const builtins = templates.filter((wf) => wf.custom !== 'True')
  if (!builtins.length) return null
  return (
    <section className="builtin-rail" aria-label="内置工作流模板">
      <div className="builtin-rail-head">
        <div>
          <span className="panel-kicker">
            <AppIcon name="stack" size={12} aria-hidden="true" /> BUILTIN / TEMPLATES
          </span>
          <h2 className="builtin-rail-title">内置工作流模板</h2>
        </div>
        <span className="builtin-rail-hint">
          {builtins.length} 条经过验证的多智能体流程：可在「新建研究」直接选用，或克隆为自定义起点。
        </span>
      </div>
      <div className="builtin-card-grid">
        {builtins.map((wf, index) => {
          const meta = BUILTIN_TEMPLATE_META[wf.name] ?? FALLBACK_META
          const title = meta.title || wf.name
          return (
            <article key={wf.name} className="builtin-card" style={staggerStyle(index)}>
              <div className="builtin-card-top">
                <span className="builtin-card-index">T-{String(index + 1).padStart(2, '0')}</span>
                <span className="builtin-card-badges">
                  {wf.default === 'True' && <span className="builtin-badge default">默认</span>}
                  {!meta.steps && <span className="builtin-badge dynamic">运行时编排</span>}
                </span>
              </div>
              <div className="builtin-card-title">
                <span className="builtin-card-glyph" aria-hidden="true">
                  <AppIcon name={meta.icon} size={19} />
                </span>
                <div className="builtin-card-name">
                  <strong>{title}</strong>
                  <code>{wf.name}</code>
                </div>
              </div>
              {wf.description && <p className="builtin-card-desc">{wf.description}</p>}
              {meta.chain.length > 0 && (
                <div className="builtin-chain" aria-label={`${title}流程链`}>
                  {meta.chain.map((node, i) => (
                    <span key={`${node.label}-${i}`} className="builtin-chain-piece">
                      {i > 0 && (
                        <AppIcon
                          name="chevron-right"
                          size={12}
                          className="builtin-chain-arrow"
                          aria-hidden="true"
                        />
                      )}
                      <span className={`builtin-chain-node${node.loop ? ' loop' : ''}`}>
                        <AppIcon name={node.icon} size={11} aria-hidden="true" />
                        {node.label}
                      </span>
                    </span>
                  ))}
                </div>
              )}
              <div className="builtin-card-foot">
                <button
                  type="button"
                  className="btn ghost small"
                  onClick={() =>
                    onClone({ name: wf.name, title, description: wf.description, steps: meta.steps })
                  }
                >
                  <AppIcon name="copy" size={13} aria-hidden="true" /> 克隆为自定义
                </button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
