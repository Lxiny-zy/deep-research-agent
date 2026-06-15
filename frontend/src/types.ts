// 后端数据契约的 TypeScript 映射（与 deep_research/models.py、observability.py、
// persistence/repository.py 对齐）。Event 命名为 ResearchEvent 以避开 DOM 全局 Event。

export type Stage = 'PLANNER' | 'RESEARCHER' | 'REFLECTOR' | 'SYNTHESIZER' | 'ORCHESTRATOR'

export type EventType =
  | 'start'
  | 'info'
  | 'finding'
  | 'round'
  | 'token'
  | 'report'
  | 'done'
  | 'error'

export type RunStatus = 'pending' | 'running' | 'done' | 'error'

export interface ResearchEvent {
  stage: Stage
  type: EventType
  message: string
  elapsed: number
  tokens?: number
  data?: Record<string, unknown> | null
}

export interface RunSummary {
  id: string
  query: string
  status: RunStatus
  created_at: string | null
  total_tokens: number
  elapsed: number
  tags: string[]
}

export interface SubQuestion {
  question: string
  rationale: string
  depends_on: number[]
}

export interface Finding {
  statement: string
  source_url: string
  confidence: number
}

export interface ResearchResult {
  sub_question: string
  findings: Finding[]
}

export interface Report {
  query: string
  markdown: string
  citations: string[]
}

export interface RunDetail extends RunSummary {
  interpretation: string
  sub_questions: SubQuestion[]
  results: ResearchResult[]
  report: Report | null
}

// GET /api/tags 行：标签 + 引用计数
export interface TagCount {
  tag: string
  count: number
}

// per-run 研究参数覆盖（前端 SettingsPanel → POST /api/runs）
export interface ResearchParams {
  max_sub_questions?: number
  max_rounds?: number
  max_concurrency?: number
  results_per_search?: number
}

export interface CreateRunRequest {
  query: string
  params?: ResearchParams | null
}

export interface CreateRunResponse {
  run_id: string
}

// done 事件的 data 负载
export interface RunStats {
  elapsed: number
  total_tokens: number
  sources: number
}

// info + ORCHESTRATOR 事件的 data.dag 负载（用于分层可视化）
export interface DagData {
  layers: number[][]
  deps: Record<string, number[]>
}

// 全局配置（GET /api/config 响应，密钥脱敏）
export interface ConfigView {
  llm_model: string
  llm_base_url: string | null
  llm_api_key_set: boolean
  llm_api_key_hint: string
  tavily_api_key_set: boolean
  tavily_api_key_hint: string
  max_sub_questions: number
  max_rounds: number
  max_concurrency: number
  results_per_search: number
  request_timeout: number
}

// 全局配置更新（PUT /api/config 请求，全部可选）
export interface ConfigUpdate {
  llm_model?: string
  llm_base_url?: string | null
  llm_api_key?: string
  tavily_api_key?: string
  max_sub_questions?: number
  max_rounds?: number
  max_concurrency?: number
  results_per_search?: number
  request_timeout?: number
}
