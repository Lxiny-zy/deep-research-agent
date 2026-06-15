import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createRun } from '../api/client'
import SettingsPanel from '../components/SettingsPanel'
import type { ResearchParams } from '../types'

const DEFAULT_QUERY = '2026 年主流 AI Agent 框架有哪些？各自的设计取舍是什么？'
const SAMPLES = [
  '对比 RAG 与长上下文：各自适用场景与成本取舍？',
  '2026 年向量数据库选型：Milvus / Qdrant / pgvector',
  'LLM 推理加速：vLLM、TensorRT-LLM、SGLang 怎么选？',
]

export default function NewResearchPage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState(DEFAULT_QUERY)
  const [params, setParams] = useState<ResearchParams>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function start() {
    const q = query.trim()
    if (!q || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const hasParams = Object.values(params).some((v) => v != null)
      const { run_id } = await createRun({ query: q, params: hasParams ? params : null })
      navigate(`/runs/${run_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败')
      setSubmitting(false)
    }
  }

  return (
    <div className="stack">
      <section className="hero">
        <span className="eyebrow">✦ Multi-Agent Deep Research</span>
        <h2>
          把一个问题，<span className="accent">研究透彻</span>
        </h2>
        <p className="sub">
          自动拆解子问题、并行检索网络、反思补洞，最终综合成结构化、带 [n] 引用溯源的研究报告。
        </p>
      </section>

      <div className="panel fade-in">
        <label className="field-label" htmlFor="query">
          研究问题
        </label>
        <textarea
          id="query"
          className="input textarea"
          rows={3}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="输入一个值得深挖的问题…"
        />
        <div className="chips">
          {SAMPLES.map((s) => (
            <button type="button" key={s} className="chip" onClick={() => setQuery(s)}>
              {s}
            </button>
          ))}
        </div>
        <SettingsPanel value={params} onChange={setParams} />
        <div className="row between" style={{ marginTop: 18 }}>
          <span className="hint">提交后创建一次可持久化研究并跳转实时观看，全程可在「历史」回放。</span>
          <button
            className="btn lg"
            onClick={start}
            disabled={submitting || !query.trim()}
          >
            {submitting ? '提交中…' : '开始研究 →'}
          </button>
        </div>
        {error && <p className="error-text">✗ {error}</p>}
      </div>
    </div>
  )
}
