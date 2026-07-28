import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'
import SettingsPanel from '../components/SettingsPanel'
import { BUILTIN_TEMPLATE_META } from '../components/BuiltinTemplateGallery'
import { useRevealOnScroll } from '../hooks/useRevealOnScroll'
import { useConfig } from '../hooks/useConfig'
import { createRun, listWorkflows } from '../api/client'
import type { ResearchParams, WorkflowInfo } from '../types'

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

  async function start() {
    const value = query.trim()
    if (!value || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const hasParams = Object.values(params).some((item) => item != null)
      const { run_id } = await createRun({ query: value, params: hasParams ? params : null, workflow: workflow || null })
      navigate(`/runs/${run_id}`)
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
          <label className="field-label research-query-label" htmlFor="query">
            研究问题
            <textarea id="query" className="input textarea research-query-input" rows={4} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入一个值得深挖的问题…" />
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

          <div className="submit-panel research-submit-panel">
            <div className="submit-context">
              <AppIcon name="help" size={17} aria-hidden="true" />
              <p>提交后创建可持久化研究任务，全程实时推送，可在「研究历史」回放。</p>
            </div>
            <button className="btn btn-primary btn-lg submit-button" onClick={start} disabled={submitting || !query.trim()} type="button">
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
