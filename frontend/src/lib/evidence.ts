// 证据链前端逻辑（可审计报告）：
// - remarkCitations：把报告正文里的 [n] 引用文本转成可拦截的 link 节点（#cite-n）；
// - 概览统计：N 论断 / M 原文匹配 / S 语义支持 / C 交叉印证 / K 冲突；
// - 来源拦截数：解析事件流中 RESEARCHER 发出的 source_policy 审计事件
//   （与 EventTimeline 相同的事件消费方式，直播与历史回放均可用）；
// - 引用序号 → 来源 URL：优先 report.citations，缺失时从「## 参考来源」回退解析。
import type {
  Finding,
  QuantityStatus,
  ReportEvidence,
  ResearchEvent,
  ResearchResult,
} from '../types'

export const CITE_HREF_PREFIX = '#cite-'

// ── remark 插件 ─────────────────────────────────────────────────────
// mdast 节点的最小结构类型（避免引入 @types/mdast 传递依赖）。
interface MdNode {
  type: string
  value?: string
  url?: string
  children?: MdNode[]
}

const CITE_PATTERN = /\[(\d{1,3})\]/g

/** remark 插件：把文本节点中的 [n] 转成 url 为 `#cite-n` 的 link 节点。 */
export function remarkCitations() {
  return (tree: unknown) => transformCitations(tree as MdNode)
}

function transformCitations(node: MdNode): void {
  const children = node.children
  if (!children) return
  // 未解析成功的 [n] 引用标记可能被 micromark 拆成相邻的多个 text 节点，
  // 先合并相邻 text 再切分，保证跨节点的 [n] 也能识别。
  const merged: MdNode[] = []
  for (const child of children) {
    const prev = merged[merged.length - 1]
    if (child.type === 'text' && prev?.type === 'text') {
      prev.value = (prev.value ?? '') + (child.value ?? '')
      continue
    }
    merged.push(child)
  }
  const next: MdNode[] = []
  for (const child of merged) {
    if (child.type === 'text' && child.value) {
      next.push(...splitCitationText(child.value))
      continue
    }
    // 链接内部不再转换，避免产生非法的嵌套链接。
    if (child.type !== 'link' && child.type !== 'linkReference') {
      transformCitations(child)
    }
    next.push(child)
  }
  node.children = next
}

function splitCitationText(value: string): MdNode[] {
  const out: MdNode[] = []
  let last = 0
  for (const match of value.matchAll(CITE_PATTERN)) {
    const start = match.index ?? 0
    if (start > last) out.push({ type: 'text', value: value.slice(last, start) })
    out.push({
      type: 'link',
      url: `${CITE_HREF_PREFIX}${match[1]}`,
      children: [{ type: 'text', value: match[0] }],
    })
    last = start + match[0].length
  }
  if (out.length === 0) return [{ type: 'text', value }]
  if (last < value.length) out.push({ type: 'text', value: value.slice(last) })
  return out
}

// ── findings 汇总与匹配 ─────────────────────────────────────────────
export function flattenFindings(results: ResearchResult[] | null | undefined): Finding[] {
  return (results ?? []).flatMap((r) => r.findings)
}

type SemanticStatus = Finding['verification']['semantic_status']
type ConsistencyStatus = Finding['verification']['consistency_status']
type CorroborationStatus = Finding['verification']['corroboration_status']

function semanticStatus(value: string): SemanticStatus {
  switch (value) {
    case 'supported':
    case 'unsupported':
    case 'uncertain':
    case 'not_checked':
      return value
    default:
      return 'not_checked'
  }
}

function consistencyStatus(value: string): ConsistencyStatus {
  switch (value) {
    case 'clear':
    case 'conflicted':
    case 'not_checked':
      return value
    default:
      return 'not_checked'
  }
}

function corroborationStatus(value: string): CorroborationStatus {
  switch (value) {
    case 'single_source':
    case 'corroborated':
    case 'disputed':
    case 'not_checked':
      return value
    default:
      return 'not_checked'
  }
}

function quantityStatus(value: string): QuantityStatus {
  switch (value) {
    case 'verified':
    case 'unsupported':
    case 'not_applicable':
      return value
    default:
      // 未知取值退回 not_applicable＝"这条没有结构化数值"，而不是 unsupported
      // ＝"数值没通过核对"。后者是一个我们并不掌握的负面断言。
      return 'not_applicable'
  }
}

/**
 * Adapt the server-owned structured evidence wire shape to the legacy Finding
 * shape consumed by the interactive evidence drawer and print appendix.
 *
 * This is intentionally explicit: ReportEvidence is a persisted report
 * contract, while Finding is the live/detail contract. Keeping the adapter at
 * this boundary prevents either shape from being unsafely asserted as the
 * other and gives old runs a well-defined fallback in the page layer.
 */
export function reportEvidenceToFinding(record: ReportEvidence): Finding {
  const confidence = Number.isFinite(record.semantic_confidence)
    ? Math.max(0, Math.min(1, record.semantic_confidence))
    : 0
  const sourceReference = record.reference.trim()
  return {
    statement: record.statement,
    source_url: record.source_url,
    evidence_quote: record.quote,
    confidence,
    verification: {
      status: record.verbatim_verified ? 'verified' : 'unverified',
      method: record.verbatim_verified ? 'normalized_quote' : 'none',
      source_content_hash: record.content_hash,
      source_title: sourceReference || undefined,
      source_reference: sourceReference || undefined,
      evidence_context: record.context,
      quantity_label: record.quantity_label,
      conditions_label: record.conditions_label,
      quantity_status: quantityStatus(record.quantity_status),
      quantity_reason: record.quantity_reason,
      reason: record.verification_reason,
      semantic_status: semanticStatus(record.semantic_status),
      semantic_confidence: confidence,
      semantic_reason: record.semantic_reason,
      claim_id: record.claim_id,
      consistency_status: consistencyStatus(record.consistency_status),
      contradicts_claim_ids: [...record.contradicts_claim_ids],
      contradiction_reason: record.contradiction_reason,
      corroboration_status: corroborationStatus(record.corroboration_status),
      independent_source_count: Math.max(0, record.independent_source_count),
      // The structured contract stores the corroboration summary but not the
      // reverse claim links. There is no safe link to synthesize here.
      corroborates_claim_ids: [],
      corroboration_reason: record.corroboration_reason,
    },
  }
}

export function reportEvidenceToFindings(records: ReportEvidence[] | null | undefined): Finding[] {
  return (records ?? []).map(reportEvidenceToFinding)
}

export interface EvidenceOverview {
  records: number
  verbatimMatched: number
  semanticallySupported: number
  corroborated: number
  conflicted: number
}

export function summarizeEvidence(findings: Finding[]): EvidenceOverview {
  let verbatimMatched = 0
  let semanticallySupported = 0
  let corroborated = 0
  let conflicted = 0
  for (const f of findings) {
    if (f.verification.status === 'verified') verbatimMatched += 1
    if (f.verification.status === 'verified' && f.verification.semantic_status === 'supported') {
      semanticallySupported += 1
    }
    if (f.verification.corroboration_status === 'corroborated') corroborated += 1
    if (f.verification.consistency_status === 'conflicted') conflicted += 1
  }
  return {
    records: findings.length,
    verbatimMatched,
    semanticallySupported,
    corroborated,
    conflicted,
  }
}

/**
 * 从事件流解析来源策略拦截数（data.category === 'source_policy' 的审计事件，
 * 累加 data.blocked）。事件流（直播或回放）里一条审计事件都没有时返回 null，
 * 概览条据此降级为只显示前三项。
 */
export function countBlockedSources(events: ResearchEvent[]): number | null {
  let found = false
  let blocked = 0
  for (const ev of events) {
    const data = ev.data as { category?: unknown; blocked?: unknown } | null | undefined
    if (data?.category !== 'source_policy') continue
    found = true
    if (typeof data.blocked === 'number') blocked += data.blocked
  }
  return found ? blocked : null
}

/**
 * 引用序号 → 来源 URL（[n] ↔ 返回数组下标 n-1）。
 * 优先使用已落库的 report.citations；流式阶段没有时从 markdown 尾部的
 * 「## 参考来源」列表回退解析。
 *
 * 行格式有两种，取决于来源有没有学术元数据：
 *   `[1] https://example.com/a`                                    （通用网页来源）
 *   `[2] 作者. 标题. 期刊, 2024. https://doi.org/10.1364/oe.456`     （学术来源）
 * 后端渲染时保证 URL 位于行尾且不带尾随标点（见 deep_research/citation.py），
 * 因此这里统一取行尾的 http(s) 串，容忍它前面的任意引用文本。
 */
export function resolveCitationTargets(markdown: string, citations?: string[] | null): string[] {
  if (citations && citations.length > 0) return [...citations]
  const targets: string[] = []
  for (const match of markdown.matchAll(
    /^\[(\d{1,3})\]\s+(?:.*\s)?(https?:\/\/\S+?)[.,;、。]?\s*$/gm,
  )) {
    targets[Number(match[1]) - 1] = match[2]
  }
  return targets
}

export function findingsForUrl(findings: Finding[], url: string | undefined): Finding[] {
  if (!url) return []
  return findings.filter((f) => f.source_url === url)
}

/**
 * 参考来源展示文本：优先取落库的学术引用，缺失时回退裸 URL。
 *
 * 屏幕与纸面必须给出同一串文本——两处各写一遍 join，迟早会在"有没有回退到
 * URL"这种细节上分叉，而读者拿它们互相核对。
 */
export function referenceTextFor(findings: Finding[], url: string): string {
  const withReference = findings.find(
    (f) => f.source_url === url && (f.verification.source_reference ?? '').trim(),
  )
  return withReference?.verification.source_reference?.trim() || url
}

/** 引用序号 + URL 对，序号是 targets 的原始下标（不因空洞而重编号）。 */
export interface CitedSource {
  n: number
  url: string
}

/**
 * Synthesizer 会在正文末尾追加「## 参考来源」段落（``synthesizer._finalize``）。
 * 参考来源在视图里独立成节，正文若把那一段带进来就会渲染两遍。
 *
 * 只匹配**结尾**（``$`` 配合 [\s\S]*），因此正文中间出现"参考来源"字样的普通
 * 段落不受影响。与后端 ``report/assemble.py`` 的 ``_REFERENCES_HEADING`` 同口径。
 *
 * 注意调用顺序：``resolveCitationTargets`` 在 citations 缺失时要从这一段回退
 * 解析，所以必须先解析 targets、再剥离正文，不能反过来。
 */
export function stripTrailingReferences(markdown: string): string {
  return markdown.replace(/\n#{2,3}\s*参考来源\s*\n[\s\S]*$/, '').trim()
}

/**
 * 把 `resolveCitationTargets` 的稀疏数组压成可渲染的列表。
 *
 * 关键是**保留原始序号**：稀疏数组里 targets[0] 缺失、targets[1] 有值时，
 * 直接 `filter(Boolean)` 会让它变成列表第 1 项，正文里的 [2] 就指向了标着
 * [1] 的条目。序号必须跟着下标走，而不是跟着渲染顺序走。
 */
export function citedSources(targets: (string | undefined)[]): CitedSource[] {
  const out: CitedSource[] = []
  targets.forEach((url, index) => {
    if (url) out.push({ n: index + 1, url })
  })
  return out
}

export function findingByClaimId(findings: Finding[], claimId: string): Finding | undefined {
  return findings.find((f) => f.verification.claim_id === claimId)
}

export function shortHash(hash: string, length = 10): string {
  return hash.slice(0, length)
}
