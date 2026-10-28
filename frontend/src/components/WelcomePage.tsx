import { useEffect, useRef, useState } from 'react'
import { AppIcon } from './AppIcon'
import IntroSpectrum from './IntroSpectrum'

const LEAVE_MS = 1100

/**
 * 欢迎界面：一条横贯画面的光谱带，谱线在其上依次显影。
 * 带以上，谱线笔直垂下——位置由元素决定，像早已写定的命运；
 * 带以下，所有谱线弯向「进入」——许多条思路，最终汇成一个回答。
 * 点击后光谱带向上下铺满画面、随即溶解，露出主界面（或登录框）。
 * 明暗两套由主题令牌驱动；「减少动态效果」时跳过过渡直接进入。
 */
export default function WelcomePage({
  onEnter,
  onTour,
  onToggleTheme,
  dark = false,
}: {
  onEnter: () => void
  onTour?: () => void
  onToggleTheme?: (origin: { x: number; y: number }) => void
  dark?: boolean
}) {
  const [leaving, setLeaving] = useState(false)
  const [hot, setHot] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const busy = useRef(false)
  const enterCallback = useRef(onEnter)
  enterCallback.current = onEnter
  useEffect(() => () => clearTimeout(timer.current), [])

  const finish = () => {
    if (!busy.current) return
    busy.current = false
    clearTimeout(timer.current)
    setLeaving(false)
    enterCallback.current()
  }

  const enter = () => {
    if (busy.current) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    if (reduced) return onEnter()
    busy.current = true
    setLeaving(true)
    timer.current = setTimeout(finish, LEAVE_MS)
  }

  return (
    <div
      className={'intro intro-spectral' + (leaving ? ' is-leaving' : '')}
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget && event.animationName === 'intro-dissolve')
          finish()
      }}
      data-testid="welcome"
    >
      <IntroSpectrum hot={hot} leaving={leaving} dark={dark} />

      <header className="intro-top">
        <span className="intro-kicker">
          <i aria-hidden="true" />
          Science Research · 科研工作台
        </span>
        {onToggleTheme && (
          <button
            type="button"
            className="intro-icon-button"
            disabled={leaving}
            aria-label={dark ? '切换到浅色主题' : '切换到深色主题'}
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect()
              onToggleTheme({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
            }}
          >
            <AppIcon name={dark ? 'sun' : 'moon'} size={17} aria-hidden="true" />
          </button>
        )}
      </header>

      <main className="intro-copy">
        <p className="intro-eyebrow">
          1868 年的日食里，日光中多出一条无人认得的黄线。
          <br />
          二十七年后，人们才在地上找到它：氦。
        </p>
        <h1 className="intro-verse">
          <span>万物皆有谱线，</span>
          <span>答案早在光中。</span>
        </h1>
        <p className="intro-lede">
          把问题交给这里：拆成思路，逐条取证，看它在哪里亮起，又在哪里留下暗线。
        </p>
      </main>

      <button
        type="button"
        className="intro-enter"
        onClick={enter}
        disabled={leaving}
        onPointerEnter={() => setHot(true)}
        onPointerLeave={() => setHot(false)}
        onFocus={() => setHot(true)}
        onBlur={() => setHot(false)}
      >
        <span className="intro-enter-cn">进入</span>
        <span className="intro-enter-en">开始读谱</span>
      </button>

      <footer className="intro-foot">
        <span>亮线是已证之事，暗线是未解之问。</span>
        {onTour && (
          <button type="button" className="intro-link" onClick={onTour} disabled={leaving}>
            使用引导
          </button>
        )}
      </footer>
    </div>
  )
}
