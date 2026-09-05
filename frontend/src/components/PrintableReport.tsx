import { useMemo } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import StructuredDocumentPreview from './StructuredDocumentPreview'
import {
  citedSources,
  findingsForUrl,
  referenceTextFor,
  remarkCitations,
  resolveCitationTargets,
  stripTrailingReferences,
  summarizeEvidence,
} from '../lib/evidence'
import type { Finding, ReportDocument } from '../types'

// 可打印报告：屏幕上的应用 → 纸上的报告。
//
// 这不是「把 ReportView 缩小」，而是另一种信息组织。屏幕上证据装置是**按需**的
// （点 [n] 开侧栏，一次只看一条）；纸上没有「按需」，所以侧栏必须被**替换**成
// 尾部附录，把全部证据一次性呈现。只把侧栏 display:none 会让装置整体消失。
//
// 该组件常驻 DOM：屏幕上由 print.css 的 .print-only 隐藏，打印媒体下显示。
// 这样 Ctrl+P 与「打印」按钮走的是同一条路径，不需要先跳转再打印。
//
// 数据取自与 ReportView 相同的 props（复用 lib/evidence 的同一套 join），因此
// 任何已存在的 run 立即可打印，无需额外请求。后端 `GET /api/runs/{id}/document`
// 是同语义的服务端装配，供 .md 导出与将来的服务端 PDF 使用；两者由各自的测试
// 钉在同一口径上（引用顺序跟随 citations、拦截数缺失记为不可用、缺失如实呈现）。

const SEMANTIC_TEXT: Record<Finding['verification']['semantic_status'], string> = {
  not_checked: '语义未检查',
  supported: '语义支持',
  unsupported: '语义不支持',
  uncertain: '语义存疑',
}

const CORROBORATION_TEXT: Record<
  NonNullable<Finding['verification']['corroboration_status']>,
  string
> = {
  not_checked: '交叉印证未检查',
  single_source: '单一来源',
  corroborated: '已交叉印证',
  disputed: '来源存在争议',
}

const CONSISTENCY_TEXT: Record<Finding['verification']['consistency_status'], string> = {
  not_checked: '一致性未检查',
  clear: '未检测到冲突',
  conflicted: '存在冲突',
}

/**
 * 免责声明的本地回退。
 *
 * 权威副本在后端 `report/document.py` 的 DISCLAIMER——它被放在模型里而不是各
 * 渲染器里，正是为了让 Markdown / PDF / 打印三种输出说同一句话。这句话界定了
 * 系统到底声称了什么，措辞漂移就是声称范围漂移。
 *
 * 所以这里只在**拿不到结构化文档时**（流式阶段、旧 run、/document 不可用）
 * 使用；有文档时一律以 document.disclaimer 为准。
 */
export const PRINT_DISCLAIMER =
  '本报告展示的是检索服务返回的快照上下文，不等同于来源完整正文，也不等同于事实已获证实。' +
  '系统保证的是出处可追溯、引用可逐字核验、单源/双源/冲突状态可判定；不保证论断在开放世界为真。'

/** 在上下文中高亮逐字引文——与屏幕侧栏一致的 <mark>，打印样式给它浅黄底。 */
function highlighted(context: string, quote: string) {
  const needle = quote.trim()
  if (!needle) return context
  const index = context.indexOf(needle)
  if (index < 0) return context
  return (
    <>
      {context.slice(0, index)}
      <mark>{needle}</mark>
      {context.slice(index + needle.length)}
    </>
  )
}

function Badges({ v }: { v: Finding['verification'] }) {
  const corroboration = v.corroboration_status ?? 'not_checked'
  const sources = Math.max(0, v.independent_source_count ?? 0)
  return (
    <div className="print-badges">
      <span className="print-badge">{v.status === 'verified' ? '原文匹配' : '未通过原文匹配'}</span>
      <span className="print-badge">
        {SEMANTIC_TEXT[v.semantic_status]}
        {v.semantic_status !== 'not_checked' &&
          `（置信度 ${Math.round((v.semantic_confidence ?? 0) * 100)}%）`}
      </span>
      <span className="print-badge">{CONSISTENCY_TEXT[v.consistency_status]}</span>
      <span className="print-badge">
        {CORROBORATION_TEXT[corroboration]}
        {corroboration !== 'not_checked' && ` · ${sources} 个独立来源`}
      </span>
    </div>
  )
}

function EvidenceCard({ finding }: { finding: Finding }) {
  const v = finding.verification
  const context = (v.evidence_context ?? '').trim()
  const quote = finding.evidence_quote.trim()
  return (
    <article className="print-evidence-card">
      <p className="print-claim-text">
        <strong>论断：</strong>
        {finding.statement}
      </p>
      {(context || quote) && (
        <blockquote cite={finding.source_url}>
          {context ? highlighted(context, quote) : quote}
        </blockquote>
      )}
      {(v.quantity_label || v.conditions_label) && (
        <div className="print-structured-facts">
          {v.quantity_label && (
            <p>
              <strong>数值：</strong>
              {v.quantity_label}
            </p>
          )}
          {v.conditions_label && (
            <p>
              <strong>成立条件：</strong>
              {v.conditions_label}
            </p>
          )}
          {v.quantity_status && v.quantity_status !== 'not_applicable' && (
            <p>
              <strong>数值校验：</strong>
              {v.quantity_status === 'verified' ? '已在原文中核对' : '未通过原文核对'}
              {v.quantity_reason && `（${v.quantity_reason}）`}
            </p>
          )}
        </div>
      )}
      <Badges v={v} />
      {/* 以下四项原先只活在 HTML title= tooltip 里。tooltip 打印不输出、触屏不可达，
          靠它承载信息在移动端就已经在无声丢失，所以纸上必须是正式内容。 */}
      {v.reason?.trim() && (
        <p className="print-reason">
          <strong>验证说明：</strong>
          {v.reason}
        </p>
      )}
      {v.semantic_reason?.trim() && (
        <p className="print-reason">
          <strong>语义判定理由：</strong>
          {v.semantic_reason}
        </p>
      )}
      {v.corroboration_reason?.trim() && (
        <p className="print-reason">
          <strong>印证说明：</strong>
          {v.corroboration_reason}
        </p>
      )}
      {v.consistency_status === 'conflicted' && (
        <p className="print-reason">
          <strong>冲突：</strong>
          {v.contradiction_reason || '与其他论断矛盾'}
          {v.contradicts_claim_ids.length > 0 && `（参见 ${v.contradicts_claim_ids.join('、')}）`}
        </p>
      )}
      <p>
        {v.claim_id && <span className="print-claim">claim {v.claim_id}</span>}
        {/* 完整哈希，不截断：截断版本无法用来核对快照，那就失去了留证的意义。 */}
        {v.source_content_hash && (
          <span className="print-hash"> 快照哈希 {v.source_content_hash}</span>
        )}
      </p>
    </article>
  )
}

export default function PrintableReport({
  markdown,
  query,
  runId,
  findings = [],
  citations = [],
  blockedSources = null,
  createdAt,
  preview = false,
  document,
}: {
  markdown: string
  query: string
  runId?: string
  findings?: Finding[]
  citations?: string[]
  blockedSources?: number | null
  createdAt?: string | null
  preview?: boolean
  document?: ReportDocument
}) {
  const targets = useMemo(() => resolveCitationTargets(markdown, citations), [markdown, citations])
  const overview = useMemo(() => summarizeEvidence(findings), [findings])

  // 打印时 [n] 退回普通文本标记。屏幕上它是按钮（点开侧栏），保留按钮外观会让
  // 读者以为纸上能点。
  const components = useMemo<Components>(
    () => ({
      a: ({ href, children }) => {
        const link = href ?? ''
        if (link.startsWith('#cite-')) return <span className="cite-ref">{children}</span>
        return <span>{children}</span>
      },
    }),
    [],
  )

  // 正文里 Synthesizer 追加的「## 参考来源」段落要剥掉——参考来源在下面独立成节，
  // 带着那一段会渲染两遍。只在结尾匹配，正文中间提到「参考来源」不受影响。
  const body = useMemo(() => stripTrailingReferences(markdown), [markdown])

  const cited = useMemo(() => citedSources(targets), [targets])

  return (
    <section
      className={`print-root${preview ? ' print-preview' : ' print-only'}`}
      data-testid="printable-report"
      aria-label="可打印报告"
    >
      <header className="print-head">
        <h1>{query || '研究报告'}</h1>
        <p className="print-meta">
          {createdAt && <span>生成时间：{createdAt}</span>}
          {runId && <span> · 运行 {runId}</span>}
        </p>
      </header>

      <p className="print-disclaimer">{document?.disclaimer?.trim() || PRINT_DISCLAIMER}</p>

      {findings.length > 0 && (
        <section className="print-overview">
          <h2>证据链概览</h2>
          <table>
            <thead>
              <tr>
                <th>指标</th>
                <th className="numeric">数量</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>证据记录</td>
                <td className="numeric">{overview.records}</td>
              </tr>
              <tr>
                <td>原文匹配</td>
                <td className="numeric">{overview.verbatimMatched}</td>
              </tr>
              <tr>
                <td>语义支持</td>
                <td className="numeric">{overview.semanticallySupported}</td>
              </tr>
              <tr>
                <td>已交叉印证</td>
                <td className="numeric">{overview.corroborated}</td>
              </tr>
              <tr>
                <td>存在冲突</td>
                <td className="numeric">{overview.conflicted}</td>
              </tr>
              <tr>
                <td>来源被拦截</td>
                {/* 拿不到审计事件时写「不可用」，不写 0——0 是我们并不掌握的具体断言。 */}
                <td className="numeric">
                  {blockedSources == null ? '不可用（本次事件流未含审计事件）' : blockedSources}
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      )}

      <section className="print-body markdown-content">
        <Markdown remarkPlugins={[remarkGfm, remarkCitations]} components={components}>
          {body}
        </Markdown>
      </section>

      {document && <StructuredDocumentPreview document={document} print />}

      {cited.length > 0 && (
        <section className="print-references">
          <h2>参考来源</h2>
          <ol className="print-reference-list">
            {cited.map(({ n, url }) => (
              <li
                key={`${n}-${url}`}
                value={n}
                className="print-reference-item"
                data-reference-index={n}
              >
                <span className="print-reference-text">{referenceTextFor(findings, url)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {findings.length > 0 && (
        <section className="print-appendix">
          <h2>证据附录</h2>
          {cited.map(({ n, url }) => {
            const forUrl = findingsForUrl(findings, url)
            if (forUrl.length === 0) return null
            return (
              <section className="print-appendix-group" key={`${n}-${url}`}>
                <h3>
                  [{n}] {referenceTextFor(findings, url)}
                </h3>
                {forUrl.map((finding, i) => (
                  <EvidenceCard
                    key={finding.verification.claim_id || `${finding.source_url}-${i}`}
                    finding={finding}
                  />
                ))}
              </section>
            )
          })}
        </section>
      )}
    </section>
  )
}
