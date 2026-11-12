import { useEffect, useRef, useState } from 'react'
import { AppIcon } from './AppIcon'
import IntroPlate from './IntroPlate'

const LEAVE_MS = 1100
/** 只收拢、不开孔（访客）时的时长：谱线收成光核后直接浮出登录框 */
const GATHER_MS = 480

/**
 * 欢迎界面：一条横贯画面的基准线是问题本身，思路线在其上依次升起；
 * 有依据处是实线，落在缺口处只留一小截；
 * 基准线以下，所有思路线弯向「进入」——许多条思路，最终汇成一个回答。
 * 点击后整幅谱线收拢进「进入」，再从这一点打开圆孔，露出主界面（或登录框）。
 * 明暗两套由主题令牌驱动；「减少动态效果」时跳过过渡直接进入。
 */
export default function WelcomePage({
  onEnter,
  onTour,
  onToggleTheme,
  dark = false,
  reveal = true,
  held = false,
}: {
  onEnter: () => void
  onTour?: () => void
  onToggleTheme?: (origin: { x: number; y: number }) => void
  dark?: boolean
  /** 背后是否已有工作台可露出；访客没有，只收拢谱线，不打开圆孔 */
  reveal?: boolean
  /** 停留在收拢状态（如登录框打开时），文字与谱线不再回到画面上 */
  held?: boolean
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
    timer.current = setTimeout(finish, reveal ? LEAVE_MS : GATHER_MS)
  }

  return (
    <div
      className={
        'intro intro-spectral' +
        (leaving && reveal ? ' is-leaving' : leaving || held ? ' is-gathering' : '')
      }
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget && event.animationName === 'intro-open') finish()
      }}
      data-testid="welcome"
    >
      <IntroPlate hot={hot} leaving={leaving || held} dark={dark} />

      <header className="intro-top">
        <span className="intro-kicker">
          <i aria-hidden="true" />
          Science Research · 实证研究工作台
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
          课题调研 · 文献综述 · 论文精读 · 同行评审 · 数据分析
        </p>
        <h1 className="intro-verse">
          <span>每一条科学论断，</span>
          <span>皆可回溯至原始文献。</span>
        </h1>
        <p className="intro-lede">
          拆解研究命题、并发检索学术与私域文献、逐字对勘原文快照并反思补证；经机械质量门复核后，同源凝练为多格式学术手稿。
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
        <span className="intro-enter-en">ENTER</span>
      </button>

      <footer className="intro-foot">
        <span>原文逐字核验 · 跨源独立印证 · 证据缺口留白 · 多格式同源交付</span>
        {onTour && (
          <button type="button" className="intro-link" onClick={onTour} disabled={leaving}>
            使用引导
          </button>
        )}
      </footer>
    </div>
  )
}
