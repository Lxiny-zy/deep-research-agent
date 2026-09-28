import { useRef, useState, type PointerEvent } from 'react'
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

  const track = (event: PointerEvent<HTMLDivElement>) => {
    const el = root.current
    if (!el || leaving) return
    const x = (event.clientX / window.innerWidth) * 2 - 1
    const y = (event.clientY / window.innerHeight) * 2 - 1
    el.style.setProperty('--px', x.toFixed(3))
    el.style.setProperty('--py', y.toFixed(3))
  }

  const enter = () => {
    if (leaving) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    if (reduced) return onEnter()
    setLeaving(true)
    window.setTimeout(() => {
      onEnter()
      // 游客进入后仍停在本页（上方弹出登录框），把光环恢复原状
      setLeaving(false)
    }, LEAVE_MS)
  }

  return (
    <div
      ref={root}
      className={'intro' + (leaving ? ' is-leaving' : '')}
      onPointerMove={track}
      data-testid="welcome"
    >
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
        <h1 className="intro-brand">
          Science <em>Research</em>
          <span>科研工作台</span>
        </h1>
        <div className="intro-ring" aria-hidden="true">
          <IntroHalo hot={hot} leaving={leaving} dark={dark} />
          <i className="intro-ring-wave" />
          <i className="intro-ring-wave" />
          <i className="intro-ring-glow" />
          <i className="intro-ring-line" />
          <i className="intro-ring-shine" />
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
          <button type="button" className="intro-link" onClick={onTour}>
            使用引导
          </button>
        )}
      </footer>
    </div>
  )
}
