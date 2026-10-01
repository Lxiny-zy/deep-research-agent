import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CITE_HREF_PREFIX, remarkCitations } from '../lib/evidence'
import type { QaEvidence } from '../types'

const ORIGINS = { paper: '本论文', library: '资料库', web: '联网来源' }

function words(text: string): Set<string> {
  return new Set(
    text
      .replace(/\[\d+(?:[,，]\s*\d+)*\]/g, '')
      .toLowerCase()
      .match(/[a-z0-9]+|[\u3400-\u9fff]/g) ?? [],
  )
}

function evidenceForParagraph(items: QaEvidence[], paragraph: string): QaEvidence | undefined {
  const tokens = words(paragraph)
  let best = items[0]
  let score = 0
  for (const item of items) {
    const claim = words(item.statement)
    const overlap = [...claim].filter((word) => tokens.has(word)).length / Math.max(1, claim.size)
    if (overlap > score) {
      best = item
      score = overlap
    }
  }
  return best
}

interface CitationContextValue {
  citations: string[]
  evidence: QaEvidence[]
  onLocate?: (item: QaEvidence) => void
  streaming: boolean
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
    const { citations, evidence, onLocate, streaming } = useContext(CitationContext)
    if (!href?.startsWith(CITE_HREF_PREFIX))
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      )
    const number = Number(href.slice(CITE_HREF_PREFIX.length))
    const url = citations[number - 1]
    const candidates = evidence.filter((item) => item.source_url === url)
    const source = candidates[0]
    if (streaming || !url) return <span className="qa-inline-cite is-unavailable">{children}</span>
    const label = source?.source_reference || source?.source_title || url
    const origin = source?.origin ? ORIGINS[source.origin] : '来源'
    if (onLocate && source?.origin === 'paper' && source.evidence_quote.trim()) {
      return (
        <button
          type="button"
          className="qa-inline-cite"
          aria-label={`定位引用 ${number} 的论文依据`}
          title={`${origin} · ${label}`}
          onClick={(event) => {
            const paragraph = event.currentTarget.closest('p, li, td, th')?.textContent ?? ''
            const target = evidenceForParagraph(candidates, paragraph)
            if (target) onLocate(target)
          }}
        >
          {children}
        </button>
      )
    }
    if (!/^https?:\/\//i.test(url) || url.startsWith('https://workspace.invalid/')) {
      return (
        <span className="qa-inline-cite is-unavailable" title={label}>
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
        title={`${origin} · ${label}`}
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
}: {
  text: string
  citations?: string[]
  evidence?: QaEvidence[]
  onLocate?: (item: QaEvidence) => void
  streaming?: boolean
}) {
  return (
    <CitationContext.Provider value={{ citations, evidence, onLocate, streaming }}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkCitations]} components={components} skipHtml>
        {text}
      </ReactMarkdown>
    </CitationContext.Provider>
  )
}
import { createContext, useContext } from 'react'
