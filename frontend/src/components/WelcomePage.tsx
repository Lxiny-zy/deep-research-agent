import { useEffect, useRef } from 'react'
import { AppIcon } from './AppIcon'
import WelcomeTelemetrySection from './WelcomeTelemetrySection'

interface Props {
  onEnter: () => void
}

export default function WelcomePage({ onEnter }: Props) {
  const stageRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const stage = stageRef.current
    if (!stage || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    let targetX = 0
    let targetY = 0
    let currentX = 0
    let currentY = 0
    let frame = 0

    const render = () => {
      currentX += (targetX - currentX) * 0.08
      currentY += (targetY - currentY) * 0.08
      stage.style.setProperty('--pointer-x', `${currentX.toFixed(2)}px`)
      stage.style.setProperty('--pointer-y', `${currentY.toFixed(2)}px`)
      frame = window.requestAnimationFrame(render)
    }
    const onPointerMove = (event: PointerEvent) => {
      targetX = (event.clientX / window.innerWidth - 0.5) * 34
      targetY = (event.clientY / window.innerHeight - 0.5) * 26
    }
    const reset = () => {
      targetX = 0
      targetY = 0
    }
    stage.addEventListener('pointermove', onPointerMove)
    stage.addEventListener('pointerleave', reset)
    frame = window.requestAnimationFrame(render)
    return () => {
      stage.removeEventListener('pointermove', onPointerMove)
      stage.removeEventListener('pointerleave', reset)
      window.cancelAnimationFrame(frame)
    }
  }, [])

  function revealCapabilities() {
    const target = document.getElementById('capabilities')
    if (!target) return
    const top = target.getBoundingClientRect().top + window.scrollY - 28
    window.scrollTo({ top, behavior: 'smooth' })
    target.classList.add('is-focused')
    window.setTimeout(() => target.classList.remove('is-focused'), 1300)
  }

  return (
    <main className="visitor-welcome signal-theme" ref={stageRef}>
      <div className="ambient-stage" aria-hidden="true">
        <span className="ambient-block block-coral" />
        <span className="ambient-block block-blue" />
        <span className="ambient-block block-lime" />
        <span className="ambient-grid" />
      </div>

      <header className="welcome-nav">
        <div className="welcome-brand">
          <span className="brand-icon" aria-hidden="true"><AppIcon name="network" size={22} /></span>
          <span><strong>Deep Research</strong><small>Multi-Agent System</small></span>
        </div>
        <div className="welcome-nav-meta"><span><i /> SYSTEM READY</span><span>BUILT BY LXINY</span></div>
      </header>

      <section className="welcome-hero">
        <div className="welcome-topline">
          <span>DEEP RESEARCH / MULTI-AGENT</span>
          <span>RESEARCH IS A MOVING TARGET</span>
        </div>
        <div className="welcome-copy">
          <span className="welcome-kicker"><AppIcon name="sparkles" size={14} aria-hidden="true" /> 欢迎来到 Lxiny 的项目空间</span>
          <h1><span>让多个 Agent</span><span className="headline-accent">像团队一样研究。</span></h1>
          <p>
            一个面向复杂任务的深度研究系统：支持规划、并行检索、反思补充、证据汇总，
            并提供可视化工作流画布来自由编排角色、分支与汇聚关系。
          </p>
          <div className="welcome-actions">
            <button className="btn btn-primary btn-lg" onClick={onEnter} type="button">
              管理员进入控制台
              <AppIcon name="arrow-up-right" size={17} aria-hidden="true" />
            </button>
            <button className="welcome-secondary" onClick={revealCapabilities} type="button">
              查看系统如何工作
              <AppIcon name="arrow-down" size={15} aria-hidden="true" />
            </button>
          </div>
          <div className="welcome-signal-line" aria-label="研究链路：规划、检索、反思、报告">
            <span><AppIcon name="route" size={13} aria-hidden="true" /> 规划</span>
            <i />
            <span><AppIcon name="search-code" size={13} aria-hidden="true" /> 检索</span>
            <i />
            <span><AppIcon name="refresh" size={13} aria-hidden="true" /> 反思</span>
            <i />
            <span><AppIcon name="file" size={13} aria-hidden="true" /> 报告</span>
          </div>
        </div>

        <div className="welcome-orbit" aria-label="多 Agent 研究流程示意">
          <span className="orbit-axis axis-horizontal" aria-hidden="true" />
          <span className="orbit-axis axis-vertical" aria-hidden="true" />
          <span className="orbit-ring ring-outer" aria-hidden="true" />
          <span className="orbit-ring ring-middle" aria-hidden="true" />
          <span className="orbit-ring ring-inner" aria-hidden="true" />
          <span className="orbit-core"><AppIcon name="network" size={29} aria-hidden="true" /><b>DR</b></span>
          <span className="orbit-node node-a"><AppIcon name="route" size={13} aria-hidden="true" />PLAN</span>
          <span className="orbit-node node-b"><AppIcon name="search-code" size={13} aria-hidden="true" />SEARCH</span>
          <span className="orbit-node node-c"><AppIcon name="refresh" size={13} aria-hidden="true" />REFLECT</span>
          <span className="orbit-node node-d"><AppIcon name="file" size={13} aria-hidden="true" />REPORT</span>
        </div>
      </section>

      <section className="welcome-capabilities" id="capabilities">
        <article><b>01</b><strong>复杂任务研究</strong><p>从问题拆解到带引用报告，保留完整研究链路。</p><AppIcon name="file-search" size={21} aria-hidden="true" /></article>
        <article><b>02</b><strong>多 Agent 编排</strong><p>可视化配置串行、并行、条件、汇聚与反思控制。</p><AppIcon name="waypoints" size={21} aria-hidden="true" /></article>
        <article><b>03</b><strong>模型与角色治理</strong><p>模型档案、角色模板、连接测试和故障转移统一管理。</p><AppIcon name="users" size={21} aria-hidden="true" /></article>
      </section>
      <WelcomeTelemetrySection />
    </main>
  )
}
