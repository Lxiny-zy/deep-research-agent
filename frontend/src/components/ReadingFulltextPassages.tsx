import { canLocateReadingAnchor, type ReadingFulltextPassage } from '../api/readingMap'

const statuses: Record<string, string> = {
  absence_confirmed: '已完成未见信息的范围核查',
  refuted: '发现回查反例',
  supported: '回查记录支持该判断',
  inconclusive: '回查尚无明确结论',
  insufficient: '回查材料不足',
  not_bound: '回查未绑定当前版本',
}

export default function ReadingFulltextPassages({
  status,
  passages,
  total,
  offset,
  onPage,
  onLocate,
}: {
  status?: string | null
  passages: ReadingFulltextPassage[]
  total: number
  offset: number
  onPage: (offset: number) => void
  onLocate?: (passage: ReadingFulltextPassage) => void
}) {
  if (!status) return null
  return (
    <section className="reading-fulltext stack" aria-label="全文回查片段与反例">
      <h4>全文回查片段与反例</h4>
      <p>{statuses[status] || status}</p>
      <p className="hint">
        这些片段来自已保存的全文回查，适用范围为取得并核查的材料；回查判断与逐段支持状态分别显示。
      </p>
      {passages.length === 0 && <p className="hint">本条回查记录没有逐字片段，不生成推测引文。</p>}
      {passages.map((passage, index) => (
        <article
          key={`${passage.source_hash}:${passage.start}:${passage.end}`}
          className="reading-fulltext-passage"
        >
          <strong>
            {passage.verdict === 'refutes' ? '回查反例' : '回查片段'} {offset + index + 1}
          </strong>
          <p className="hint">
            {passage.locator}
            {passage.page_hint ? ` · 页码提示 ${passage.page_hint}` : ''}
          </p>
          <blockquote>{passage.quote}</blockquote>
          {onLocate && canLocateReadingAnchor(passage) && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => onLocate(passage)}
            >
              定位回查片段
            </button>
          )}
          {(passage.quote_redacted || passage.quote_truncated) && (
            <p className="hint">此片段已遮盖或截断，不能精确定位。</p>
          )}
        </article>
      ))}
      {total > 8 && (
        <div className="row">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={offset === 0}
            onClick={() => onPage(Math.max(0, offset - 8))}
          >
            上一页回查片段
          </button>
          <span className="hint">共 {total} 条回查片段</span>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={offset + 8 >= total}
            onClick={() => onPage(offset + 8)}
          >
            下一页回查片段
          </button>
        </div>
      )}
    </section>
  )
}
