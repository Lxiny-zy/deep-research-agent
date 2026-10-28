import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AppIcon } from './AppIcon'
import { getReaderPassages } from '../api/client'
import type { PdfHighlight } from './PdfViewer'

const normalize = (text: string) => text.replace(/\s+/g, '')

/**
 * 没有原版 PDF 的文档（上传的 Markdown / 文本、粘贴的论文、只取回正文的链接）：
 * 按原文顺序显示任务里冻结的正文片段。点击证据定位时，高亮并滚动到包含引文的片段。
 */
export default function ReaderTextView({
  runId,
  documentId,
  highlight,
  note,
  url,
}: {
  runId: string
  documentId: string
  highlight: PdfHighlight | null
  note: string
  url?: string
}) {
  const passages = useQuery({
    queryKey: ['reader-passages', runId, documentId],
    queryFn: ({ signal }) => getReaderPassages(runId, documentId, signal),
    staleTime: Infinity,
  })
  const root = useRef<HTMLDivElement>(null)
  const items = passages.data?.passages ?? []
  const quote = highlight ? normalize(highlight.quote).slice(0, 60) : ''
  const hit = quote ? items.findIndex((item) => normalize(item.content).includes(quote)) : -1

  useEffect(() => {
    if (hit < 0) return
    const node = root.current?.querySelector<HTMLElement>(`[data-passage="${hit}"]`)
    node?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [hit, highlight?.token])

  if (passages.isLoading) {
    return (
      <p className="reader-note hint" role="status">
        <AppIcon name="loader" size={14} className="spin" aria-hidden="true" />
        正在读取原文…
      </p>
    )
  }
  const external = url ? (
    <a className="btn btn-secondary btn-sm" href={url} target="_blank" rel="noopener noreferrer">
      <AppIcon name="external" size={14} aria-hidden="true" />
      在原网站打开
    </a>
  ) : null
  if (passages.isError || items.length === 0) {
    return (
      <div className="reader-note">
        <p className="hint">{note || '这份论文没有可显示的原文。'}</p>
        {external}
      </div>
    )
  }
  return (
    <div className="reader-text" ref={root}>
      <div className="reader-text-note">
        <p className="hint">
          这份文档没有原版版面，以下是任务读取的正文
          {passages.data?.truncated ? '（过长，已截取前段）' : ''}。
        </p>
        {external}
      </div>
      {items.map((item, index) => (
        <section
          key={index}
          className={'reader-text-passage' + (index === hit ? ' is-hit' : '')}
          data-passage={index}
        >
          {(item.section_title || item.locator) && <h3>{item.section_title || item.locator}</h3>}
          <p>{item.content}</p>
        </section>
      ))}
    </div>
  )
}
