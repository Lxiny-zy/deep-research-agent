import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import Markdown from 'react-markdown'
import ReadingMeasurement from './ReadingMeasurement'
import ReadingFulltextPassages from './ReadingFulltextPassages'
import remarkGfm from 'remark-gfm'
import { AcceptanceError } from '../api/acceptance'
import {
  canLocateReadingAnchor,
  getReadingMap,
  getReadingUnit,
  type ReadingAnchor,
  type ReadingPdfTarget,
  type ReadingFulltextPassage,
} from '../api/readingMap'
import { mathRemarkPlugins, mathRehypePlugins, normalizeMathMarkdown } from '../lib/scientificMath'

const labels: Record<string, string> = {
  supported: '有依据支持',
  unsupported: '依据不足',
  not_checked: '未逐段检查',
  not_bound: '未绑定当前版本',
  covered: '已覆盖',
  missing: '未覆盖',
  partial: '部分覆盖',
  insufficient: '依据不足',
  ambiguous: '有多个候选位置',
  located: '已匹配原文',
  not_located: '未找到可靠原文位置',
  critical: '关键问题',
  major: '主要问题',
  minor: '一般问题',
  strength: '优点',
  weakness: '不足',
  comment: '建议',
  recommendation: '总体意见',
}
const label = (value?: string | null) => (value ? labels[value] || value : '未知')
const errorText = (value: unknown) =>
  value instanceof AcceptanceError && value.status === 409
    ? '文档版本已变化，请重新加载报告后查看。'
    : value instanceof Error
      ? value.message
      : '读取失败，请重试。'
function sourceLink(value: string) {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      !url.hostname.endsWith('.invalid')
      ? url.href
      : null
  } catch {
    return null
  }
}

export default function ReadingMapPanel({
  runId,
  documentVersion,
  includeHsiTables = false,
  embedded = false,
  active = true,
  initialUnitId = '',
  onLocate,
  onClearLocate,
  onRefreshVersion,
}: {
  runId: string
  documentVersion: string
  includeHsiTables?: boolean
  embedded?: boolean
  active?: boolean
  initialUnitId?: string
  onLocate?: (anchor: ReadingPdfTarget, unitId: string) => void
  onClearLocate?: () => void
  onRefreshVersion: () => void
}) {
  const [open, setOpen] = useState(embedded)
  const [offset, setOffset] = useState(0)
  const [unitId, setUnitId] = useState(initialUnitId)
  const [textOffset, setTextOffset] = useState(0)
  const [anchorOffset, setAnchorOffset] = useState(0)
  const [fulltextOffset, setFulltextOffset] = useState(0)
  const clear = useRef(onClearLocate)
  const navigationEpoch = useRef(0)
  clear.current = onClearLocate
  useEffect(() => {
    if (!open || !active) navigationEpoch.current += 1
  }, [open, active])
  useEffect(() => {
    navigationEpoch.current += 1
    setOffset(0)
    setUnitId(initialUnitId)
    setTextOffset(0)
    setAnchorOffset(0)
    setFulltextOffset(0)
    clear.current?.()
    return () => {
      navigationEpoch.current += 1
    }
  }, [documentVersion, initialUnitId, includeHsiTables])
  const map = useQuery({
    queryKey: ['reading-map', runId, documentVersion, includeHsiTables, offset],
    queryFn: ({ signal }) =>
      getReadingMap(runId, documentVersion, includeHsiTables, offset, signal),
    enabled: open && active,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  const detail = useQuery({
    queryKey: [
      'reading-unit',
      runId,
      documentVersion,
      includeHsiTables,
      unitId,
      textOffset,
      anchorOffset,
      fulltextOffset,
    ],
    queryFn: ({ signal }) =>
      getReadingUnit(
        runId,
        documentVersion,
        includeHsiTables,
        unitId,
        textOffset,
        anchorOffset,
        fulltextOffset,
        signal,
      ),
    enabled: open && active && Boolean(unitId),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  const choose = (id: string) => {
    navigationEpoch.current += 1
    if (id && id === unitId) void detail.refetch()
    setUnitId(id)
    setTextOffset(0)
    setAnchorOffset(0)
    setFulltextOffset(0)
    clear.current?.()
  }
  async function locate(anchor: ReadingAnchor) {
    const epoch = navigationEpoch.current
    try {
      const result = await detail.refetch()
      if (
        epoch !== navigationEpoch.current ||
        result.error ||
        result.data?.document_version !== documentVersion
      )
        return
      const current = result.data.anchors.find(
        (item) =>
          item.id === anchor.id &&
          item.quote === anchor.quote &&
          item.document_id === anchor.document_id,
      )
      if (current && canLocateReadingAnchor(current)) onLocate?.(current, unitId)
    } catch {
      /* An aborted query cannot trigger a late navigation. */
    }
  }
  async function locateFulltext(passage: ReadingFulltextPassage) {
    const epoch = navigationEpoch.current
    try {
      const result = await detail.refetch()
      if (
        epoch !== navigationEpoch.current ||
        result.error ||
        result.data?.document_version !== documentVersion
      )
        return
      const current = result.data.fulltext_passages?.find(
        (item) =>
          item.source_hash === passage.source_hash &&
          item.start === passage.start &&
          item.end === passage.end &&
          item.quote === passage.quote &&
          item.document_id === passage.document_id,
      )
      if (current && canLocateReadingAnchor(current)) onLocate?.(current, unitId)
    } catch {
      /* A failed recheck cannot reuse an old full-text location. */
    }
  }
  const page = (next: number) => {
    setOffset(next)
    choose('')
  }
  const versionConflict = [map.error, detail.error].some(
    (cause) => cause instanceof AcceptanceError && cause.status === 409,
  )
  const content = (
    <div className="reading-map-body stack">
      <p className="hint">
        文档版本 {documentVersion.slice(0, 12)} · 选择段落或关注点，再逐条核对原文依据。
      </p>
      {map.isLoading && <p role="status">正在读取阅读导览…</p>}
      {(map.error || detail.error) && (
        <p role="alert" className="error-text">
          {errorText(map.error || detail.error)}
          <button className="btn btn-secondary btn-sm" type="button" onClick={onRefreshVersion}>
            重新加载报告
          </button>
        </p>
      )}
      {map.data && !versionConflict && (
        <>
          <p className="reading-map-review" role="note">
            {map.data.review_bound
              ? '当前版本有逐段审核记录，请查看各段状态和待处理项。'
              : '当前版本没有可用的逐段审核绑定，以下仅作阅读导航。'}
          </p>
          {map.data.review_issues.length > 0 && (
            <ul>
              {map.data.review_issues.map((issue, index) => (
                <li key={index}>{issue}</li>
              ))}
            </ul>
          )}
          {map.data.peer_review && (
            <section className="reading-peer-summary" aria-label="评审评分与覆盖">
              <h3>评审意见与评分</h3>
              <p>
                {typeof map.data.peer_review.score === 'number'
                  ? `评分：${map.data.peer_review.score}`
                  : '没有当前版本可核对的评分'}{' '}
                · {label(map.data.peer_review.score_status)}
              </p>
              <p className="hint">
                {map.data.peer_review.scoring_note ||
                  '评分属于评审判断，请结合原文和具体意见核对。'}
              </p>
              {map.data.peer_review.coverage_issues.map((issue, index) => (
                <p key={index}>{issue}</p>
              ))}
              {map.data.peer_review.coverage?.length ? (
                <details>
                  <summary>章节阅读覆盖</summary>
                  <ul>
                    {map.data.peer_review.coverage.map((section) => (
                      <li key={section.id}>
                        {section.title} · {label(section.status)}
                        {section.missing_topics.length > 0 && (
                          <span>；待核对：{section.missing_topics.join('、')}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </section>
          )}
          {map.data.focus_items.length > 0 && (
            <details className="reading-focus">
              <summary>任务关注点（{map.data.focus_total}）</summary>
              <ul>
                {map.data.focus_items.map((focus) => (
                  <li key={focus.id}>
                    <span>
                      {focus.label} · {label(focus.status)}
                    </span>
                    <div className="row">
                      {focus.location_ids.length ? (
                        focus.location_ids.map((id, index) => (
                          <button
                            className="btn btn-ghost btn-sm"
                            type="button"
                            key={id}
                            onClick={() => choose(id)}
                          >
                            查看对应位置 {index + 1}
                          </button>
                        ))
                      ) : (
                        <span className="hint">暂无已对应的正文位置</span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
              {map.data.focus_truncated && <p className="hint">这里只显示部分关注点。</p>}
            </details>
          )}
          <ul className="reading-map-units" aria-label="正文解释卡">
            {map.data.units.map((unit, index) => (
              <li key={unit.id}>
                <button
                  type="button"
                  className="reading-unit-button"
                  aria-pressed={unitId === unit.id}
                  onClick={() => choose(unit.id)}
                >
                  <strong>
                    {offset + index + 1}.{' '}
                    {unit.section ||
                      { formula: '公式说明', figure: '图形说明', prose: '正文段落' }[unit.kind] ||
                      '正文位置'}
                  </strong>
                  <span>{unit.preview}</span>
                  <small>
                    {label(unit.verification_status)} · {unit.evidence_count} 条核验选用依据
                    {unit.peer_type ? ` · ${label(unit.peer_type)}` : ''}
                    {unit.severity ? ` · ${label(unit.severity)}` : ''}
                  </small>
                </button>
              </li>
            ))}
          </ul>
          {map.data.total === 0 && <p className="hint">本版本没有可显示的正文解释卡。</p>}
          <div className="row">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={offset === 0 || map.isFetching}
              onClick={() => page(Math.max(0, offset - 20))}
            >
              上一页段落
            </button>
            <span className="hint">
              {map.data.total ? offset + 1 : 0}–
              {Math.min(offset + map.data.units.length, map.data.total)} / {map.data.total}
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={offset + map.data.units.length >= map.data.total || map.isFetching}
              onClick={() => page(offset + 20)}
            >
              下一页段落
            </button>
          </div>
        </>
      )}
      {detail.isFetching && <p role="status">正在读取所选段落与依据…</p>}
      {detail.data && !detail.isFetching && !detail.error && !map.error && (
        <section className="reading-unit-detail stack" aria-label="所选段落与原文依据">
          <h3>
            {detail.data.unit.section || '所选正文位置'} ·{' '}
            {label(detail.data.unit.verification_status)}
          </h3>
          {detail.data.unit.kind === 'coverage_region' && (
            <p className="hint">这是任务要求对应的文本范围，不表示其中每句已经审核。</p>
          )}
          <div className="markdown-body">
            <Markdown
              remarkPlugins={[remarkGfm, ...mathRemarkPlugins]}
              rehypePlugins={mathRehypePlugins}
              components={{
                img: ({ alt }) => <span>{alt || '图像'}</span>,
                a: ({ children }) => <span>{children}</span>,
              }}
            >
              {normalizeMathMarkdown(detail.data.text)}
            </Markdown>
          </div>
          {detail.data.text_redacted && <p className="hint">本段含已遮盖的内容。</p>}
          <div className="row">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={textOffset === 0}
              onClick={() => setTextOffset(Math.max(0, textOffset - 1600))}
            >
              上一段文本
            </button>
            <span className="hint">
              第 {textOffset + 1}–{Math.min(textOffset + 1600, detail.data.text_total_chars)} 字 /{' '}
              {detail.data.text_total_chars}
            </span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={textOffset + 1600 >= detail.data.text_total_chars}
              onClick={() => setTextOffset(textOffset + 1600)}
            >
              后续文本
            </button>
          </div>
          {detail.data.peer_item && (
            <div className="reading-peer-item">
              <strong>
                {label(detail.data.peer_item.type)} · {label(detail.data.peer_item.severity)}
              </strong>
              {detail.data.peer_item.reason && <p>理由：{detail.data.peer_item.reason}</p>}
              {detail.data.peer_item.action && <p>修改建议：{detail.data.peer_item.action}</p>}
              {detail.data.peer_item.action_ready === false && (
                <p className="hint">这条意见尚缺明确的可执行修改说明。</p>
              )}
            </div>
          )}
          <ReadingMeasurement records={detail.data.measurement_context} />
          <ReadingFulltextPassages
            status={detail.data.fulltext_status}
            passages={detail.data.fulltext_passages || []}
            total={detail.data.fulltext_total || 0}
            offset={fulltextOffset}
            onPage={(next) => {
              navigationEpoch.current += 1
              setFulltextOffset(next)
              clear.current?.()
            }}
            onLocate={
              onLocate
                ? (passage) => {
                    void locateFulltext(passage)
                  }
                : undefined
            }
          />
          {detail.data.evidence_status_truncated && (
            <p className="hint">这里只显示部分依据匹配状态，请继续查看候选原文。</p>
          )}
          {detail.data.evidence_status.map((item) => (
            <p className="hint" key={item.evidence_id}>
              {label(item.status)}
              {item.candidates > 1 ? `（${item.candidates} 个候选，请结合上下文选择）` : ''}
            </p>
          ))}
          {detail.data.anchors.length === 0 && (
            <p className="hint">当前段落没有可可靠定位的核验依据，不自动推测原文位置。</p>
          )}
          {detail.data.anchors.map((anchor, index) => (
            <article key={anchor.id} className="reading-anchor">
              <h4>
                依据候选 {anchorOffset + index + 1}
                {anchor.page_hint ? ` · 页码提示 ${anchor.page_hint}` : ''}
              </h4>
              <p className="hint">{anchor.locator}</p>
              {anchor.context_before && (
                <p className="reading-anchor-context">{anchor.context_before}</p>
              )}
              <blockquote>{anchor.quote}</blockquote>
              {anchor.context_after && (
                <p className="reading-anchor-context">{anchor.context_after}</p>
              )}
              <div className="row">
                {onLocate && canLocateReadingAnchor(anchor) && (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => void locate(anchor)}
                  >
                    定位这条原文
                  </button>
                )}
                {sourceLink(anchor.source_url) && (
                  <a
                    href={sourceLink(anchor.source_url)!}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    查看来源
                  </a>
                )}
              </div>
              {anchor.quote_truncated && (
                <p className="hint">引文已截断，不能用这段文本精确定位。</p>
              )}
              {anchor.quote_redacted && (
                <p className="hint">引文包含遮盖内容，不能作为完整原文定位。</p>
              )}
              {(!anchor.document_id || !anchor.pdf_available) && (
                <p className="hint">未确认可用的原版 PDF，请查看来源。</p>
              )}
            </article>
          ))}
          {detail.data.anchor_total > 8 && (
            <div className="row">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={anchorOffset === 0}
                onClick={() => {
                  setAnchorOffset(Math.max(0, anchorOffset - 8))
                  clear.current?.()
                }}
              >
                上一页依据
              </button>
              <span className="hint">共 {detail.data.anchor_total} 个候选</span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={anchorOffset + 8 >= detail.data.anchor_total}
                onClick={() => {
                  setAnchorOffset(anchorOffset + 8)
                  clear.current?.()
                }}
              >
                下一页依据
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  )
  return embedded ? (
    <section className="reading-map embedded" aria-label="阅读导览与原文依据">
      {content}
    </section>
  ) : (
    <details
      className="panel reading-map"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="run-collapsible-head">
        <span className="panel-title">阅读导览与原文依据</span>
        <span className="hint">关注点、段落与评审意见</span>
      </summary>
      {open && content}
    </details>
  )
}
