import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useDialogFocus } from '../hooks/useDialogFocus'
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

const CORROBORATION_BADGE: Record<
  Finding['verification']['corroboration_status'],
  { label: string; cls: string }
> = {
  not_checked: { label: '交叉印证未检查', cls: 'warning' },
  single_source: { label: '单一来源', cls: 'warning' },
  corroborated: { label: '已交叉印证', cls: 'success' },
  disputed: { label: '来源存在争议', cls: 'error' },
}

function corroborationExplanation(v: Finding['verification']): string {
  const status = v.corroboration_status ?? 'not_checked'
  const sourceCount = Math.max(0, v.independent_source_count ?? 0)
  const fallback =
    status === 'single_source'
      ? '当前只有一个独立来源，尚未达到双源交叉印证要求。'
      : status === 'corroborated'
        ? `该论断已获得 ${sourceCount} 个独立来源支持。`
        : status === 'disputed'
          ? '独立来源对该论断存在分歧，请结合各来源原文复核。'
          : '尚未执行独立来源交叉印证。'
  const reason = v.corroboration_reason?.trim()
  if (status === 'disputed') return fallback
  if (
    !reason ||
    reason === 'no_independent_corroboration' ||
    reason === 'independent_sources_corroborate_claim' ||
    reason === 'contradiction_detected'
  ) {
    return fallback
  }
  return reason
}

function VerificationBadges({ v }: { v: Finding['verification'] }) {
  const semantic = SEMANTIC_BADGE[v.semantic_status]
  const corroborationStatus = v.corroboration_status ?? 'not_checked'
  const corroboration = CORROBORATION_BADGE[corroborationStatus]
  const sourceCount = Math.max(0, v.independent_source_count ?? 0)
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
      <span className={`badge ${corroboration.cls}`} title={corroborationExplanation(v)}>
        <AppIcon
          name={corroborationStatus === 'disputed' ? 'alert' : 'merge'}
          size={12}
          aria-hidden="true"
        />
        {corroboration.label}
        {corroborationStatus !== 'not_checked' && ` · ${sourceCount} 个独立来源`}
      </span>
      {v.consistency_status === 'conflicted' && (
        <span className="badge error" title={v.contradiction_reason || undefined}>
          存在冲突
          <span className="sr-only">conflicted</span>
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

function CorroborationLinks({
  finding,
  allFindings,
}: {
  finding: Finding
  allFindings: Finding[]
}) {
  const v = finding.verification
  const status = v.corroboration_status ?? 'not_checked'
  const claimIds = v.corroborates_claim_ids ?? []
  if (status === 'not_checked' && claimIds.length === 0 && !v.corroboration_reason) return null
  if (status === 'disputed' && claimIds.length === 0) return null

  return (
    <div className={`evidence-corroboration ${status}`}>
      <span className="evidence-corroboration-title">
        <AppIcon name={status === 'disputed' ? 'alert' : 'merge'} size={13} aria-hidden="true" />
        {status === 'disputed' ? '争议来源关联' : '佐证来源关联'}
      </span>
      <span>{corroborationExplanation(v)}</span>
      {claimIds.map((claimId) => {
        const other = findingByClaimId(allFindings, claimId)
        if (!other) {
          return (
            <div className="evidence-corroboration-claim" key={claimId}>
              <span className="muted small">claim {claimId}（不在本次报告素材内）</span>
            </div>
          )
        }
        const sourceTitle = other.verification.source_title?.trim()
        const sourceLabel = sourceTitle
          ? `${sourceTitle} · ${hostOf(other.source_url)}`
          : hostOf(other.source_url)
        return (
          <div className="evidence-corroboration-claim" key={claimId}>
            <span>{other.statement}</span>
            <a
              className="evidence-source"
              href={other.source_url}
              target="_blank"
              rel="noreferrer"
              title={other.source_url}
              aria-label={`打开佐证来源：${sourceLabel}`}
            >
              {sourceLabel}
              <AppIcon name="external" size={11} aria-hidden="true" />
            </a>
          </div>
        )
      })}
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
  const [copyStatus, setCopyStatus] = useState('')
  async function copyEvidence() {
    try {
      await navigator.clipboard.writeText(
        [finding.statement, quote, finding.source_url].filter(Boolean).join('\n\n'),
      )
      setCopyStatus('已复制证据与来源')
    } catch {
      setCopyStatus('复制失败，请重试或手动选择文本')
    }
  }
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
            <span>{verification.status === 'verified' ? '已验证摘录' : '未验证摘录'}</span>
            <small>旧记录未保存上下文</small>
          </figcaption>
          <blockquote cite={finding.source_url}>{quote || '未记录原文摘录'}</blockquote>
        </figure>
      )}
      {(verification.quantity_label ||
        verification.conditions_label ||
        (verification.quantity_status && verification.quantity_status !== 'not_applicable')) && (
        <div className="evidence-structured-facts">
          {verification.quantity_label && (
            <p>
              <strong>数值：</strong>
              {verification.quantity_label}
            </p>
          )}
          {verification.conditions_label && (
            <p>
              <strong>成立条件：</strong>
              {verification.conditions_label}
            </p>
          )}
          {verification.quantity_status && verification.quantity_status !== 'not_applicable' && (
            <p className="muted small">
              <strong>数值校验：</strong>
              {verification.quantity_status === 'verified' ? '已在原文中核对' : '未通过原文核对'}
              {verification.quantity_reason && `（${verification.quantity_reason}）`}
            </p>
          )}
        </div>
      )}
      <VerificationBadges v={verification} />
      <details className="verification-details">
        <summary>
          验证详情
          <AppIcon name="chevron-down" size={14} aria-hidden="true" />
        </summary>
        <dl>
          <dt>原文核对</dt>
          <dd>
            {verification.reason ||
              (verification.status === 'verified'
                ? '摘录已匹配检索快照，不等同事实已证实。'
                : '未通过原文核对。')}
          </dd>
          <dt>语义判断</dt>
          <dd>
            模型结果：{verification.semantic_reason || SEMANTIC_BADGE[verification.semantic_status].label}
          </dd>
          {verification.semantic_status !== 'not_checked' && (
            <>
              <dt>模型置信度</dt>
              <dd>{Math.round(verification.semantic_confidence * 100)}%</dd>
            </>
          )}
          <dt>交叉印证</dt>
          <dd>详情：{corroborationExplanation(verification)}</dd>
          {verification.contradiction_reason && (
            <>
              <dt>一致性说明</dt>
              <dd>详情：{verification.contradiction_reason}</dd>
            </>
          )}
          {hash && (
            <>
              <dt>快照指纹</dt>
              <dd>
                <code>{hash}</code>
              </dd>
            </>
          )}
        </dl>
      </details>
      {hash && (
        <span className="evidence-hash" title={`检索快照内容哈希：${hash}`}>
          <AppIcon name="braces" size={12} aria-hidden="true" />
          hash {shortHash(hash)}…
        </span>
      )}
      <CorroborationLinks finding={finding} allFindings={allFindings} />
      <ConflictLinks finding={finding} allFindings={allFindings} />
      <div className="evidence-copy-row">
        <button type="button" className="btn btn-ghost btn-sm" onClick={copyEvidence}>
          <AppIcon
            name={copyStatus.startsWith('已') ? 'check' : 'copy'}
            size={14}
            aria-hidden="true"
          />
          复制证据
        </button>
        <span role="status">{copyStatus}</span>
      </div>
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
  sources,
  onSelect,
}: {
  id: string
  citation: number
  url: string
  findings: Finding[]
  allFindings: Finding[]
  onClose: () => void
  sources: { n: number; url: string }[]
  onSelect: (citation: number) => void
}) {
  const bodyRef = useRef<HTMLDivElement>(null)
  const dialogRef = useDialogFocus(onClose)
  const sourceIndex = sources.findIndex((source) => source.n === citation)
  const sourceTitle = findings.find((finding) => finding.verification.source_title)?.verification
    .source_title

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = 0
  }, [citation, url])

  return createPortal(
    <div className="evidence-overlay">
      <div className="evidence-backdrop" onClick={onClose} aria-hidden="true" />
      <aside
        ref={dialogRef}
        tabIndex={-1}
        id={id}
        className="evidence-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`引用 ${citation} 的证据`}
      >
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
              title="关闭证据侧栏"
            >
              <AppIcon name="x" size={14} aria-hidden="true" />
            </button>
          </div>
          <div className="evidence-navigation">
            <button
              type="button"
              className="btn btn-ghost icon-button"
              title="上一个来源"
              aria-label="上一个来源"
              disabled={sourceIndex <= 0}
              onClick={() => onSelect(sources[sourceIndex - 1].n)}
            >
              <AppIcon name="arrow-left" size={16} aria-hidden="true" />
            </button>
            <select
              className="input"
              aria-label="引用来源"
              value={citation}
              onChange={(event) => onSelect(Number(event.target.value))}
            >
              {sources.map((source) => (
                <option key={source.n} value={source.n}>
                  [{source.n}] {hostOf(source.url)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-ghost icon-button"
              title="下一个来源"
              aria-label="下一个来源"
              disabled={sourceIndex >= sources.length - 1}
              onClick={() => onSelect(sources[sourceIndex + 1].n)}
            >
              <AppIcon name="arrow-right" size={16} aria-hidden="true" />
            </button>
            <span>
              {sourceIndex + 1} / {sources.length}
            </span>
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
    </div>,
    document.body,
  )
}
