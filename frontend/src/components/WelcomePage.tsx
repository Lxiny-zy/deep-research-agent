import { useRef } from 'react'
import { AppIcon } from './AppIcon'
import WelcomeTelemetrySection from './WelcomeTelemetrySection'
import { useRevealOnScroll } from '../hooks/useRevealOnScroll'

interface Props {
  onEnter: () => void
}

/** Public entry surface. The page is intentionally typographic: the product
 * is a research workspace, so the first impression is its method and rhythm.
 */
export default function WelcomePage({ onEnter }: Props) {
  const stageRef = useRef<HTMLElement>(null)
  useRevealOnScroll(stageRef)

  function revealCapabilities() {
    const target = document.getElementById('capabilities')
    if (!target) return
    const top = target.getBoundingClientRect().top + window.scrollY - 28
    const reduceMotion =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    window.scrollTo({ top, behavior: reduceMotion ? 'auto' : 'smooth' })
    target.classList.add('is-focused')
    window.setTimeout(() => target.classList.remove('is-focused'), 1300)
  }

  return (
    <main className="visitor-welcome signal-theme" ref={stageRef}>
      <header className="welcome-nav">
        <div className="welcome-brand">
          <span className="brand-icon" aria-hidden="true">
            <AppIcon name="network" size={22} />
          </span>
          <span>
            <strong>Deep Research</strong>
            <small>Multi-Agent System</small>
          </span>
        </div>
        <div className="welcome-nav-meta">
          <span>
            <i /> SYSTEM READY
          </span>
          <span>BUILT FOR CLEARER QUESTIONS</span>
        </div>
      </header>

      <section className="welcome-hero">
        <div className="welcome-topline">
          <span>DEEP RESEARCH / MULTI-AGENT</span>
          <span>RESEARCH IS A MOVING TARGET</span>
        </div>
        <div className="welcome-copy">
          <span className="welcome-kicker">
            <AppIcon name="sparkles" size={14} aria-hidden="true" />A QUIET PLACE FOR COMPLEX WORK
          </span>
          <h1>
            <span className="headline-line">
              <span className="headline-line-inner">让多个 Agent</span>
            </span>
            <span className="headline-line">
              <span className="headline-line-inner headline-accent">像团队一样研究。</span>
            </span>
          </h1>
          <p>
            把问题交给一组会规划、检索、反思和写作的 Agent。每一步都留下可追溯的证据，
            让复杂研究从黑箱变成一份可以阅读、复核和继续追问的报告。
          </p>
          <div className="welcome-actions">
            <button className="btn btn-primary btn-lg" onClick={onEnter} type="button">
              进入研究工作台
              <AppIcon name="arrow-up-right" size={17} aria-hidden="true" />
            </button>
            <button className="welcome-secondary" onClick={revealCapabilities} type="button">
              查看工作方式
              <AppIcon name="arrow-down" size={15} aria-hidden="true" />
            </button>
          </div>
          <div className="welcome-signal-line" aria-label="研究链路：规划、检索、反思、报告">
            <span>
              <AppIcon name="route" size={13} aria-hidden="true" /> 规划
            </span>
            <i />
            <span>
              <AppIcon name="search-code" size={13} aria-hidden="true" /> 检索
            </span>
            <i />
            <span>
              <AppIcon name="refresh" size={13} aria-hidden="true" /> 反思
            </span>
            <i />
            <span>
              <AppIcon name="file" size={13} aria-hidden="true" /> 报告
            </span>
          </div>
        </div>

        <aside className="welcome-orbit" aria-label="Agent research workflow">
          <span className="welcome-note-label">FIELD NOTE / 001</span>
          <p className="welcome-note-title">A readable trail from question to source.</p>
          <ol className="welcome-note-list">
            <li>
              <span>01</span>
              <span>Frame the question</span>
            </li>
            <li>
              <span>02</span>
              <span>Gather and compare</span>
            </li>
            <li>
              <span>03</span>
              <span>Write with evidence</span>
            </li>
          </ol>
        </aside>
      </section>

      <section className="welcome-capabilities" id="capabilities">
        <article data-reveal="1">
          <b>01</b>
          <strong>复杂任务研究</strong>
          <p>从问题拆解到带引用报告，保留完整研究链路和每一条来源。</p>
          <AppIcon name="file-search" size={21} aria-hidden="true" />
        </article>
        <article data-reveal="2">
          <b>02</b>
          <strong>多 Agent 编排</strong>
          <p>用可视化工作流安排串行、并行、条件和汇聚节点。</p>
          <AppIcon name="waypoints" size={21} aria-hidden="true" />
        </article>
        <article data-reveal="3">
          <b>03</b>
          <strong>模型与角色治理</strong>
          <p>统一管理模型档案、角色模板、检索密钥和运行策略。</p>
          <AppIcon name="users" size={21} aria-hidden="true" />
        </article>
      </section>
      <WelcomeTelemetrySection />
    </main>
  )
}
