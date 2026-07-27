import { useEffect, useRef, type ReactNode } from 'react'
import { findingByClaimId, shortHash } from '../lib/evidence'
import type { Finding } from '../types'
import { AppIcon } from './AppIcon'

// 证据侧栏：点击报告里的 [n] 引用后展示该来源下的全部论断——
// 论断 → 逐字 quote（高亮）→ 程序验证徽章 → 内容哈希缩写；
// conflicted 论断额外渲染矛盾 claim 的反向链接。样式沿用 design-system 卡片语言。

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

const SEMANTIC_BADGE: Record<
  Finding['verification']['semantic_status'],
  { label: string; cls: string }
> = {
  not_checked: { label: '语义未检查', cls: 'warning' },
  supported: { label: '语义支持 · 模型判定', cls: 'success' },
  unsupported: { label: '语义不支持 · 模型判定', cls: 'error' },
  uncertain: { label: '语义存疑 · 模型判定', cls: 'warning' },
}

function VerificationBadges({ v }: { v: Finding['verification'] }) {
  const semantic = SEMANTIC_BADGE[v.semantic_status]
  return (
    <div className="evidence-badges">
      {v.status === 'verified' ? (
        <span
          className="badge success"
          title={v.reason || '摘录已在检索快照中通过归一化比对，不等同事实已证实'}
        >
          <AppIcon name="shield" size={12} aria-hidden="true" /> 原文匹配
        </span>
      ) : (
        <span className="badge warning" title={v.reason || '未通过程序验证'}>
          未验证
        </span>
      )}
      <span
        className={`badge ${semantic.cls}`}
        title={
          v.semantic_reason ||
          (v.semantic_status === 'not_checked'
            ? '尚未执行语义支持判断'
            : `模型判定置信度 ${Math.round(v.semantic_confidence * 100)}%`)
        }
      >
        {semantic.label}
      </span>
      {v.consistency_status === 'conflicted' && (
        <span className="badge error" title={v.contradiction_reason || undefined}>
          conflicted
        </span>
      )}
      {v.consistency_status === 'clear' && <span className="badge info">未检测到冲突</span>}
      {v.consistency_status === 'not_checked' && (
        <span className="badge warning" title={v.contradiction_reason || '尚未执行一致性检查'}>
          一致性未检查
        </span>
      )}
    </div>
  )
}

function ConflictLinks({ finding, allFindings }: { finding: Finding; allFindings: Finding[] }) {
  const v = finding.verification
  if (v.consistency_status !== 'conflicted' || v.contradicts_claim_ids.length === 0) return null
  return (
    <div className="evidence-conflict">
      <span className="evidence-conflict-title">
        <AppIcon name="alert" size={13} aria-hidden="true" />
        与以下论断矛盾
      </span>
      {v.contradiction_reason && <span>{v.contradiction_reason}</span>}
      {v.contradicts_claim_ids.map((claimId) => {
        const other = findingByClaimId(allFindings, claimId)
        return (
          <div className="evidence-conflict-claim" key={claimId}>
            {other ? (
              <>
                <span>{other.statement}</span>
                <a
                  className="evidence-source"
                  href={other.source_url}
                  target="_blank"
                  rel="noreferrer"
                  title={other.source_url}
                >
                  {hostOf(other.source_url)}
                  <AppIcon name="external" size={11} aria-hidden="true" />
                </a>
              </>
            ) : (
              <span className="muted small">claim {claimId}（不在本次报告素材内）</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function highlightedContext(context: string, quote: string): ReactNode {
  if (!quote) return context
  const index = context.indexOf(quote)
  if (index < 0) return context
  return (
    <>
      {context.slice(0, index)}
      <mark>{quote}</mark>
      {context.slice(index + quote.length)}
    </>
  )
}

function EvidenceCard({ finding, allFindings }: { finding: Finding; allFindings: Finding[] }) {
  const verification = finding.verification
  const hash = verification.source_content_hash
  const context = verification.evidence_context?.trim() ?? ''
  const quote = finding.evidence_quote.trim()
  return (
    <article className="evidence-card">
      <p className="evidence-claim">{finding.statement}</p>
      {context ? (
        <figure className="evidence-context">
          <figcaption>
            <span>检索快照上下文</span>
            <small>程序截取</small>
          </figcaption>
          <blockquote cite={finding.source_url}>{highlightedContext(context, quote)}</blockquote>
        </figure>
      ) : (
        <figure className="evidence-context legacy">
          <figcaption>
            <span>已验证摘录</span>
            <small>旧记录未保存上下文</small>
          </figcaption>
          <blockquote cite={finding.source_url}>{quote || '未记录原文摘录'}</blockquote>
        </figure>
      )}
      <VerificationBadges v={verification} />
      {hash && (
        <span className="evidence-hash" title={`检索快照内容哈希：${hash}`}>
          <AppIcon name="braces" size={12} aria-hidden="true" />
          hash {shortHash(hash)}…
        </span>
      )}
      <ConflictLinks finding={finding} allFindings={allFindings} />
    </article>
  )
}

export default function EvidencePanel({
  id,
  citation,
  url,
  findings,
  allFindings,
  onClose,
}: {
  id: string
  citation: number
  url: string
  findings: Finding[]
  allFindings: Finding[]
  onClose: () => void
}) {
  const bodyRef = useRef<HTMLDivElement>(null)
  const sourceTitle = findings.find((finding) => finding.verification.source_title)?.verification
    .source_title

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = 0
  }, [citation, url])

  return (
    <aside id={id} className="evidence-drawer" role="dialog" aria-label={`引用 ${citation} 的证据`}>
      <div className="evidence-drawer-inner">
        <div className="evidence-drawer-head" aria-live="polite">
          <div className="evidence-drawer-title">
            <span className="cite-ref inert">[{citation}]</span>
            <div className="evidence-source-meta">
              {sourceTitle && <strong title={sourceTitle}>{sourceTitle}</strong>}
              <a
                className="evidence-source"
                href={url}
                target="_blank"
                rel="noreferrer"
                title={url}
              >
                {hostOf(url)}
                <AppIcon name="external" size={12} aria-hidden="true" />
              </a>
            </div>
          </div>
          <button
            type="button"
            className="btn ghost sm"
            onClick={onClose}
            aria-label="关闭证据侧栏"
          >
            <AppIcon name="x" size={14} aria-hidden="true" />
          </button>
        </div>
        <div className="evidence-drawer-body" ref={bodyRef}>
          <p className="evidence-snapshot-note">
            展示的是检索服务返回的快照上下文，不等同于完整网页正文或事实已获证实。
            {findings.length > 1 &&
              ` 当前引用按来源关联，共 ${findings.length} 条证据记录，尚非正文句子级一一映射。`}
          </p>
          {findings.length === 0 ? (
            <p className="muted small">该来源暂无结构化证据记录。</p>
          ) : (
            findings.map((f, i) => (
              <EvidenceCard
                key={f.verification.claim_id || `${f.source_url}-${i}`}
                finding={f}
                allFindings={allFindings}
              />
            ))
          )}
        </div>
      </div>
    </aside>
  )
}
