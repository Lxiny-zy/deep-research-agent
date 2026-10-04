import type { components } from './api/schema'

type Wire<Name extends keyof components['schemas']> = components['schemas'][Name]

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
  | 'needs_review'
  | 'error'
  | 'cancelled'

export type TerminalRunStatus = 'cancelled' | 'done' | 'error' | 'needs_review'
export type RunStatus = 'pending' | 'running' | 'cancelling' | TerminalRunStatus

export interface ResearchEvent {
  seq?: number | null
  attempt?: number
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

export type CancelRunResponse = Wire<'CancelRunResponse'>

export type SubQuestion = Required<Wire<'SubQuestion'>>

/**
 * 数值校验三态，与后端 ``EvidenceVerification.quantity_status`` 对齐。
 * ``not_applicable`` 表示该论断没有结构化数值，不是"没通过校验"。
 */
export type QuantityStatus = 'not_applicable' | 'verified' | 'unsupported'

export interface Finding {
  support_id?: string
  statement: string
  source_url: string
  evidence_quote: string
  confidence: number
  verification: {
    status: 'unverified' | 'verified'
    method: 'none' | 'normalized_quote'
    source_content_hash: string
    source_title?: string
    // 学术引用文本（作者/标题/期刊/年/DOI）。由后端在证据验证时刻渲染；
    // 通用网页来源为空串，历史 run 可能完全没有该字段，故为可选。
    source_reference?: string
    evidence_context?: string
    // Structured-document-only fields. Legacy RunDetail findings do not carry
    // them, so they remain optional at this compatibility boundary.
    quantity_label?: string
    conditions_label?: string
    quantity_status?: QuantityStatus
    quantity_reason?: string
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

export type Report = Required<Wire<'Report'>>

/** Structured report wire contract returned by GET /api/runs/{id}/document. */
export interface ReportDocument {
  bibliography?: ReportBibliography | null
  final_validation?: Wire<'FinalReportValidation'> | null
  schema_version: number
  query: string
  title?: string
  abstract?: string
  keywords?: string[]
  authors?: string[]
  institution?: string
  blocks: ReportBlock[]
  sections?: PaperSection[]
  references: ReportReference[]
  evidence: ReportEvidence[]
  overview: ReportOverview
  disclaimer: string
}

export interface ReportBibliography {
  source_body: string
  body: string
  binding_status?: 'unavailable' | 'bound' | 'invalid'
  occurrences?: CitationOccurrence[]
  cited_documents?: number[] | null
  documents: {
    index: number
    identity: string
    title: string
    reference: string
    url: string
    locations: number[]
  }[]
  locations: {
    index: number
    document: number
    url: string
    label: string
    content_hashes: string[]
  }[]
}

export interface CitationOccurrence {
  id: string
  run: number
  document: number
  locations: number[]
  unit_id: string
  scope: 'reviewed_unit' | 'source_location' | 'unused_location' | 'fulltext_review'
  evidence_ids: string[]
  review_note?: string
}

export type ReportBlock = ProseBlock | TableBlock | ChartBlock

export type PaperSectionKind =
  | 'abstract'
  | 'introduction'
  | 'related_work'
  | 'methods'
  | 'experiments'
  | 'results'
  | 'limitations'
  | 'conclusion'
  | 'data_availability'
  | 'ethics'
  | 'other'

export interface PaperSection {
  id: string
  title: string
  kind: PaperSectionKind
  level: 1 | 2 | 3
  blocks: ReportBlock[]
}

export interface ProseBlock {
  kind: 'prose'
  markdown: string
}

export interface TableColumn {
  key: string
  label: string
  unit: string
  align: 'left' | 'right'
  numeric: boolean
  note_ref: number | null
}

export interface TableCell {
  value: string
  numeric: number | null
  citations: number[]
  note_ref: number | null
  disputed: boolean
}

export interface TableRow {
  label: string
  citation: number | null
  cells: Record<string, TableCell>
}

export interface TableBlock {
  kind: 'table'
  id: string
  title: string
  columns: TableColumn[]
  rows: TableRow[]
  notes: string[]
  caption: string
}

export type ChartForm = 'bar' | 'dot' | 'grouped_bar' | 'scatter' | 'line'

export interface ChartBlock {
  kind: 'chart'
  id: string
  title: string
  form: ChartForm
  source_table: string
  value_columns: string[]
  x_column: string
  emphasis: string
  y_label: string
  caption: string
}

export interface ReportReference {
  index: number
  url: string
  reference: string
}

export interface ReportEvidence {
  support_id?: string
  citation: number
  claim_id: string
  statement: string
  quote: string
  context: string
  source_url: string
  reference: string
  source_section: string
  content_hash: string
  verbatim_verified: boolean
  verification_reason: string
  semantic_status: string
  semantic_confidence: number
  semantic_reason: string
  consistency_status: string
  contradicts_claim_ids: string[]
  contradiction_reason: string
  corroboration_status: string
  independent_source_count: number
  corroboration_reason: string
  quantity_label: string
  conditions_label: string
  quantity_status: string
  quantity_reason: string
}

export interface ReportOverview {
  records: number
  verbatim_matched: number
  semantically_supported: number
  corroborated: number
  conflicted: number
  blocked_sources: number | null
}

export interface SourceSnapshot {
  title: string
  url: string
  content: string
  content_hash: string
  locator?: string
}

export interface ResearchProject {
  id: string
  name: string
  description: string
  owner_id: string
  corpus_count: number
  source_count: number
  included_source_count: number
  created_at: string | null
  updated_at: string | null
}

export interface Corpus {
  id: string
  project_id: string
  name: string
  description: string
  source_count: number
  created_at: string | null
  updated_at: string | null
}

export type LibrarySourceKind = 'text' | 'markdown' | 'url' | 'doi' | 'pdf'
export type LibrarySourceStatus = 'included' | 'excluded'

export interface LibrarySource {
  id: string
  project_id: string
  corpus_id: string
  title: string
  kind: LibrarySourceKind
  status: LibrarySourceStatus
  origin_url: string
  mime_type: string
  content_hash: string
  char_count: number
  chunk_count: number
  metadata: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

export interface SourceChunk {
  id: string
  source_id: string
  ordinal: number
  content: string
  content_hash: string
  locator: string
  start_char: number
  end_char: number
  page_start: number | null
  page_end: number | null
  section: string
}

export interface ImportSourceInput {
  corpus_id: string
  title?: string
  kind: LibrarySourceKind
  text?: string
  data_base64?: string
  origin_url?: string
  mime_type?: string
}

export type RunManifest = Required<Wire<'RunManifest'>>

export type QualityMetrics = Required<Wire<'QualityMetrics'>>

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

/** Persisted routing policy included with newer intent decisions. */
export interface IntentExecutionPolicy {
  workflow?: string | null
  answer_mode?: string | null
  max_sub_questions?: number | null
  max_rounds?: number | null
  parallelism?: number | null
  requires_reflection?: boolean
  requires_corroboration?: boolean
  freshness?: string
  source_strategy?: string
  rationale?: string
}

export interface IntentDecision {
  intent: string
  confidence: number
  tier: IntentTier
  risk:
    | 'none'
    | 'prompt_injection'
    | 'system_prompt_probe'
    | 'off_task_instruction'
    | 'unsafe_content'
  risk_confidence: number
  signals: IntentSignal[]
  escalated: boolean
  scores: Record<string, number>
  reason: string
  slots: IntentSlots
  context_resolved: boolean
  resolved_query: string
  clarification: ClarificationRequest | null
  execution_policy?: IntentExecutionPolicy | null
}

export interface RunCompletion {
  status: 'done' | 'needs_review'
  issues: string[]
  advisories?: string[]
  input_version?: string
  content_version?: string
  required_formats?: string[]
  gates?: GateResult[]
}

export interface RunDetail extends RunSummary {
  cancel_requested_at?: string | null
  status_notice?: string | null
  completion?: RunCompletion | null
  project_id?: string | null
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
  // 恢复次数。后端 prepare_resume 在返回 202 之前就把它 +1，因此它是
  // 「新一次尝试已经开始」的权威信号——比 status 可靠：一次立刻再失败的
  // 恢复会让 status 停在 error，看不出与恢复前的区别。
  attempt?: number
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
  max_run_seconds?: number
  require_corroboration?: boolean
}

// GET /api/workflows 行：可选研究流程（default 为后端 str(bool)，"True"/"False"）
export interface WorkflowInfo {
  name: string
  description: string
  default: string | boolean // 兼容后端 "True"/"False" 与原生布尔值
  custom?: string | boolean // 兼容后端 "True"/"False" 与原生布尔值
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

export type CreateRunRequest = Wire<'CreateRunRequest'>

export type CreateRunResponse = Wire<'CreateRunResponse'>

// POST /api/intent/assess —— 建 run 之前的「信息够不够」判定。
// 累积的答案由客户端携带（见 lib/clarification.ts），服务端不存会话。
export type AssessRequest = Wire<'AssessRequest'>

export type AssessResponse = Required<Wire<'AssessResponse'>>

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
export type ConfigView = Omit<Wire<'ConfigView'>, 'access'> & {
  access?: { id: string; role: 'admin' | 'researcher' | 'reader' }
}

// 全局配置更新（PUT /api/config 请求，全部可选）
export type ConfigUpdate = Wire<'ConfigUpdate'>

/** 交付质量策略（值为数字或开关；字段集合与说明由 /api/config/quality-schema 给出） */
export type QualityPolicy = Record<string, number | boolean>

/** 交付质量设置的一个字段：标签、分组、取值范围、默认值与悬浮说明 */
export interface QualityField {
  key: string
  label: string
  group: string
  kind: 'int' | 'bool'
  help: string
  min: number | null
  max: number | null
  unit: string
  default: number | boolean
}

// ── 角色广场 catalog ──────────────────────────────────────────────────
// 角色行为模板（决定该角色在引擎里的执行逻辑）
export type Behavior = 'plan' | 'research' | 'reflect' | 'synthesize' | 'critique'

// 模型档案（GET /api/models，api_key 脱敏）
export interface ModelProfile {
  context_window_tokens?: number | null
  max_output_tokens?: number | null
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
  context_window_tokens?: number | null
  max_output_tokens?: number | null
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
  prompt_mode?: 'append' | 'replace'
  search_profile_ids?: string[] | null
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
  prompt_mode?: 'append' | 'replace'
  search_profile_ids?: string[] | null
  icon?: string
  enabled?: boolean
  model_profile_id?: string | null
}

// 搜索 key（GET /api/search-keys，api_key 脱敏）
export type SearchProvider =
  | 'tavily'
  | 'brave'
  | 'serper'
  | 'grok'
  | 'responses'
  | 'chat_search'
  | 'openalex'
  | 'arxiv'

export interface SearchProfile {
  id: string
  name: string
  provider: SearchProvider
  endpoint: string
  model: string
  key_ids: string[] | null
  enabled: boolean
  builtin: boolean
}

export type SearchProfileInput = Omit<SearchProfile, 'id' | 'builtin' | 'key_ids'> & {
  key_ids: string[]
}

export interface PromptPreview {
  default_prompt: string
  contract: string
  global_rules: string
  effective_system_prompt: string
}

export interface SearchKey {
  id: string
  provider: SearchProvider
  label: string
  priority: number
  enabled: boolean
  api_key_hint: string
}

export interface SearchKeyInput {
  provider?: SearchProvider
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
  context_window_tokens?: number | null
  max_output_tokens?: number | null
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
export interface ResourcePreflight {
  ok: boolean
  workflow: string
  roles: {
    role: string
    model: string
    model_profile: string
    inherits_search: boolean
    search_profiles: { id: string; name: string; ready: boolean; key_count: number }[]
  }[]
  errors: string[]
  warnings: string[]
}

export interface SearchResourceImpact {
  profiles: Record<string, string[]>
  keys: Record<string, string[]>
}

// ---- 科研工作台 ------------------------------------------------------------

export type TemplateKey =
  | 'autoResearch'
  | 'litReview'
  | 'peerReview'
  | 'paperRead'
  | 'dataAnalysis'
  | 'slides'
  | 'mindmap'

export type DeliverableFormat = 'md' | 'docx' | 'pdf' | 'html' | 'pptx' | 'mindmap' | 'xlsx' | 'zip'

export interface TemplateSection {
  key: string
  title: string
  guidance: string
  required: boolean
}

/** 检索策略：任务怎么找证据。深度检索是一种策略，不是独立的任务类型。 */
export type StrategyKey = 'none' | 'quick' | 'deep'

export interface TemplateStrategy {
  key: StrategyKey
  label: string
  description: string
  workflow: string
}

export interface TaskTemplate {
  key: TemplateKey
  title: string
  tagline: string
  description: string
  icon: string
  workflow: string
  strategies?: TemplateStrategy[]
  default_strategy?: StrategyKey
  input_kind: 'topic' | 'paper' | 'dataset'
  input_label: string
  input_placeholder: string
  sections: TemplateSection[]
  deliverables: DeliverableFormat[]
  examples: string[]
  accepts_attachments: boolean
  tier_default: 'light' | 'standard' | 'deep'
  min_citations: number
  tags: string[]
  /** Whether a selected private-library project is consumed by this template. */
  supports_library?: boolean
}

export interface PaperReference {
  kind: 'arxiv' | 'doi' | 'url'
  value: string
  url: string
}

export interface TaskContract {
  template: TemplateKey
  title: string
  original_request: string
  focus: string
  papers: PaperReference[]
  dataset_csv: string
  dataset_rows?: number
  /** 数据来源：文件名、工作表与服务端重算的行数、列类型 */
  dataset_source?: Partial<DatasetSourceInfo>
  /** 用户是否主动选择示例数据演示；旧任务为 null */
  demo_data?: boolean | null
  /** 预览：粘贴数据按分析规则解析后的概况，或无法分析的原因 */
  dataset_profile?: DatasetSheetProfile
  dataset_error?: string
  /** 论文类任务：没有论文链接时被当作论文正文的输入字数（0 表示输入太短） */
  pasted_paper_chars?: number
  required_sections: string[]
  deliverables: string[]
  constraints: string[]
  evidence_rules: string[]
  tier: string
  strategy?: string
  rendered?: string
}

export interface DatasetColumn {
  name: string
  type: '数值' | '文本' | '日期' | '布尔'
}

export interface DatasetSheetProfile {
  /** 工作表名；CSV / TSV 为空串 */
  name: string
  rows: number
  columns: DatasetColumn[]
  chars: number
}

export interface DatasetSheet extends DatasetSheetProfile {
  csv: string
}

/** POST /api/datasets：表格文件解析结果（不落盘） */
export interface DatasetParseResult {
  filename: string
  sheets: DatasetSheet[]
  skipped: { name: string; error: string }[]
}

export interface DatasetSourceInfo {
  filename: string
  sheet: string
  rows: number
  columns: DatasetColumn[]
}

export type GateStatus = 'pass' | 'warn' | 'fail'

export interface GateResult {
  name: string
  status: GateStatus
  issues: string[]
  metrics: Record<string, unknown>
  blocking_issues?: string[] | null
  advisories?: string[]
}

export interface DeliverableItem {
  name: string
  format: string
  title: string
  role: 'report' | 'reading' | 'source' | 'slides' | 'figure' | 'data'
  size: number
  sha256: string
  mime_type: string
  status: GateStatus
  issues: string[]
}

export interface DeliverableRegistry {
  content_revision?: { available: boolean; reason: string; source_version: string }
  content_version?: string
  input_version?: string
  parent_version?: string
  attempt?: number
  can_retry?: boolean
  failures?: { format: string; title: string; issues: string[]; retryable: boolean }[]
  version: number
  run_id: string
  template: TemplateKey
  title: string
  status: GateStatus
  generated_at: string
  primary: string | null
  items: DeliverableItem[]
  gates: GateResult[]
}

export interface RunTemplateInfo {
  revision_source?: { parent_run_id: string; source_version: string } | null
  template: TaskTemplate
  contract: TaskContract | null
  extras: { score?: number | null; stats?: Record<string, number>; figures?: number }
  analysis: {
    rows: number
    columns: string[]
    describe: Record<string, unknown>[]
    tests: Record<string, unknown>[]
    synthetic: boolean
  } | null
  intake: {
    /** 研究对象来源：论文链接 / 上传文件 / 粘贴文本 / 未提供 */
    mode?: 'papers' | 'attachments' | 'pasted' | 'missing'
    papers: PaperReference[]
    sections: { url: string; title: string; section: string; chars: number }[]
    failures: { url: string; error: string }[]
  } | null
}

// ---- 学术问答 --------------------------------------------------------------

export interface QaThought {
  tool: string
  input: string
  observation: string
  call_id?: string
  usage?: Record<string, unknown>
  binding?: ReportBibliography
  unresolved_topics?: string[]
}

export interface QaActivity {
  type: 'reasoning' | 'usage' | 'status' | 'cache' | 'reset'
  message?: string
  hit?: boolean
  call_id?: string
  model?: string
  reasoning_delta?: string
  llm_usage?: Record<string, unknown>
  replay?: boolean
}

export interface QaEvidence {
  support_id?: string
  statement: string
  source_url: string
  evidence_quote: string
  source_title?: string
  source_reference?: string
  /** 区分本论文、本次任务、资料库与联网来源。 */
  origin?: QaOrigin
}

export type QaOrigin = 'paper' | 'research' | 'library' | 'web'
export type QaSourceOption = 'web' | 'library'

export interface QaMessage {
  id: string
  position: number
  query: string
  answer: string
  citations: string[]
  evidence: QaEvidence[]
  thoughts: QaThought[]
  status: 'pending' | 'running' | 'done' | 'fallback' | 'error'
  created_at: string | null
  tokens?: number | null
  request_id?: string | null
  request_payload?: { query?: string; sources?: QaSourceOption[]; project_id?: string | null }
  error?: string | null
}

export interface QaConversation {
  id: string
  title: string
  created_at: string | null
  updated_at: string | null
  message_count: number
  messages: QaMessage[]
  /** 绑定到某次论文或研究任务的会话；普通问答为 null。 */
  run_id?: string | null
}

// ---- 论文精读工作区 --------------------------------------------------------

export interface ReaderDocument {
  id: string
  kind: 'attachment' | 'paper' | 'pasted'
  title: string
  url?: string
  /** 能否在右侧直接显示原版 PDF。 */
  pdf: boolean
  note: string
}

export interface RunReader {
  run_id: string
  status: RunStatus
  documents: ReaderDocument[]
  has_report: boolean
  can_ask?: boolean
  qa_scope?: 'paper' | 'research'
  source_count?: number
  query?: string
}

export interface NarrativeSection {
  key: string
  title: string
  status: 'pending' | 'active' | 'done' | 'needs_review' | 'error'
  lines: string[]
  first_seq: number | null
  last_seq: number | null
  elapsed: number
}

export interface RunNarrative {
  headline: string
  sections: NarrativeSection[]
  counters: Record<string, number>
  last_seq: number
}

// ---- 运行工作区（三栏详情页） ------------------------------------------------

export interface WorkspaceStep {
  index: number
  node_id: string
  label: string
  kind: string
  agent: string
  status: StepRunStatus
  attempt: number
  error: string | null
  started_at: string | null
  finished_at: string | null
  elapsed: number | null
}

export interface WorkspaceReplan {
  id: string
  target: string
  trigger: 'partial' | 'failed'
  action: 'rescue' | 'accept'
  name: string
  reason: string
  result: string
}

export interface WorkspaceFile {
  path: string
  area: 'work' | 'output'
  stage: string
  name: string
  size: number
  sha256: string
  mime_type: string
  step: string | null
  attempt: number | null
  created_at: string
}

export interface RunWorkspace {
  run_id: string
  status: string
  workflow: string | null
  attempt: number | null
  steps: WorkspaceStep[]
  replans: WorkspaceReplan[]
  files: WorkspaceFile[]
  slug: string | null
}

// ---- 档位与额度 --------------------------------------------------------------

export type TierKey = 'light' | 'standard' | 'deep'

export interface TierSpec {
  key: TierKey
  title: string
  description: string
  max_sub_questions: number
  max_rounds: number
  results_per_search: number
}

export interface UsageQuota {
  period: 'day'
  resets_at: string
  runs: { used: number; limit: number | null }
  tokens: { used: number; limit: number | null }
  exhausted: boolean
}

// ── 任务附件（POST /api/attachments）─────────────────────────────────
/** 已解析附件的完整数据：创建任务时原样放进 CreateRunRequest.attachments */
export type AttachmentPayload = Record<string, unknown> & { id: string; filename: string }

/** 附件展示摘要 */
export interface AttachmentSummary {
  id: string
  filename: string
  kind: string
  mime_type: string
  size: number
  char_count: number
  chunk_count: number
  truncated: boolean
  preview: string
  sections: string[]
}

export interface AttachmentUploadResult {
  attachment: AttachmentPayload
  summary: AttachmentSummary
}
