import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from 'react'
import { AppIcon } from './AppIcon'
import IntroHalo from './IntroHalo'

const LEAVE_MS = 1100

/**
 * 欢迎界面：一枚缓慢旋转、随指针偏转的虹彩光环，中心是「进入」。
 * 环外一圈光谱随节拍律动、朝指针隆起，并随节拍向外荡出涟漪。
 * 点击后光环向外扩散铺满屏幕、画面溶解，随后露出主界面（或登录框）。
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
  const root = useRef<HTMLDivElement>(null)
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

  const track = (event: PointerEvent<HTMLDivElement>) => {
    const el = root.current
    if (!el || leaving || event.pointerType === 'touch' ||
        window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const x = (event.clientX / window.innerWidth) * 2 - 1
    const y = (event.clientY / window.innerHeight) * 2 - 1
    el.style.setProperty('--px', x.toFixed(3))
    el.style.setProperty('--py', y.toFixed(3))
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
      ref={root}
      className={'intro intro-cinematic' + (leaving ? ' is-leaving' : '')}
      onPointerMove={track}
      onPointerLeave={() => {
        root.current?.style.setProperty('--px', '0')
        root.current?.style.setProperty('--py', '0')
      }}
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget && event.animationName === 'intro-portal-leave') finish()
      }}
      data-testid="welcome"
    >
      <div className="intro-observatory" aria-hidden="true">
        <div className="intro-coordinate-grid" />
        {Array.from({ length: 18 }, (_, index) => (
          <i className="intro-star" key={index} style={{
            '--star-x': `${(index * 37 + 11) % 96 + 2}%`,
            '--star-y': `${(index * 23 + 7) % 90 + 5}%`,
            '--star-delay': `${index * 65}ms`,
          } as CSSProperties} />
        ))}
      </div>
      <div className="intro-prism" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>

      <header className="intro-top">
        <span className="intro-kicker">From questions to a brighter tomorrow</span>
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

      <main className="intro-stage">
        <span className="intro-eyebrow">每一个问题，都是新的起点</span>
        <h1 className="intro-brand">
          Science <em>Research</em>
          <span>科研工作台</span>
        </h1>
        <div className="intro-orbit-stage" aria-hidden="true">
          <span className="intro-orbit intro-orbit-one" />
          <span className="intro-orbit intro-orbit-two" />
          <span className="intro-axis intro-axis-x" />
          <span className="intro-axis intro-axis-y" />
          <div className="intro-ring">
            <IntroHalo hot={hot} leaving={leaving} dark={dark} />
            <i className="intro-ring-wave" />
            <i className="intro-ring-wave" />
            <i className="intro-ring-glow" />
            <i className="intro-ring-line" />
            <i className="intro-ring-shine" />
          </div>
        </div>
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
          <span className="intro-enter-en">Enter</span>
        </button>
      </main>

      <footer className="intro-foot">
        <span>Science illumines a more open tomorrow.</span>
        {onTour && (
          <button type="button" className="intro-link" onClick={onTour} disabled={leaving}>
            使用引导
          </button>
        )}
      </footer>
    </div>
  )
}
