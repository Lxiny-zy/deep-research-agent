import { useCallback, useEffect, useMemo, useRef, useState, type AnchorHTMLAttributes } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { mathRemarkPlugins, mathRehypePlugins, normalizeMathMarkdown } from '../lib/scientificMath'
import {
  CITE_HREF_PREFIX,
  citedSources,
  findingsForUrl,
  referenceTextFor,
  remarkCitations,
  resolveCitationTargets,
  stripTrailingReferences,
  summarizeEvidence,
} from '../lib/evidence'
import type { Finding, ReportBibliography } from '../types'
import {
  catalogForReport,
  citedDocuments,
  citationLocations,
  citationOccurrence,
  documentNumber,
  reviewedFindings,
} from '../lib/bibliography'
import { AppIcon } from './AppIcon'
import EvidencePanel from './EvidencePanel'
import { verificationText } from '../lib/verificationText'

// 可审计报告视图：
// - [n] 引用渲染为可点击角标（有匹配 findings 时），点击打开证据侧栏；
// - 报告头部证据链概览条分开展示原文匹配、语义支持、交叉印证、冲突与来源拦截；
//   拦截数来自事件流 source_policy 审计事件，拿不到时保留其余统计并注明。
// 流式阶段 findings/citations 可能为空——引用降级为不可点击的普通角标，不显示概览条。
export default function ReportView({
  markdown,
  streaming,
  isLive,
  findings = [],
  citations = [],
  blockedSources = null,
  finalReview,
  bibliography,
}: {
  markdown: string
  streaming: boolean
  isLive?: boolean
  findings?: Finding[]
  citations?: string[]
  blockedSources?: number | null
  finalReview?: { status: string; issues: string[]; fallback?: boolean }
  bibliography?: ReportBibliography | null
}) {
  const [activeLocations, setActiveLocations] = useState<number[]>([])
  const [activeTarget, setActiveTarget] = useState<string | null>(null)
  const [selectedOccurrenceId, setSelectedOccurrenceId] = useState<string | null>(null)
  const [browsingSource, setBrowsingSource] = useState(false)
  const activeCitation = activeLocations[0] ?? null
  const setActiveCitation = useCallback((value: number | null) => {
    setActiveLocations(value == null ? [] : [value])
    setActiveTarget(value == null ? null : `#cite-${value}`)
    setSelectedOccurrenceId(null)
    setBrowsingSource(value != null)
  }, [])
  const activeTargetRef = useRef(activeTarget)
  activeTargetRef.current = activeTarget
  const citationTriggerRef = useRef<HTMLButtonElement | null>(null)
  const targets = useMemo(
    () => (streaming ? [] : resolveCitationTargets(markdown, citations)),
    [markdown, citations, streaming],
  )
  const overview = useMemo(() => summarizeEvidence(findings), [findings])
  const catalog = useMemo(
    () => (streaming ? undefined : catalogForReport(markdown, targets, bibliography)),
    [markdown, targets, bibliography, streaming],
  )
  // 参考来源列表。结构化文档把「## 参考来源」从正文里剥掉并放进独立的
  // references 字段（见 report/assemble.py 的 _body），所以正文本身不再带
  // 这一段——不在这里补渲染，读者就只剩下角标，没有可平铺核对的来源清单。
  const cited = useMemo(() => citedSources(targets), [targets])
  // 正文统一剥掉尾部的参考来源段：来源由下面独立成节渲染，两种数据源
  // （结构化文档已剥离 / 旧 report.markdown 未剥离）因此行为一致，不会有
  // 一种路径印两遍、另一种路径不印。
  const body = useMemo(
    () => (streaming ? markdown : (catalog?.body ?? stripTrailingReferences(markdown))),
    [markdown, streaming, catalog],
  )
  const closeEvidence = useCallback(() => {
    setActiveCitation(null)
  }, [setActiveCitation])
  const selectedOccurrence = selectedOccurrenceId
    ? citationOccurrence(`#cite-o-${selectedOccurrenceId}`, catalog)
    : undefined
  useEffect(() => {
    if (selectedOccurrenceId && !selectedOccurrence) closeEvidence()
  }, [selectedOccurrenceId, selectedOccurrence, closeEvidence])

  const components = useMemo<Components>(
    () => ({
      pre: ({ children }) => (
        <pre tabIndex={0} aria-label="代码示例">
          {children}
        </pre>
      ),
      a: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        const link = href ?? ''
        if (!link.startsWith(CITE_HREF_PREFIX)) {
          return (
            <a href={link} target="_blank" rel="noreferrer" {...rest}>
              {children}
            </a>
          )
        }
        const occurrence = citationOccurrence(link, catalog)
        const indices = occurrence?.locations ?? citationLocations(link)
        const n = indices[0]
        const display = documentNumber(catalog, n)
        const url = targets[n - 1]
        const clickable = Boolean(url) && (!streaming || findingsForUrl(findings, url).length > 0)
        if (!clickable) {
          // 无证据数据（流式阶段/来源无结构化 findings）：降级为不可点击角标
          return <span className="cite-ref inert">{children}</span>
        }
        return (
          <button
            type="button"
            className={`cite-ref${activeTargetRef.current === link ? ' active' : ''}`}
            title={url}
            aria-label={`查看引用 ${display} 的证据`}
            aria-controls="evidence-panel"
            aria-expanded={activeTargetRef.current === link}
            aria-pressed={activeTargetRef.current === link}
            onClick={(event) => {
              citationTriggerRef.current = event.currentTarget
              setActiveLocations(indices)
              setActiveTarget(link)
              setSelectedOccurrenceId(occurrence?.id ?? null)
              setBrowsingSource(false)
            }}
          >
            {children}
          </button>
        )
      },
    }),
    [targets, findings, streaming, catalog],
  )

  if (!markdown) {
    return (
      <p className="muted small">
        {streaming ? '报告生成中…' : '报告将在这里显示（含 [n] 引用与参考来源）。'}
      </p>
    )
  }

  const activeUrl = activeCitation != null ? targets[activeCitation - 1] : undefined
  const scoped = selectedOccurrence && selectedOccurrence.scope !== 'source_location'
  const activeFindings =
    !browsingSource && scoped
      ? reviewedFindings(findings, selectedOccurrence, targets)
      : browsingSource
        ? [
            ...new Set(
              activeLocations.flatMap((index) => findingsForUrl(findings, targets[index - 1])),
            ),
          ]
        : []
  const reportIsLive = isLive ?? streaming

  return (
    <div
      className={`report-view${activeCitation != null ? ' has-evidence' : ''}${reportIsLive ? ' is-streaming' : ''}`}
    >
      {!streaming && finalReview && (
        <div
          className="run-validation-note"
          role={finalReview.status === 'fail' ? 'alert' : 'status'}
        >
          <span>
            {finalReview.status === 'fail'
              ? '正文的结论依据尚未全部核验通过，正式交付暂不可用。'
              : finalReview.fallback
                ? '已回退为核验素材摘要，并完成结论依据核对（模型辅助）。'
                : '已完成终稿结论依据核对（模型辅助）。'}
          </span>
          {finalReview.status === 'fail' && finalReview.issues.length > 0 && (
            <details>
              <summary>查看未通过的内容</summary>
              <ul>
                {finalReview.issues.map((issue, index) => (
                  <li key={index}>{verificationText(issue, '本段核验尚未完成，请稍后重试。')}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {findings.length > 0 && (
        <div className="evidence-overview" data-testid="evidence-overview">
          <span className="evidence-stat" data-testid="evidence-records">
            <AppIcon name="file-search" size={13} aria-hidden="true" />
            <b>{overview.records}</b> 证据记录
          </span>
          <span className="evidence-stat verified" data-testid="evidence-verbatim">
            <AppIcon name="shield" size={13} aria-hidden="true" />
            <b>{overview.verbatimMatched}</b> 原文匹配
          </span>
          <span className="evidence-stat supported" data-testid="evidence-supported">
            <AppIcon name="check-circle" size={13} aria-hidden="true" />
            <b>{overview.semanticallySupported}</b> 语义支持
          </span>
          <span className="evidence-stat corroborated" data-testid="evidence-corroborated">
            <AppIcon name="merge" size={13} aria-hidden="true" />
            <b>{overview.corroborated}</b> 已交叉印证
          </span>
          <span className="evidence-stat conflicted" data-testid="evidence-conflicted">
            <AppIcon name="alert" size={13} aria-hidden="true" />
            <b>{overview.conflicted}</b> 存在冲突
          </span>
          {blockedSources != null ? (
            <span className="evidence-stat blocked" data-testid="evidence-blocked">
              <AppIcon name="circle-x" size={13} aria-hidden="true" />
              <b>{blockedSources}</b> 来源被拦截
            </span>
          ) : (
            <span className="evidence-note muted small">
              拦截数不可用（本次事件流未含来源策略审计事件）
            </span>
          )}
        </div>
      )}
      {!streaming && cited.length > 0 && (
        <div className="evidence-toolbar">
          <span>{catalog?.documents.length ?? cited.length} 个引用来源</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={(event) => {
              citationTriggerRef.current = event.currentTarget
              setActiveCitation(cited[0].n)
            }}
          >
            <AppIcon name="file-search" size={15} aria-hidden="true" />
            查看证据
          </button>
        </div>
      )}
      <div className="report-view-body">
        <div className="report markdown-content">
          {streaming ? (
            <>
              <p className="hint">正文正在生成，完成前内容可能调整。结论请结合引用来源核对。</p>
              <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{body}</div>
            </>
          ) : (
            <Markdown
              remarkPlugins={[remarkGfm, ...mathRemarkPlugins, remarkCitations]}
              rehypePlugins={mathRehypePlugins}
              components={components}
            >
              {normalizeMathMarkdown(body)}
            </Markdown>
          )}
          {streaming && <span className="report-caret" aria-hidden="true" />}
          {/* 流式阶段不渲染来源节：正文还在写，此时的 citations 是残缺快照，
              先给出一份会随后变化的清单，比暂时不给更容易误导。 */}
          {!streaming && (catalog ? citedDocuments(catalog).length : cited.length) > 0 && (
            <section className="report-references" aria-label="参考来源">
              <h2>{catalog ? '参考文献' : '参考来源'}</h2>
              <ol>
                {catalog
                  ? citedDocuments(catalog).map((document) => (
                      <li key={document.identity} value={document.index}>
                        {document.url ? (
                          <a href={document.url} target="_blank" rel="noreferrer">
                            {document.reference}
                          </a>
                        ) : (
                          <span>{document.reference}</span>
                        )}
                        <button
                          type="button"
                          className="reference-evidence-button"
                          aria-label={`查看文献 ${document.index} 的证据`}
                          onClick={(event) => {
                            citationTriggerRef.current = event.currentTarget
                            setActiveLocations(document.locations)
                            setSelectedOccurrenceId(null)
                            setBrowsingSource(true)
                            setActiveTarget(null)
                          }}
                        >
                          <AppIcon name="file-search" size={15} aria-hidden="true" />
                        </button>
                      </li>
                    ))
                  : cited.map(({ n, url }) => (
                      <li key={`${n}-${url}`} value={n}>
                        <a href={url} target="_blank" rel="noreferrer">
                          {referenceTextFor(findings, url)}
                        </a>
                        <button
                          type="button"
                          className="reference-evidence-button"
                          title={`查看来源 ${n} 的证据`}
                          aria-label={`查看来源 ${n} 的证据`}
                          onClick={(event) => {
                            citationTriggerRef.current = event.currentTarget
                            setActiveCitation(n)
                          }}
                        >
                          <AppIcon name="file-search" size={15} aria-hidden="true" />
                        </button>
                      </li>
                    ))}
              </ol>
            </section>
          )}
        </div>
        {activeCitation != null && activeUrl && (
          <EvidencePanel
            id="evidence-panel"
            citation={activeCitation}
            selectedLocationCount={catalog ? activeLocations.length : undefined}
            selectionScope={
              browsingSource ? 'source_location' : scoped ? selectedOccurrence.scope : 'unbound'
            }
            reviewNote={!browsingSource ? selectedOccurrence?.review_note : undefined}
            missingEvidence={
              !browsingSource && scoped
                ? new Set(selectedOccurrence.evidence_ids).size -
                  new Set(activeFindings.map((finding) => finding.support_id)).size
                : 0
            }
            onShowAll={!browsingSource ? () => setBrowsingSource(true) : undefined}
            displayCitation={catalog ? documentNumber(catalog, activeCitation) : undefined}
            referenceUrl={
              catalog?.documents.find(
                (document) => document.index === documentNumber(catalog, activeCitation),
              )?.url
            }
            url={activeUrl}
            findings={activeFindings}
            allFindings={findings}
            onClose={closeEvidence}
            returnFocus={() => citationTriggerRef.current}
            sources={cited.map((source) => ({
              ...source,
              label: catalog
                ? `[${documentNumber(catalog, source.n)}] ${catalog.locations.find((item) => item.index === source.n)?.label || source.url}`
                : undefined,
            }))}
            onSelect={setActiveCitation}
          />
        )}
      </div>
    </div>
  )
}
