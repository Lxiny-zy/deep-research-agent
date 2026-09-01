import type { CSSProperties } from 'react'
import {
  BUILTIN_TEMPLATE_META,
  isCustomWorkflow,
  isDefaultWorkflow,
  isUserFacingBuiltin,
  type TemplateClone,
  type TemplateMeta,
} from '../lib/workflowTemplates'
import type { WorkflowInfo } from '../types'
import { AppIcon } from './AppIcon'

/**
 * 内置工作流模板陈列：只读公共模板卡（名称 / 描述 / default 徽章 / 迷你流程链）。
 * 公共流程链是稳定的内置定义（deep_research/workflows.py），此处按名硬编码展示；
 * runtime-only 控制原语（compose/team_fanout/全局门禁）不会伪装成可克隆模板。
 */

const FALLBACK_META: TemplateMeta = { title: '', icon: 'workflow', chain: [], steps: null }

function staggerStyle(index: number): CSSProperties {
  return { '--stagger-i': index } as CSSProperties
}

interface Props {
  templates: WorkflowInfo[]
  onClone: (template: TemplateClone) => void
}

export default function BuiltinTemplateGallery({ templates, onClone }: Props) {
  // Keep implementation-only orchestration (guarded/auto/teams/...) out of
  // the template gallery. Those capabilities are applied by the runtime
  // policy layer rather than selected as standalone user workflows.
  const builtins = templates.filter((wf) => !isCustomWorkflow(wf) && isUserFacingBuiltin(wf.name))
  if (!builtins.length) return null
  return (
    <section className="builtin-rail" aria-label="内置工作流模板">
      <div className="builtin-rail-head">
        <div>
          <span className="panel-kicker">
            <AppIcon name="stack" size={12} aria-hidden="true" /> 内置 / 模板
          </span>
          <h2 className="builtin-rail-title">内置工作流模板</h2>
        </div>
        <div className="builtin-rail-meta">
          <span className="builtin-rail-hint">
            {builtins.length}{' '}
            条经过验证的多智能体流程：可在「新建研究」直接选用，或克隆为自定义起点。
          </span>
          <span className="builtin-rail-policy" title="任务识别与高风险拒识由全局编排统一处理">
            <AppIcon name="shield" size={13} aria-hidden="true" /> 全局意图门禁
          </span>
        </div>
      </div>
      <div className="builtin-card-grid">
        {builtins.map((wf, index) => {
          const meta = BUILTIN_TEMPLATE_META[wf.name] ?? FALLBACK_META
          const title = meta.title || wf.name
          const description = meta.description || wf.description
          return (
            <article
              key={wf.name}
              className="builtin-card"
              data-workflow={wf.name}
              style={staggerStyle(index)}
            >
              <div className="builtin-card-top">
                <span className="builtin-card-index">
                  模板 {String(index + 1).padStart(2, '0')}
                </span>
                <span className="builtin-card-badges">
                  {isDefaultWorkflow(wf) && <span className="builtin-badge default">默认</span>}
                  {!meta.steps && <span className="builtin-badge dynamic">运行时编排</span>}
                </span>
              </div>
              <div className="builtin-card-title">
                <span className="builtin-card-glyph" aria-hidden="true">
                  <AppIcon name={meta.icon} size={19} />
                </span>
                <div className="builtin-card-name">
                  <strong>{title}</strong>
                  {/* Keep the stable identifier in the DOM for diagnostics and
                      tests, but do not make an implementation code part of
                      the Chinese product copy. */}
                  <code className="builtin-card-code" aria-hidden="true">
                    {wf.name}
                  </code>
                </div>
              </div>
              {description && <p className="builtin-card-desc">{description}</p>}
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
                    onClone({
                      name: wf.name,
                      title,
                      description,
                      steps: meta.steps,
                    })
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
