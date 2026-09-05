import { lazy, Suspense, useRef, useState } from 'react'
import { AppIcon, type AppIconName } from './AppIcon'
import WelcomeTelemetrySection from './WelcomeTelemetrySection'
import { useRevealOnScroll } from '../hooks/useRevealOnScroll'

const ResearchField = lazy(() => import('./ResearchField'))
const stages: { icon: AppIconName; name: string; english: string; description: string }[] = [
  {
    icon: 'route',
    name: '从问题出发',
    english: 'PLAN',
    description: '明确边界，拆解值得深入的研究方向。',
  },
  {
    icon: 'search-code',
    name: '沿证据探索',
    english: 'RESEARCH',
    description: '多路检索，让每一条发现都有据可循。',
  },
  {
    icon: 'refresh',
    name: '在反思中求证',
    english: 'REFLECT',
    description: '交叉验证，回到尚未解决的关键问题。',
  },
  {
    icon: 'file',
    name: '让洞见成形',
    english: 'SYNTHESIZE',
    description: '组织证据，形成可以复核的研究报告。',
  },
]

export default function WelcomePage({
  onEnter,
  onTour,
}: {
  onEnter: () => void
  onTour?: () => void
}) {
  const pageRef = useRef<HTMLElement>(null)
  const [paused, setPaused] = useState(false)
  useRevealOnScroll(pageRef)

  return (
    <main className="research-welcome" ref={pageRef}>
      <section className="entry-scene" aria-labelledby="entry-title">
        <Suspense fallback={null}>
          <ResearchField paused={paused} />
        </Suspense>
        <header className="entry-nav">
          <a className="entry-brand" href="/welcome" aria-label="Deep Research 欢迎页">
            <AppIcon name="network" size={30} strokeWidth={1.5} aria-hidden="true" />
            <span>
              Deep Research<small>INDEPENDENT INTELLIGENCE</small>
            </span>
          </a>
          <nav aria-label="欢迎页导航">
            {onTour && (
              <button
                type="button"
                className="entry-tour-button"
                onClick={onTour}
                title="入门引导"
                aria-label="入门引导"
              >
                <AppIcon name="help" size={18} aria-hidden="true" />
              </button>
            )}
            <a href="#research-method">
              研究方法
              <AppIcon name="arrow-down" size={14} aria-hidden="true" />
            </a>
            <button type="button" onClick={onEnter}>
              进入工作台
              <AppIcon name="arrow-up-right" size={17} aria-hidden="true" />
            </button>
          </nav>
        </header>
        <div className="entry-topline">
          <span>多智能体深度研究系统</span>
          <span>QUESTION / EVIDENCE / INSIGHT</span>
        </div>
        <div className="entry-content">
          <div className="entry-edition">
            <span className="entry-status-line" />
            THE PURSUIT OF UNDERSTANDING
          </div>
          <h1 id="entry-title">
            <span>Deep</span>
            <span>
              Research<span className="entry-period">.</span>
            </span>
          </h1>
          <div className="entry-copy">
            <h2>
              循证而行，<span>深究其理。</span>
            </h2>
            <p>
              让不同的智能，汇成有据可循的洞见。
              <br />
              从第一个问题，到每一条可追溯的答案。
            </p>
            <button type="button" className="entry-cta" onClick={onEnter}>
              开启研究
              <AppIcon name="arrow-up-right" size={21} aria-hidden="true" />
            </button>
          </div>
        </div>
        <div className="entry-scene-caption" aria-hidden="true">
          <span>CONNECTED PERSPECTIVES</span>
          <span>01 / 04</span>
        </div>
        <footer className="entry-bottom">
          <a href="#research-method">
            <AppIcon name="arrow-down" size={17} aria-hidden="true" />
            <span>由问题，见全貌</span>
          </a>
          <span>一组 Agent，一条完整的证据链。</span>
          <button
            type="button"
            className="entry-motion"
            onClick={() => setPaused(!paused)}
            aria-pressed={paused}
            aria-label={paused ? '播放背景动画' : '暂停背景动画'}
            title={paused ? '播放背景动画' : '暂停背景动画'}
          >
            <AppIcon name={paused ? 'play' : 'pause'} size={16} aria-hidden="true" />
          </button>
        </footer>
      </section>

      <section className="entry-method" id="research-method" aria-labelledby="method-title">
        <header className="entry-section-heading">
          <span className="entry-section-index">01 / THE METHOD</span>
          <h2 id="method-title">理解，来自每一步的深入。</h2>
          <AppIcon name="waypoints" size={32} strokeWidth={1.3} aria-hidden="true" />
        </header>
        <div className="entry-stage-list">
          {stages.map((stage, index) => (
            <article key={stage.english} data-reveal={String(index + 1)}>
              <div className="entry-stage-top">
                <span>0{index + 1}</span>
                <AppIcon name={stage.icon} size={25} strokeWidth={1.5} aria-hidden="true" />
              </div>
              <span className="entry-stage-english">{stage.english}</span>
              <h3>{stage.name}</h3>
              <p>{stage.description}</p>
            </article>
          ))}
        </div>
      </section>
      <WelcomeTelemetrySection />
      <footer className="entry-footer">
        <span>
          <AppIcon name="network" size={19} aria-hidden="true" />
          Deep Research
        </span>
        <span>始于好问题，终于真洞见。</span>
        <button type="button" onClick={onEnter}>
          开始研究
          <AppIcon name="arrow-up-right" size={17} aria-hidden="true" />
        </button>
      </footer>
    </main>
  )
}
