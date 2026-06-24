import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createRun, listWorkflows } from '../api/client'
import SettingsPanel from '../components/SettingsPanel'
import type { ResearchParams, WorkflowInfo } from '../types'

const DEFAULT_QUERY = '2026 年主流 AI Agent 框架有哪些？各自的设计取舍是什么？'
const SAMPLES = [
  '对比 RAG 与长上下文：各自适用场景与成本取舍？',
  '2026 年向量数据库选型：Milvus / Qdrant / pgvector',
  'LLM 推理加速：vLLM、TensorRT-LLM、SGLang 怎么选？',
]

export default function NewResearchPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [query, setQuery] = useState(DEFAULT_QUERY)
  const [params, setParams] = useState<ResearchParams>({})
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([])
  const [workflow, setWorkflow] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 拉取可选研究流程；优先选中 URL ?workflow=（构建器「去研究」跳转），否则后端标记的 default
  useEffect(() => {
    const preferred = searchParams.get('workflow')
    listWorkflows()
      .then((ws) => {
        setWorkflows(ws)
        const match = preferred ? ws.find((w) => w.name === preferred) : undefined
        const def = match ?? ws.find((w) => w.default === 'True') ?? ws[0]
        if (def) setWorkflow(def.name)
      })
      .catch(() => {})
  }, [searchParams])

  async function start() {
    const q = query.trim()
    if (!q || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const hasParams = Object.values(params).some((v) => v != null)
      const { run_id } = await createRun({
        query: q,
        params: hasParams ? params : null,
        workflow: workflow || null,
      })
      navigate(`/runs/${run_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败')
      setSubmitting(false)
    }
  }

  const activeWf = workflows.find((w) => w.name === workflow)

  return (
    <div className="stack">
      {/* Hero 区域 - 大块色域 */}
      <div className="panel hero-panel" style={{
        background: 'linear-gradient(135deg, var(--surface-1) 0%, var(--surface-2) 100%)',
        borderLeft: '4px solid var(--accent-primary)',
        position: 'relative'
      }}>
        <div className="geo-corner top-left"></div>
        <div className="geo-corner top-right"></div>
        <div style={{ position: 'relative', zIndex: 1 }}>
          <div className="badge info" style={{ marginBottom: '16px' }}>
            <span>Multi-Agent System</span>
          </div>
          <h2 style={{ fontSize: '2.5rem', marginBottom: '16px' }}>
            把一个问题，<span style={{ color: 'var(--accent-primary)' }}>研究透彻</span>
          </h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '1.1rem', maxWidth: '800px' }}>
            自动拆解子问题、并行检索网络、反思补洞，最终综合成结构化、带引用溯源的研究报告
          </p>
        </div>
        <div className="geo-corner bottom-left"></div>
        <div className="geo-corner bottom-right"></div>
      </div>

      {/* 主面板 */}
      <div className="panel fade-in" style={{ position: 'relative' }}>
        <div className="panel-header">
          <div className="panel-title">配置研究任务</div>
        </div>

        <div className="panel-body stack">
          {/* 研究问题 */}
          <div>
            <label className="field-label" htmlFor="query">
              研究问题
            </label>
            <textarea
              id="query"
              className="input textarea"
              rows={4}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入一个值得深挖的问题…"
              style={{ fontFamily: 'var(--font-sans)', fontSize: '1rem' }}
            />
          </div>

          {/* 快捷示例 */}
          <div>
            <div className="field-label" style={{ marginBottom: '12px' }}>快捷示例</div>
            <div className="sample-grid">
              {SAMPLES.map((s) => (
                <button
                  type="button"
                  key={s}
                  className="card"
                  onClick={() => setQuery(s)}
                  style={{
                    padding: '16px',
                    textAlign: 'left',
                    cursor: 'pointer',
                    fontSize: '0.9rem',
                    color: 'var(--text-secondary)'
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {/* 工作流选择 */}
          {workflows.length > 0 && (
            <div>
              <label className="field-label" htmlFor="workflow">
                研究流程
              </label>
              <select
                id="workflow"
                className="input"
                value={workflow}
                onChange={(e) => setWorkflow(e.target.value)}
                style={{ fontWeight: '600' }}
              >
                {workflows.map((w) => (
                  <option key={w.name} value={w.name}>
                    {w.name} {w.default === 'True' ? '（默认）' : ''}
                  </option>
                ))}
              </select>
              {activeWf && (
                <p style={{
                  marginTop: '8px',
                  color: 'var(--text-tertiary)',
                  fontSize: '0.85rem',
                  fontStyle: 'italic'
                }}>
                  {activeWf.description}
                </p>
              )}
            </div>
          )}

          {/* 高级参数 */}
          <SettingsPanel value={params} onChange={setParams} />

          {/* 提交区域 */}
          <div className="submit-panel">
            <div style={{ flex: 1 }}>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ display: 'inline-block', marginRight: '6px', verticalAlign: 'middle' }}>
                  <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" fill="none"/>
                  <path d="M8 5V9M8 11V11.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                提交后创建可持久化研究任务，全程实时推送，可在「研究历史」回放
              </p>
            </div>
            <button
              className="btn btn-primary btn-lg"
              onClick={start}
              disabled={submitting || !query.trim()}
              style={{ minWidth: '180px', display: 'flex', alignItems: 'center', gap: '8px', justifyContent: 'center' }}
            >
              {submitting ? (
                <>
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" className="spinner" style={{ animation: 'spin 1s linear infinite' }}>
                    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="10 30" fill="none"/>
                  </svg>
                  提交中…
                </>
              ) : (
                <>
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M2 8L14 8M14 8L9 3M14 8L9 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                  开始研究
                </>
              )}
            </button>
          </div>

          {error && (
            <div className="badge error" style={{ width: '100%', justifyContent: 'center', padding: '12px' }}>
              ✗ {error}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
