// 证据链前端逻辑（可审计报告）：
// - remarkCitations：把报告正文里的 [n] 引用文本转成可拦截的 link 节点（#cite-n）；
// - 概览统计：N 论断 / M 原文匹配 / S 语义支持 / K 冲突；
// - 来源拦截数：解析事件流中 RESEARCHER 发出的 source_policy 审计事件
//   （与 EventTimeline 相同的事件消费方式，直播与历史回放均可用）；
// - 引用序号 → 来源 URL：优先 report.citations，缺失时从「## 参考来源」回退解析。
import type { Finding, ResearchEvent, ResearchResult } from '../types'

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

export interface EvidenceOverview {
  records: number
  verbatimMatched: number
  semanticallySupported: number
  conflicted: number
}

export function summarizeEvidence(findings: Finding[]): EvidenceOverview {
  let verbatimMatched = 0
  let semanticallySupported = 0
  let conflicted = 0
  for (const f of findings) {
    if (f.verification.status === 'verified') verbatimMatched += 1
    if (f.verification.status === 'verified' && f.verification.semantic_status === 'supported') {
      semanticallySupported += 1
    }
    if (f.verification.consistency_status === 'conflicted') conflicted += 1
  }
  return { records: findings.length, verbatimMatched, semanticallySupported, conflicted }
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
 * 「## 参考来源」列表回退解析（格式：`[n] url`）。
 */
export function resolveCitationTargets(markdown: string, citations?: string[] | null): string[] {
  if (citations && citations.length > 0) return [...citations]
  const targets: string[] = []
  for (const match of markdown.matchAll(/^\[(\d{1,3})\]\s+(\S+)\s*$/gm)) {
    targets[Number(match[1]) - 1] = match[2]
  }
  return targets
}

export function findingsForUrl(findings: Finding[], url: string | undefined): Finding[] {
  if (!url) return []
  return findings.filter((f) => f.source_url === url)
}

export function findingByClaimId(findings: Finding[], claimId: string): Finding | undefined {
  return findings.find((f) => f.verification.claim_id === claimId)
}

export function shortHash(hash: string, length = 10): string {
  return hash.slice(0, length)
}
