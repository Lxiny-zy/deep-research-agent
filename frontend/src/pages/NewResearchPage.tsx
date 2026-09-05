import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'
import ClarifyDialog from '../components/ClarifyDialog'
import SettingsPanel from '../components/SettingsPanel'
import { useRevealOnScroll } from '../hooks/useRevealOnScroll'
import { useConfig } from '../hooks/useConfig'
import { useResearchDraft } from '../hooks/useResearchDraft'
import { assessIntent, createRun, listWorkflows } from '../api/client'
import { advance, emptySlots, isSkip, type ClarifyState } from '../lib/clarification'
import { clearThread, loadThread } from '../lib/conversation'
import {
  BUILTIN_TEMPLATE_META,
  isDefaultWorkflow,
  isUserFacingWorkflow,
} from '../lib/workflowTemplates'
import type { ConversationTurn, WorkflowInfo } from '../types'

const DEFAULT_QUERY = '2026 年主流 AI Agent 框架有哪些？各自的设计取舍是什么？'
const SAMPLES = [
  '对比 RAG 与长上下文：各自适用场景与成本取舍？',
  '2026 年向量数据库选型：Milvus / Qdrant / pgvector',
  'LLM 推理加速：vLLM、TensorRT-LLM、SGLang 怎么选？',
]

export default function NewResearchPage() {
  const [searchParams] = useSearchParams()
  return <ResearchComposer key={searchParams.get('followup') === '1' ? 'followup' : 'new'} />
}

function ResearchComposer() {
  const navigate = useNavigate()
  const { data: config } = useConfig()
  const pageRef = useRef<HTMLDivElement>(null)
  useRevealOnScroll(pageRef)
  const [searchParams] = useSearchParams()
  const isFollowUp = searchParams.get('followup') === '1'
  const [thread, setThread] = useState<ConversationTurn[]>(() => loadThread())
  const [draftContext] = useState(() => (isFollowUp ? JSON.stringify(thread) : ''))
  const draft = useResearchDraft(isFollowUp ? '' : DEFAULT_QUERY, draftContext)
  const { query, params, workflow } = draft
  const draftRef = useRef(draft)
  draftRef.current = draft
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [phase, setPhase] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [clarify, setClarify] = useState<ClarifyState | null>(null)
  const busy = submitting || clarify !== null

  useEffect(() => {
    let stale = false
    const preferred = searchParams.get('workflow')
    listWorkflows()
      .then((items) => {
        if (stale) return
        const visible = items.filter(isUserFacingWorkflow)
        setWorkflows(visible)
        const selected =
          visible.find((item) => item.name === preferred) ??
          visible.find((item) => item.name === draftRef.current.workflow) ??
          visible.find(isDefaultWorkflow) ??
          visible[0]
        if (selected) draftRef.current.update({ workflow: selected.name }, false)
      })
      .catch(() => {})
    return () => {
      stale = true
    }
  }, [searchParams])

  function dropThread() {
    clearThread()
    setThread([])
    if (isFollowUp) {
      draft.discard()
      navigate('/', { replace: true })
    }
  }

  async function launch(finalQuery: string, clarified = false) {
    setPhase('正在创建研究任务')
    const hasParams = Object.values(params).some((item) => item != null)
    const { run_id } = await createRun({
      query: finalQuery,
      params: hasParams ? params : null,
      workflow: workflow || null,
      history: thread,
      clarified,
    })
    draft.discard()
    navigate('/runs/' + run_id)
  }

  async function step(state: ClarifyState, allowFallback = false) {
    setPhase('正在确认研究范围')
    // Only assessment failures fall back; failed creation must not trigger another request.
    const verdict = await assessIntent({
      query: state.query,
      answers: state.answers,
      round: state.round,
      history: thread,
    }).catch((cause: unknown) => {
      if (!allowFallback) throw cause
      return null
    })
    if (!verdict || verdict.ready) {
      await launch(verdict?.resolved_query || state.query, Boolean(verdict))
      return
    }
    setClarify({ ...state, question: verdict.question, options: verdict.options, gap: verdict.gap })
    setSubmitting(false)
  }

  function failed(cause: unknown) {
    setError(cause instanceof Error ? cause.message : '提交失败，请重试')
    setSubmitting(false)
  }

  async function start() {
    const value = query.trim()
    if (!value || busy) return
    setSubmitting(true)
    setError(null)
    try {
      await step(
        { query: value, round: 0, answers: emptySlots(), question: '', options: [], gap: 'none' },
        true,
      )
    } catch (cause) {
      failed(cause)
    }
  }

  async function answerClarification(answer: string) {
    if (!clarify || submitting) return
    if (isSkip(answer)) {
      await skipClarification()
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await step(advance(clarify, answer))
    } catch (cause) {
      failed(cause)
    }
  }

  async function skipClarification() {
    if (!clarify || submitting) return
    setSubmitting(true)
    setError(null)
    setPhase('正在确认研究范围')
    const verdict = await assessIntent({
      query: clarify.query,
      answers: clarify.answers,
      round: clarify.round,
      history: thread,
      skip: true,
    }).catch(() => null)
    try {
      await launch(verdict?.resolved_query || clarify.query, true)
    } catch (cause) {
      failed(cause)
    }
  }

  const activeWorkflow = workflows.find((item) => item.name === workflow)
  const draftLabel = {
    idle: '',
    restored: '已恢复上次草稿',
    saving: '正在保存草稿…',
    saved: '草稿已保存到此浏览器',
    unavailable: '草稿未能保存，请保持当前页面',
    cleared: '草稿已清除',
  }[draft.status]

  return (
    <div className="stack page-stack" ref={pageRef}>
      <header className="page-intro composer-intro page-intro-compact intro-unveil">
        <div>
          <span className="eyebrow">
            <AppIcon name="sparkles" size={14} aria-hidden="true" /> 研究工作台
          </span>
          <h1>
            把一个问题，<em>研究透彻。</em>
          </h1>
          <p>从一个值得深挖的问题开始。</p>
        </div>
        <div className="workspace-signature" aria-hidden="true">
          <AppIcon name="scan-search" size={34} strokeWidth={1.1} />
          <span>
            RESEARCH
            <br />
            STARTS HERE
          </span>
        </div>
      </header>
      <section className="panel research-composer" data-reveal="1">
        {draft.status !== 'idle' && (
          <div className={'draft-status ' + draft.status}>
            <span role="status">
              <AppIcon
                name={draft.status === 'unavailable' ? 'alert' : 'save'}
                size={14}
                aria-hidden="true"
              />
              {draftLabel}
            </span>
            <span className="draft-actions">
              {draft.canUndo && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={busy}
                  onClick={draft.restore}
                >
                  <AppIcon name="undo" size={14} aria-hidden="true" />
                  撤销清除
                </button>
              )}
              {query.trim() && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm icon-button"
                  disabled={busy}
                  title="清除草稿"
                  aria-label="清除草稿"
                  onClick={draft.clear}
                >
                  <AppIcon name="trash" size={14} aria-hidden="true" />
                </button>
              )}
              {draft.status === 'unavailable' && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={draft.retry}>
                  重试保存
                </button>
              )}
            </span>
          </div>
        )}
        <div className="panel-header">
          <div>
            <span className="panel-kicker">问题 / 01</span>
            <h2 className="panel-title">定义研究问题</h2>
          </div>
          <span className="panel-index">NEW / RESEARCH</span>
        </div>
        <div className="panel-body stack">
          <fieldset className="composer-workbench composer-fields" disabled={busy}>
            <div className="composer-main">
              {thread.length > 0 && (
                <div className="thread-context" data-testid="thread-context">
                  <div className="field-label thread-heading">
                    <span>
                      <AppIcon name="history" size={14} aria-hidden="true" /> 追问上下文（
                      {thread.length} 轮）
                    </span>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={dropThread}>
                      开始新话题
                    </button>
                  </div>
                  <ol className="thread-list">
                    {thread.map((turn, index) => (
                      <li key={index}>
                        <span className="thread-index">{index + 1}</span>
                        <span className="thread-query">{turn.query}</span>
                      </li>
                    ))}
                  </ol>
                  <p className="hint">本次提问会带上以上轮次。</p>
                </div>
              )}
              <label className="field-label research-query-label" htmlFor="query">
                研究问题
                <textarea
                  id="query"
                  className="input textarea research-query-input"
                  rows={4}
                  value={query}
                  onChange={(event) => draft.update({ query: event.target.value })}
                  placeholder={
                    thread.length ? '接着上文追问，例如「那第二个呢」…' : '输入一个值得深挖的问题…'
                  }
                />
              </label>
              <div className="sample-block">
                <div className="field-label sample-heading">
                  <span>研究灵感</span>
                  <AppIcon name="arrow-down" size={13} aria-hidden="true" />
                </div>
                <div className="sample-grid">
                  {SAMPLES.map((sample, index) => (
                    <button
                      type="button"
                      key={sample}
                      className="sample-card"
                      onClick={() => draft.update({ query: sample })}
                    >
                      <span className="sample-number">0{index + 1}</span>
                      <span>{sample}</span>
                      <AppIcon name="arrow-up-right" size={15} aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <aside className="composer-sidebar" aria-label="研究配置">
              <div className="composer-section-heading">
                <span className="panel-kicker">配置 / 02</span>
                <h3 className="composer-section-title">研究配置</h3>
              </div>
              {workflows.length > 0 && (
                <label className="field-label workflow-select-field" htmlFor="workflow">
                  研究流程
                  <span className="select-with-icon">
                    <AppIcon name="workflow" size={15} aria-hidden="true" />
                    <select
                      id="workflow"
                      className="input"
                      value={workflow}
                      onChange={(event) => draft.update({ workflow: event.target.value })}
                    >
                      {workflows.map((item) => (
                        <option key={item.name} value={item.name}>
                          {BUILTIN_TEMPLATE_META[item.name]?.title || item.name}
                          {isDefaultWorkflow(item) ? '（默认）' : ''}
                        </option>
                      ))}
                    </select>
                  </span>
                  {activeWorkflow && (
                    <span className="hint">
                      {BUILTIN_TEMPLATE_META[activeWorkflow.name]?.description ??
                        activeWorkflow.description}
                    </span>
                  )}
                </label>
              )}
              <SettingsPanel
                value={params}
                onChange={(next) => draft.update({ params: next })}
                globalRequireCorroboration={config?.require_corroboration ?? false}
              />
            </aside>
          </fieldset>
          <div className="composer-action-column">
            {clarify && (
              <>
                <ClarifyDialog
                  question={clarify.question}
                  options={clarify.options}
                  round={clarify.round}
                  busy={submitting}
                  onAnswer={answerClarification}
                  onSkip={skipClarification}
                />
                <button
                  className="btn btn-ghost btn-sm"
                  type="button"
                  disabled={submitting}
                  onClick={() => {
                    setClarify(null)
                    setError(null)
                    requestAnimationFrame(() => document.getElementById('query')?.focus())
                  }}
                >
                  <AppIcon name="edit" size={14} aria-hidden="true" />
                  修改问题
                </button>
              </>
            )}
            <div className="submit-panel research-submit-panel">
              <div className="submit-context" role="status">
                <AppIcon
                  name={submitting ? 'activity' : 'circle-dot-dashed'}
                  size={17}
                  aria-hidden="true"
                />
                <p>
                  {submitting ? phase : clarify ? '等待补充研究范围' : '准备好，探索下一个问题。'}
                </p>
              </div>
              <button
                className="btn btn-primary btn-lg submit-button"
                onClick={start}
                disabled={busy || !query.trim()}
                type="button"
              >
                <AppIcon
                  name={submitting ? 'loader' : 'arrow-right'}
                  size={17}
                  aria-hidden="true"
                  className={submitting ? 'spin' : ''}
                />
                {submitting ? '提交中…' : '开始研究'}
              </button>
            </div>
            {error && (
              <div className="badge error form-error" role="alert">
                <AppIcon name="circle-x" size={15} aria-hidden="true" />
                {error}。输入已保留，可重新提交。
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
