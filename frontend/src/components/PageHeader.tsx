import type { ReactNode } from 'react'
import { PAGE_VOICE } from '../lib/pageVoice'
import { PAGE_META, type PageKey } from '../lib/pageMeta'

/**
 * 页头按期刊栏目排：「§ 节号 · 栏目」眉题 → 页名主标题 → 题句（引文体）→ 白话说明，底边一道细双线。
 * 题句集中在 lib/pageVoice，保证全站一页一句、口吻一致；引用原句时注明出处。
 */
export default function PageHeader({
  page,
  title,
  children,
  actions,
}: {
  page: PageKey
  title: string
  children: ReactNode
  actions?: ReactNode
}) {
  const voice = PAGE_VOICE[page]
  const meta = PAGE_META[page]
  return (
    <header className="page-header">
      <div>
        <p className="page-section" aria-hidden="true">
          § {meta.section} · {meta.stage}
        </p>
        <h1>{title}</h1>
        <p className="page-verse">
          {voice.verse}
          {voice.source && <cite className="page-verse-source">{voice.source}</cite>}
        </p>
        <p>{children}</p>
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  )
}
