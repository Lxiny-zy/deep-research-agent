import { useCallback, useMemo, useRef, useState, type AnchorHTMLAttributes } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
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
import type { Finding } from '../types'
import { AppIcon } from './AppIcon'
import EvidencePanel from './EvidencePanel'

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
}: {
  markdown: string
  streaming: boolean
  isLive?: boolean
  findings?: Finding[]
  citations?: string[]
  blockedSources?: number | null
}) {
  const [activeCitation, setActiveCitation] = useState<number | null>(null)
  const activeCitationRef = useRef(activeCitation)
  activeCitationRef.current = activeCitation
  const citationTriggerRef = useRef<HTMLButtonElement | null>(null)
  const targets = useMemo(() => resolveCitationTargets(markdown, citations), [markdown, citations])
  const overview = useMemo(() => summarizeEvidence(findings), [findings])
  // 参考来源列表。结构化文档把「## 参考来源」从正文里剥掉并放进独立的
  // references 字段（见 report/assemble.py 的 _body），所以正文本身不再带
  // 这一段——不在这里补渲染，读者就只剩下角标，没有可平铺核对的来源清单。
  const cited = useMemo(() => citedSources(targets), [targets])
  // 正文统一剥掉尾部的参考来源段：来源由下面独立成节渲染，两种数据源
  // （结构化文档已剥离 / 旧 report.markdown 未剥离）因此行为一致，不会有
  // 一种路径印两遍、另一种路径不印。
  const body = useMemo(() => stripTrailingReferences(markdown), [markdown])
  const closeEvidence = useCallback(() => {
    setActiveCitation(null)
    citationTriggerRef.current?.focus()
  }, [])

  const components = useMemo<Components>(
    () => ({
      a: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        const link = href ?? ''
        if (!link.startsWith(CITE_HREF_PREFIX)) {
          return (
            <a href={link} target="_blank" rel="noreferrer" {...rest}>
              {children}
            </a>
          )
        }
        const n = Number(link.slice(CITE_HREF_PREFIX.length))
        const url = targets[n - 1]
        const clickable = findingsForUrl(findings, url).length > 0
        if (!clickable) {
          // 无证据数据（流式阶段/来源无结构化 findings）：降级为不可点击角标
          return <span className="cite-ref inert">{children}</span>
        }
        return (
          <button
            type="button"
            className={`cite-ref${activeCitationRef.current === n ? ' active' : ''}`}
            title={url}
            aria-label={`查看引用 ${n} 的证据`}
            aria-controls="evidence-panel"
            aria-expanded={activeCitationRef.current === n}
            aria-pressed={activeCitationRef.current === n}
            onClick={(event) => {
              citationTriggerRef.current = event.currentTarget
              setActiveCitation(n)
            }}
          >
            {children}
          </button>
        )
      },
    }),
    [targets, findings],
  )

  if (!markdown) {
    return (
      <p className="muted small">
        {streaming ? '报告生成中…' : '报告将在这里显示（含 [n] 引用与参考来源）。'}
      </p>
    )
  }

  const activeUrl = activeCitation != null ? targets[activeCitation - 1] : undefined
  const activeFindings = findingsForUrl(findings, activeUrl)
  const reportIsLive = isLive ?? streaming

  return (
    <div
      className={`report-view${activeCitation != null ? ' has-evidence' : ''}${reportIsLive ? ' is-streaming' : ''}`}
    >
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
      <div className="report-view-body">
        <div className="report markdown-content">
          <Markdown remarkPlugins={[remarkGfm, remarkCitations]} components={components}>
            {body}
          </Markdown>
          {streaming && <span className="cursor">▍</span>}
          {/* 流式阶段不渲染来源节：正文还在写，此时的 citations 是残缺快照，
              先给出一份会随后变化的清单，比暂时不给更容易误导。 */}
          {!streaming && cited.length > 0 && (
            <section className="report-references" aria-label="参考来源">
              <h2>参考来源</h2>
              <ol>
                {cited.map(({ n, url }) => (
                  <li key={`${n}-${url}`} value={n}>
                    <a href={url} target="_blank" rel="noreferrer">
                      {referenceTextFor(findings, url)}
                    </a>
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
            url={activeUrl}
            findings={activeFindings}
            allFindings={findings}
            onClose={closeEvidence}
          />
        )}
      </div>
    </div>
  )
}
