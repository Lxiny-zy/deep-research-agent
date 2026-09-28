import { useId, useState, type ReactNode } from 'react'
import { AppIcon } from './AppIcon'

interface Props {
  /** 悬浮 / 聚焦时显示的详细说明 */
  text: ReactNode
  /** 图标按钮的可读名称，默认「说明」 */
  label?: string
  /** 让外层字段通过 aria-describedby 引用说明文本 */
  id?: string
}

/**
 * 字段说明气泡：鼠标悬浮、键盘聚焦或点击图标时显示。
 *
 * 说明文本始终渲染在 DOM 里（视觉隐藏），因此读屏软件经 aria-describedby 能读到，
 * 触屏用户点一下图标即可展开；按 Esc 收起。
 */
export default function InfoTip({ text, label = '说明', id }: Props) {
  const generated = useId()
  const tipId = id ?? `tip-${generated}`
  const [pinned, setPinned] = useState(false)
  return (
    <span
      className={'info-tip' + (pinned ? ' is-open' : '')}
      onMouseLeave={() => setPinned(false)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setPinned(false)
      }}
    >
      <button
        type="button"
        className="info-tip-trigger"
        aria-label={label}
        aria-describedby={tipId}
        aria-expanded={pinned}
        onClick={(event) => {
          event.preventDefault()
          setPinned((open) => !open)
        }}
      >
        <AppIcon name="help" size={13} aria-hidden="true" />
      </button>
      <span role="tooltip" id={tipId} className="info-tip-bubble">
        {text}
      </span>
    </span>
  )
}
