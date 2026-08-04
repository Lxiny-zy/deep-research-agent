import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'
import ClarifyDialog from '../components/ClarifyDialog'
import SettingsPanel from '../components/SettingsPanel'
import { BUILTIN_TEMPLATE_META } from '../components/BuiltinTemplateGallery'
import { useRevealOnScroll } from '../hooks/useRevealOnScroll'
import { useConfig } from '../hooks/useConfig'
import { assessIntent, createRun, listWorkflows } from '../api/client'
import { advance, emptySlots, isSkip, type ClarifyState } from '../lib/clarification'
import { clearThread, loadThread } from '../lib/conversation'
import type { ConversationTurn, ResearchParams, WorkflowInfo } from '../types'

const DEFAULT_QUERY = '2026 年主流 AI Agent 框架有哪些？各自的设计取舍是什么？'
const SAMPLES = [
  '对比 RAG 与长上下文：各自适用场景与成本取舍？',
  '2026 年向量数据库选型：Milvus / Qdrant / pgvector',
  'LLM 推理加速：vLLM、TensorRT-LLM、SGLang 怎么选？',
]

export default function NewResearchPage() {
  const navigate = useNavigate()
  const { data: config } = useConfig()
  const pageRef = useRef<HTMLDivElement>(null)
  useRevealOnScroll(pageRef)
  const [searchParams] = useSearchParams()
  const [query, setQuery] = useState(DEFAULT_QUERY)
  const [params, setParams] = useState<ResearchParams>({})
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([])
  const [workflow, setWorkflow] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 追问上下文来自 sessionStorage，在挂载时读一次即可：本页是唯一的写入方之一，
  // 而另一个写入方（运行详情页的「继续追问」）跳转过来时组件会重新挂载。
  const [thread, setThread] = useState<ConversationTurn[]>(() => loadThread())
  // 澄清循环的临时状态：非 null 表示正在等用户补充信息，研究尚未开始。
  // 刻意不落 sessionStorage——它是一次提问内部的过程，刷新页面就该重来。
  const [clarify, setClarify] = useState<ClarifyState | null>(null)

  useEffect(() => {
    // stale 标记：连续导航或组件卸载后，旧请求的迟到响应不得覆盖新选择。
    let stale = false
    const preferred = searchParams.get('workflow')
    listWorkflows()
      .then((items) => {
        if (stale) return
        setWorkflows(items)
        const match = preferred ? items.find((item) => item.name === preferred) : undefined
        const defaultWorkflow = match ?? items.find((item) => item.default === 'True') ?? items[0]
        if (defaultWorkflow) setWorkflow(defaultWorkflow.name)
      })
      .catch(() => {})
    return () => {
      stale = true
    }
  }, [searchParams])

  // 从运行详情页点「继续追问」过来时带 followup=1：此时把默认问题清空，
  // 否则用户会对着一段与上文无关的示例文案继续追问。
  useEffect(() => {
    if (searchParams.get('followup') === '1') setQuery('')
  }, [searchParams])

  function dropThread() {
    clearThread()
    setThread([])
  }

  /** 真正创建研究。到这一步意味着信息已经够了（或用户主动跳过了追问）。 */
  async function launch(finalQuery: string) {
    const hasParams = Object.values(params).some((item) => item != null)
    const { run_id } = await createRun({
      query: finalQuery,
      params: hasParams ? params : null,
      workflow: workflow || null,
      // 空数组也照常传：后端把它当作「无上下文」，与不传等价，
      // 省掉一个仅为省几字节而存在的条件分支。
      history: thread,
    })
    navigate(`/runs/${run_id}`)
  }

  /** 走一轮澄清判定：够了就建 run，不够就把追问渲染出来等用户作答。 */
  async function step(state: ClarifyState) {
    const verdict = await assessIntent({
      query: state.query,
      answers: state.answers,
      round: state.round,
      history: thread,
    })
    if (verdict.ready) {
      // blocked 的请求也走这里：它必须照常建 run，把「为什么被拒」
      // 留成审计记录——拒识是安全事件，不是可以澄清掉的产品交互。
      setClarify(null)
      await launch(verdict.resolved_query || state.query)
      return
    }
    setClarify({
      ...state,
      question: verdict.question,
      options: verdict.options,
      gap: verdict.gap,
    })
    // 追问期间必须解除提交锁：否则用户点了选项也没反应。
    setSubmitting(false)
  }

  async function start() {
    const value = query.trim()
    if (!value || submitting) return
    setSubmitting(true)
    setError(null)
    const initial: ClarifyState = {
      query: value,
      round: 0,
      answers: emptySlots(),
      question: '',
      options: [],
      gap: 'none',
    }
    try {
      await step(initial)
    } catch (cause) {
      // 澄清判定挂了（网络/限流/后端版本没有这个端点）不该挡住提问——
      // 它是增强而非必需。直接按老路建 run。
      try {
        setClarify(null)
        await launch(value)
      } catch (fallbackCause) {
        setError(fallbackCause instanceof Error ? fallbackCause.message : '提交失败')
        setSubmitting(false)
      }
      void cause
    }
  }

  /** 用户答了一轮：累积答案后再判一次。 */
  async function answerClarification(answer: string) {
    if (!clarify || submitting) return
    setSubmitting(true)
    setError(null)
    const next = isSkip(answer) ? null : advance(clarify, answer)
    try {
      if (next === null) {
        setClarify(null)
        await launch(clarify.query)
        return
      }
      await step(next)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '提交失败')
      setSubmitting(false)
    }
  }

  async function skipClarification() {
    if (!clarify) return
    setSubmitting(true)
    setError(null)
    try {
      setClarify(null)
      await launch(clarify.query)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '提交失败')
      setSubmitting(false)
    }
  }

  const activeWorkflow = workflows.find((item) => item.name === workflow)

  return (
    <div className="stack page-stack" ref={pageRef}>
      <header className="page-intro composer-intro page-intro-compact intro-unveil">
        <div>
          <span className="eyebrow"><AppIcon name="sparkles" size={14} aria-hidden="true" /> STUDIO / NEW RESEARCH</span>
          <h1>把一个问题，<em>研究透彻。</em></h1>
          <p>交给一组会规划、检索、反思和写作的 Agent。你提供问题，系统负责把证据组织成可复核的报告。</p>
        </div>
        <div className="intro-orbit" aria-hidden="true">
          <span className="intro-orbit-ring ring-one" />
          <span className="intro-orbit-ring ring-two" />
          <span className="intro-orbit-core"><AppIcon name="network" size={20} /></span>
        </div>
      </header>

      <section className="panel research-composer" data-reveal="1">
        <div className="panel-header">
          <div>
            <span className="panel-kicker">PROMPT / 01</span>
            <h2 className="panel-title">定义研究问题</h2>
          </div>
          <span className="panel-index">01 — 03</span>
        </div>

        <div className="panel-body stack">
          {thread.length > 0 && (
            <div className="thread-context" data-testid="thread-context">
              {/* 上下文必须可见且可清除：一段用户看不见的历史会悄悄改变本次判定，
                  「为什么它答的是另一个东西」将无从解释。 */}
              <div className="field-label thread-heading">
                <span>
                  <AppIcon name="history" size={14} aria-hidden="true" /> 追问上下文（{thread.length} 轮）
                </span>
                <button type="button" className="btn btn-ghost btn-sm" onClick={dropThread}>
                  开始新话题
                </button>
              </div>
              <ol className="thread-list">
                {thread.map((turn, index) => (
                  <li key={`${turn.query}-${index}`}>
                    <span className="thread-index">{index + 1}</span>
                    <span className="thread-query">{turn.query}</span>
                  </li>
                ))}
              </ol>
              <p className="hint">
                本次提问会带上以上轮次，「那第二个呢」这类追问会先被还原成完整问题再研究。
              </p>
            </div>
          )}

          <label className="field-label research-query-label" htmlFor="query">
            研究问题
            <textarea id="query" className="input textarea research-query-input" rows={4} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={thread.length > 0 ? '接着上文追问，例如「那第二个呢」…' : '输入一个值得深挖的问题…'} />
          </label>

          <div className="sample-block">
            <div className="field-label sample-heading"><span>快捷示例</span><span className="muted small">点击载入</span></div>
            <div className="sample-grid">
              {SAMPLES.map((sample, index) => (
                <button type="button" key={sample} className="sample-card" onClick={() => setQuery(sample)}>
                  <span className="sample-number">0{index + 1}</span>
                  <span>{sample}</span>
                  <AppIcon name="arrow-up-right" size={15} aria-hidden="true" />
                </button>
              ))}
            </div>
          </div>

          {workflows.length > 0 && (
            <label className="field-label workflow-select-field" htmlFor="workflow">
              研究流程
              <span className="select-with-icon">
                <AppIcon name="workflow" size={15} aria-hidden="true" />
                <select id="workflow" className="input" value={workflow} onChange={(event) => setWorkflow(event.target.value)}>
                  {workflows.map((item) => {
                    const title = BUILTIN_TEMPLATE_META[item.name]?.title
                    const label = title ? `${title} · ${item.name}` : item.name
                    return <option key={item.name} value={item.name}>{label}{item.default === 'True' ? '（默认）' : ''}</option>
                  })}
                </select>
              </span>
              {activeWorkflow && <span className="hint">{activeWorkflow.description}</span>}
            </label>
          )}

          <SettingsPanel
            value={params}
            onChange={setParams}
            globalRequireCorroboration={config?.require_corroboration ?? false}
          />

          {clarify && (
            <ClarifyDialog
              question={clarify.question}
              options={clarify.options}
              round={clarify.round}
              busy={submitting}
              onAnswer={answerClarification}
              onSkip={skipClarification}
            />
          )}

          <div className="submit-panel research-submit-panel">
            <div className="submit-context">
              <AppIcon name="help" size={17} aria-hidden="true" />
              <p>提交后创建可持久化研究任务，全程实时推送，可在「研究历史」回放。</p>
            </div>
            <button className="btn btn-primary btn-lg submit-button" onClick={start} disabled={submitting || !query.trim() || clarify !== null} type="button">
              <AppIcon name={submitting ? 'loader' : 'arrow-right'} size={17} aria-hidden="true" className={submitting ? 'spin' : ''} />
              {submitting ? '提交中…' : '开始研究'}
            </button>
          </div>

          {error && <div className="badge error form-error"><AppIcon name="circle-x" size={15} aria-hidden="true" />{error}</div>}
        </div>
      </section>
    </div>
  )
}
