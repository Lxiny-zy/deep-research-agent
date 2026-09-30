import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AppIcon } from './AppIcon'
import type { QaEvidence, QaMessage, QaOrigin } from '../types'

const THOUGHT_LABEL: Record<string, string> = {
  rewrite: '检索式',
  paper_read: '查阅本论文',
  search_and_verify: '检索与逐字核验',
  citation_check: '引用复核',
}

const ORIGIN_ORDER: QaOrigin[] = ['paper', 'library', 'web']
const ORIGIN_LABEL: Record<QaOrigin, string> = {
  paper: '本论文',
  library: '资料库',
  web: '联网检索',
}

interface CitationItem {
  url: string
  number: number
  evidence?: QaEvidence
}

function citationLabel(item: CitationItem): string {
  return item.evidence?.source_reference || item.evidence?.source_title || item.url
}

function CitationList({
  items,
  label,
  onLocate,
}: {
  items: CitationItem[]
  label: string
  onLocate?: (evidence: QaEvidence) => void
}) {
  return (
    <ol className="qa-citations" aria-label={label}>
      {items.map((item) => (
        <li key={item.url} value={item.number}>
          {onLocate && item.evidence?.origin === 'paper' ? (
            <button
              type="button"
              className="qa-citation-locate"
              title="在右侧原文中定位这段话"
              onClick={() => item.evidence && onLocate(item.evidence)}
            >
              {citationLabel(item)}
              <AppIcon name="arrow-right" size={12} aria-hidden="true" />
            </button>
          ) : (
            <a href={item.url} target="_blank" rel="noopener noreferrer">
              {citationLabel(item)}
            </a>
          )}
        </li>
      ))}
    </ol>
  )
}

/**
 * 一轮问答。精读对话的证据带 origin，引用按「本论文 / 资料库 / 联网检索」分组，
 * 本论文的引用点击后交给 onLocate 在原文中定位；普通问答保持单一列表。
 */
export default function QaMessageView({
  message,
  onLocate,
}: {
  message: QaMessage
  onLocate?: (evidence: QaEvidence) => void
}) {
  const items: CitationItem[] = message.citations.map((url, index) => ({
    url,
    number: index + 1,
    evidence: message.evidence.find((item) => item.source_url === url),
  }))
  const grouped = items.some((item) => item.evidence?.origin)
  return (
    <article className="qa-turn" aria-label={`第 ${message.position + 1} 轮问答`}>
      <div className="qa-question">
        <p>{message.query}</p>
      </div>
      <div className="qa-answer-row">
        <span className="qa-avatar" aria-hidden="true">
          <AppIcon name="network" size={14} strokeWidth={2} />
        </span>
        <div className={`qa-answer${message.status === 'fallback' ? ' is-fallback' : ''}`}>
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
              {message.answer}
            </ReactMarkdown>
          </div>
          {items.length > 0 && !grouped && (
            <div className="qa-sources">
              <span className="qa-sources-title">引用来源</span>
              <CitationList items={items} label="引用来源" />
            </div>
          )}
          {grouped &&
            ORIGIN_ORDER.map((origin) => {
              const group = items.filter((item) => (item.evidence?.origin ?? 'web') === origin)
              if (!group.length) return null
              return (
                <div key={origin} className={`qa-sources is-${origin}`}>
                  <span className="qa-sources-title">{ORIGIN_LABEL[origin]}</span>
                  <CitationList
                    items={group}
                    label={`${ORIGIN_LABEL[origin]}引用`}
                    onLocate={onLocate}
                  />
                </div>
              )
            })}
          {message.thoughts.length > 0 && (
            <details className="qa-thoughts">
              <summary>
                <AppIcon name="chevron-right" size={13} aria-hidden="true" />
                检索与核验过程
              </summary>
              <ol>
                {message.thoughts.map((thought, index) => (
                  <li key={index}>
                    <strong>{THOUGHT_LABEL[thought.tool] ?? thought.tool}</strong>
                    <span>{thought.observation}</span>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </div>
      </div>
    </article>
  )
}
