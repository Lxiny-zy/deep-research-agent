import type { ReactNode } from 'react'
import { PAGE_VOICE } from '../lib/pageVoice'
import type { SpectrumKey } from '../lib/spectralLines'

/**
 * 页头：页名退为小字标签，题句作视觉标题，下面一行白话说明用途。
 * 题句集中在 lib/pageVoice，保证全站一页一句、口吻一致；引用原句时注明出处。
 */
export default function PageHeader({
  page,
  title,
  children,
  actions,
}: {
  page: SpectrumKey
  title: string
  children: ReactNode
  actions?: ReactNode
}) {
  const voice = PAGE_VOICE[page]
  return (
    <header className="page-header">
      <div>
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
