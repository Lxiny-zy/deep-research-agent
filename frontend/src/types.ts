// 后端数据契约的 TypeScript 映射（与 deep_research/models.py、observability.py、
// persistence/repository.py 对齐）。Event 命名为 ResearchEvent 以避开 DOM 全局 Event。

export type Stage = string

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
  tokens_estimated?: boolean
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
  evidence_quote: string
  confidence: number
  verification: {
    status: 'unverified' | 'verified'
    method: 'none' | 'normalized_quote'
    source_content_hash: string
    source_title?: string
    evidence_context?: string
    reason: string
    semantic_status: 'not_checked' | 'supported' | 'unsupported' | 'uncertain'
    semantic_confidence: number
    semantic_reason: string
    claim_id: string
    consistency_status: 'not_checked' | 'clear' | 'conflicted'
    contradicts_claim_ids: string[]
    contradiction_reason: string
    corroboration_status: 'not_checked' | 'single_source' | 'corroborated' | 'disputed'
    independent_source_count: number
    corroborates_claim_ids: string[]
    corroboration_reason: string
  }
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

export interface SourceSnapshot {
  title: string
  url: string
  content: string
  content_hash: string
}

export interface RunManifest {
  schema_version: number
  created_at: string
  workflow_name: string
  workflow_hash: string
  query_hash: string
  settings: Record<string, boolean | number | null>
  llm_model: string
  llm_endpoint: string
  search_backend: string
  catalog_snapshot_hash: string
  catalog_model_profiles: Record<string, unknown>[]
}

export interface QualityMetrics {
  total_findings: number
  verbatim_verified: number
  semantically_supported: number
  report_eligible: number
  corroborated: number
  conflicted: number
  disputed: number
  source_snapshots: number
  cited_sources: number
  cited_source_snapshot_coverage: number
  verified_finding_rate: number
  supported_finding_rate: number
  eligible_finding_rate: number
  independent_publishers: number
  blocked_sources: number
  total_tokens: number
  elapsed_seconds: number
}

export type IntentTier = 'rule' | 'model' | 'llm' | 'fallback'

export interface IntentSignal {
  tier: IntentTier
  code: string
  detail: string
}

export interface IntentSlots {
  entities: string[]
  time_range: string
  domain: string
  language: string
  aspects: string[]
}

export interface ClarificationRequest {
  question: string
  options: string[]
  reason: string
}

export interface IntentDecision {
  intent: string
  confidence: number
  tier: IntentTier
  risk: 'none' | 'prompt_injection' | 'system_prompt_probe' | 'off_task_instruction' | 'unsafe_content'
  risk_confidence: number
  signals: IntentSignal[]
  escalated: boolean
  scores: Record<string, number>
  reason: string
  slots: IntentSlots
  context_resolved: boolean
  resolved_query: string
  clarification: ClarificationRequest | null
}

export interface RunDetail extends RunSummary {
  interpretation: string
  sub_questions: SubQuestion[]
  results: ResearchResult[]
  report: Report | null
  orchestration: WorkflowRun | null
  sources: SourceSnapshot[]
  events: ResearchEvent[]
  manifest: RunManifest | null
  metrics: QualityMetrics | null
  intent: IntentDecision | null
}

export type WorkflowRunStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type StepRunStatus =
  | 'pending'
  | 'ready'
  | 'running'
  | 'retrying'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled'

export interface StepRun {
  id: string
  node_id: string
  label: string
  kind: string
  agent: string
  status: StepRunStatus
  attempt: number
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export interface WorkflowRun {
  id: string
  workflow_name: string
  status: WorkflowRunStatus
  input: Record<string, unknown>
  output: Record<string, unknown>
  definition?: Record<string, unknown>
  checkpoint?: Record<string, unknown>
  steps: StepRun[]
  started_at: string | null
  finished_at: string | null
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
  max_tokens?: number
  require_corroboration?: boolean
}

// GET /api/workflows 行：可选研究流程（default 为后端 str(bool)，"True"/"False"）
export interface WorkflowInfo {
  name: string
  description: string
  default: string
  custom?: string // "True"＝自定义工作流（构建器创建），"False"/缺省＝内置预置
}

// 自定义工作流的一个步骤（与后端 Step 对齐，顺序即数组序）
export interface WorkflowStep {
  kind: 'agent' | 'reflect_loop'
  agent?: string // kind=agent 时：角色名
  reflector?: string // kind=reflect_loop 时
  researcher?: string // kind=reflect_loop 时
  max_rounds?: number | null
  timeout_seconds?: number | null
  max_attempts?: number
  retry_backoff?: number
  failure_policy?: 'continue' | 'fail_fast'
  fallback_agent?: string | null
}

export interface WorkflowNode {
  id: string
  type: string
  position: { x: number; y: number }
  step: WorkflowStep
  join_mode?: 'any' | 'all' | 'success_all'
}

export interface WorkflowEdge {
  id: string
  source: string
  target: string
  condition?: string | null
}

export interface WorkflowViewport {
  x: number
  y: number
  zoom: number
  input_position?: { x: number; y: number }
  output_position?: { x: number; y: number }
}

// 自定义工作流（GET /api/workflows/custom）
export interface WorkflowDef {
  id: string
  name: string
  display_name: string
  description: string
  steps: WorkflowStep[]
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  viewport: WorkflowViewport
  version: number
  enabled: boolean
}

export interface WorkflowDefInput {
  name?: string
  display_name?: string
  description?: string
  steps?: WorkflowStep[]
  nodes?: WorkflowNode[]
  edges?: WorkflowEdge[]
  viewport?: WorkflowViewport
  version?: number
  enabled?: boolean
}

// GET /api/roles 行：可编排进自定义工作流的角色
export interface RoleInfo {
  name: string
  label: string
  description?: string
  icon: string
  builtin: boolean
  produces_report?: boolean
}

// 多轮追问的一轮历史。由**客户端**保管并随创建请求上传（见 lib/conversation.ts）：
// 服务端不持有会话状态，因此同样的请求体永远得到同样的判定。
export interface ConversationTurn {
  query: string
  intent: string
  slots: IntentSlots
}

export interface CreateRunRequest {
  query: string
  params?: ResearchParams | null
  workflow?: string | null
  history?: ConversationTurn[]
}

export interface CreateRunResponse {
  run_id: string
}

// POST /api/intent/assess —— 建 run 之前的「信息够不够」判定。
// 累积的答案由客户端携带（见 lib/clarification.ts），服务端不存会话。
export interface AssessRequest {
  query: string
  answers?: IntentSlots
  round?: number
  history?: ConversationTurn[]
}

export interface AssessResponse {
  ready: boolean
  resolved_query: string
  question: string
  options: string[]
  gap: string
  /** 安全拦截：前端照常建 run，让拒识留下审计痕迹 */
  blocked: boolean
  intent: string
  reason: string
}

// done 事件的 data 负载
export interface RunStats {
  elapsed: number
  total_tokens: number
  sources: number
  tokens_estimated?: boolean
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
  require_corroboration: boolean
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
  require_corroboration?: boolean
}

// ── 角色广场 catalog ──────────────────────────────────────────────────
// 角色行为模板（决定该角色在引擎里的执行逻辑）
export type Behavior = 'plan' | 'research' | 'reflect' | 'synthesize' | 'critique'

// 模型档案（GET /api/models，api_key 脱敏）
export interface ModelProfile {
  id: string
  name: string
  base_url: string | null
  model: string
  temperature: number
  parameter_mode: 'temperature' | 'reasoning'
  reasoning_effort: 'low' | 'medium' | 'high'
  is_default: boolean
  api_key_set: boolean
  api_key_hint: string
}

export interface ModelProfileInput {
  name?: string
  base_url?: string | null
  api_key?: string
  model?: string
  temperature?: number
  parameter_mode?: 'temperature' | 'reasoning'
  reasoning_effort?: 'low' | 'medium' | 'high'
  is_default?: boolean
}

// 角色卡片（GET /api/agents）
export interface AgentCard {
  id: string
  name: string
  display_name: string
  description: string
  behavior: Behavior
  system_prompt: string
  icon: string
  enabled: boolean
  model_profile_id: string | null
  model_profile_name: string | null
}

export interface AgentCardInput {
  name?: string
  display_name?: string
  description?: string
  behavior?: Behavior
  system_prompt?: string
  icon?: string
  enabled?: boolean
  model_profile_id?: string | null
}

// 搜索 key（GET /api/search-keys，api_key 脱敏）
export interface SearchKey {
  id: string
  label: string
  priority: number
  enabled: boolean
  api_key_hint: string
}

export interface SearchKeyInput {
  label?: string
  api_key?: string
  priority?: number
  enabled?: boolean
}

// 「测试连接」结果(POST /api/models|search-keys/{id}/test)
export interface TestResult {
  ok: boolean
  latency_ms: number
  detail: string
}

export interface ModelProbeInput {
  profile_id?: string | null
  base_url?: string | null
  api_key?: string
  model?: string
  parameter_mode?: 'temperature' | 'reasoning'
  reasoning_effort?: 'low' | 'medium' | 'high'
}

export interface ModelDiscoveryResult {
  models: string[]
  latency_ms: number
}
