import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { mathRemarkPlugins, mathRehypePlugins, normalizeMathMarkdown } from '../lib/scientificMath'
import { CITE_HREF_PREFIX, remarkCitations } from '../lib/evidence'
import { catalogForReport, citationLocations, citationOccurrence } from '../lib/bibliography'
import { useEvidenceFocus } from '../hooks/useEvidenceFocus'
import { createPortal } from 'react-dom'
import type { QaEvidence, ReportBibliography } from '../types'

const ORIGINS = { paper: '本论文', library: '资料库', web: '联网来源' }

interface CitationContextValue {
  citations: string[]
  evidence: QaEvidence[]
  onLocate?: (item: QaEvidence) => void
  streaming: boolean
  binding?: ReportBibliography
}

const CitationContext = createContext<CitationContextValue>({
  citations: [],
  evidence: [],
  streaming: false,
})

const components: Components = {
  p: ({ node, children }) => {
    const parts =
      node?.children.filter((child) => child.type !== 'text' || child.value.trim()) ?? []
    const only = parts[0]
    const heading =
      parts.length === 1 &&
      only?.type === 'element' &&
      only.tagName === 'strong' &&
      only.children.every((child) => child.type === 'text') &&
      only.children.map((child) => (child.type === 'text' ? child.value : '')).join('').length <= 40
    return heading ? <h3>{children}</h3> : <p>{children}</p>
  },
  pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
  a: function CitationLink({ href, children }) {
    const { citations, evidence, onLocate, streaming, binding } = useContext(CitationContext)
    const [choosing, setChoosing] = useState(false)
    useEffect(() => setChoosing(false), [href])
    if (!href?.startsWith(CITE_HREF_PREFIX))
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      )
    const occurrence = citationOccurrence(href, binding)
    const locations = occurrence?.locations ?? citationLocations(href)
    const number = locations[0]
    const url = citations[number - 1]
    const scoped = occurrence && occurrence.scope !== 'source_location'
    const candidates = evidence.filter(
      (item) =>
        locations.some((index) => citations[index - 1] === item.source_url) &&
        (!scoped || (item.support_id && occurrence.evidence_ids.includes(item.support_id))),
    )
    const source = candidates[0]
    if (streaming || !url) return <span className="qa-inline-cite is-unavailable">{children}</span>
    const label = source?.source_reference || source?.source_title || url
    const origin = source?.origin ? ORIGINS[source.origin] : '来源'
    if (scoped && !source)
      return (
        <span
          className="qa-inline-cite is-unavailable"
          title={
            occurrence.scope === 'unused_location'
              ? '这个位置未被本次内容核验选用'
              : '本次核验选用的摘录暂未加载'
          }
        >
          {children}
        </span>
      )
    if (onLocate && source?.origin === 'paper' && source.evidence_quote.trim()) {
      return (
        <>
          <button
            type="button"
            className="qa-inline-cite"
            aria-label={scoped ? `定位引用 ${number} 的论文依据` : `浏览引用 ${number} 的来源记录`}
            title={`${origin} · ${label}${scoped ? ' · 核验选用的摘录' : ' · 未绑定依据，浏览来源记录'}`}
            aria-expanded={choosing}
            onClick={() => {
              if (scoped) {
                if (candidates.length === 1) onLocate(candidates[0])
                else setChoosing(true)
                return
              }
              setChoosing(true)
            }}
          >
            {children}
          </button>
          {choosing && (
            <EvidenceChoice
              bound={Boolean(scoped)}
              items={candidates}
              onClose={() => setChoosing(false)}
              onLocate={(item) => {
                setChoosing(false)
                onLocate(item)
              }}
            />
          )}
        </>
      )
    }
    if (!/^https?:\/\//i.test(url) || url.startsWith('https://workspace.invalid/')) {
      return (
        <span
          className="qa-inline-cite is-unavailable"
          title={`${label}${scoped ? '' : ' · 未绑定依据'}`}
          aria-label={scoped ? undefined : `引用 ${number}：未绑定依据`}
        >
          {children}
        </span>
      )
    }
    return (
      <a
        className="qa-inline-cite"
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`查看引用 ${number}：${label}`}
        title={`${origin} · ${label}${scoped ? '' : ' · 未绑定依据，仅查看来源'}`}
      >
        {children}
      </a>
    )
  },
}

/** Stable renderers preserve text selection and focused citations during updates. */
export default function QaAnswerBody({
  text,
  citations = [],
  evidence = [],
  onLocate,
  streaming = false,
  binding,
}: {
  text: string
  citations?: string[]
  evidence?: QaEvidence[]
  onLocate?: (item: QaEvidence) => void
  streaming?: boolean
  binding?: ReportBibliography
}) {
  const catalog = useMemo(
    () => (streaming ? undefined : catalogForReport(text, citations, binding)),
    [text, citations, binding, streaming],
  )
  return (
    <CitationContext.Provider
      value={{ citations, evidence, onLocate, streaming, binding: catalog }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, ...mathRemarkPlugins, remarkCitations]}
        rehypePlugins={mathRehypePlugins}
        components={components}
        skipHtml
      >
        {normalizeMathMarkdown(catalog?.body ?? text)}
      </ReactMarkdown>
    </CitationContext.Provider>
  )
}

function EvidenceChoice({
  bound,
  items,
  onClose,
  onLocate,
}: {
  bound: boolean
  items: QaEvidence[]
  onClose: () => void
  onLocate: (item: QaEvidence) => void
}) {
  const ref = useEvidenceFocus(onClose)
  return createPortal(
    <div className="evidence-overlay">
      <aside
        className="evidence-drawer"
        role="dialog"
        aria-modal="false"
        aria-label={bound ? '选择论文依据' : '浏览来源记录'}
        tabIndex={-1}
        ref={ref}
      >
        <div className="evidence-drawer-inner">
          <div className="evidence-drawer-head">
            <h3>{bound ? '选择论文依据' : '浏览来源记录'}</h3>
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              关闭
            </button>
          </div>
          <div className="evidence-drawer-body">
            <p>
              {bound
                ? '这段内容的核验选用了多条摘录，请选择要查看的位置。'
                : '未绑定依据：以下是该来源的记录，不代表本句的核验依据。'}
            </p>
            {items.map((item, index) => (
              <article className="evidence-card" key={`${item.support_id}-${index}`}>
                <p>{item.statement}</p>
                <blockquote>{item.evidence_quote}</blockquote>
                <button type="button" className="btn btn-ghost" onClick={() => onLocate(item)}>
                  {bound ? '定位这条依据' : '定位这条记录'}
                </button>
              </article>
            ))}
          </div>
        </div>
      </aside>
    </div>,
    document.body,
  )
}
