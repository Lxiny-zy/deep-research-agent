import { useEffect } from 'react'
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
  { label: string; cls: string } | null
> = {
  not_checked: null,
  supported: { label: '语义支持', cls: 'success' },
  unsupported: { label: '语义不支持', cls: 'error' },
  uncertain: { label: '语义存疑', cls: 'warning' },
}

function VerificationBadges({ v }: { v: Finding['verification'] }) {
  const semantic = SEMANTIC_BADGE[v.semantic_status]
  return (
    <div className="evidence-badges">
      {v.status === 'verified' ? (
        <span className="badge success" title={v.reason || '逐字证据已通过归一化比对'}>
          <AppIcon name="shield" size={12} aria-hidden="true" /> verified
        </span>
      ) : (
        <span className="badge warning" title={v.reason || '未通过程序验证'}>
          未验证
        </span>
      )}
      {semantic && (
        <span className={`badge ${semantic.cls}`} title={v.semantic_reason || undefined}>
          {semantic.label}
        </span>
      )}
      {v.consistency_status === 'conflicted' && (
        <span className="badge error" title={v.contradiction_reason || undefined}>
          conflicted
        </span>
      )}
      {v.consistency_status === 'clear' && <span className="badge info">一致性无冲突</span>}
    </div>
  )
}

function ConflictLinks({
  finding,
  allFindings,
}: {
  finding: Finding
  allFindings: Finding[]
}) {
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

function EvidenceCard({
  finding,
  allFindings,
}: {
  finding: Finding
  allFindings: Finding[]
}) {
  const hash = finding.verification.source_content_hash
  return (
    <article className="evidence-card">
      <p className="evidence-claim">{finding.statement}</p>
      <blockquote className="evidence-quote">{finding.evidence_quote}</blockquote>
      <VerificationBadges v={finding.verification} />
      {hash && (
        <span className="evidence-hash" title={`来源内容哈希：${hash}`}>
          <AppIcon name="braces" size={12} aria-hidden="true" />
          hash {shortHash(hash)}…
        </span>
      )}
      <ConflictLinks finding={finding} allFindings={allFindings} />
    </article>
  )
}

export default function EvidencePanel({
  citation,
  url,
  findings,
  allFindings,
  onClose,
}: {
  citation: number
  url: string
  findings: Finding[]
  allFindings: Finding[]
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside className="evidence-drawer" role="dialog" aria-label={`引用 ${citation} 的证据`}>
      <div className="evidence-drawer-head">
        <div className="evidence-drawer-title">
          <span className="cite-ref inert">[{citation}]</span>
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
        <button type="button" className="btn ghost sm" onClick={onClose} aria-label="关闭证据侧栏">
          <AppIcon name="x" size={14} aria-hidden="true" />
        </button>
      </div>
      <div className="evidence-drawer-body">
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
    </aside>
  )
}
