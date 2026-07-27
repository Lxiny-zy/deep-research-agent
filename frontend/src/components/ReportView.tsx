import { useCallback, useMemo, useRef, useState, type AnchorHTMLAttributes } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  CITE_HREF_PREFIX,
  findingsForUrl,
  remarkCitations,
  resolveCitationTargets,
  summarizeEvidence,
} from '../lib/evidence'
import type { Finding } from '../types'
import { AppIcon } from './AppIcon'
import EvidencePanel from './EvidencePanel'

// 可审计报告视图：
// - [n] 引用渲染为可点击角标（有匹配 findings 时），点击打开证据侧栏；
// - 报告头部证据链概览条分开展示原文匹配、语义支持、冲突与来源拦截；
//   拦截数来自事件流 source_policy 审计事件，拿不到时降级只显示前三项并注明。
// 流式阶段 findings/citations 可能为空——引用降级为不可点击的普通角标，不显示概览条。
export default function ReportView({
  markdown,
  streaming,
  findings = [],
  citations = [],
  blockedSources = null,
}: {
  markdown: string
  streaming: boolean
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

  return (
    <div className={`report-view${activeCitation != null ? ' has-evidence' : ''}`}>
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
            {markdown}
          </Markdown>
          {streaming && <span className="cursor">▍</span>}
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
