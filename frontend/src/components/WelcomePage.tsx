import WelcomeTelemetrySection from './WelcomeTelemetrySection'

interface Props {
  onEnter: () => void
}

export default function WelcomePage({ onEnter }: Props) {
  return (
    <main className="visitor-welcome signal-theme">
      <div className="ambient-stage" aria-hidden="true">
        <span className="ambient-block block-coral" />
        <span className="ambient-block block-blue" />
        <span className="ambient-block block-lime" />
        <span className="ambient-grid" />
      </div>
      <section className="welcome-hero">
        <div className="welcome-topline">
          <span>DEEP RESEARCH / MULTI-AGENT</span>
          <span>BUILT BY LXINY</span>
        </div>
        <div className="welcome-copy">
          <span className="welcome-kicker">欢迎来到 Lxiny 的项目空间</span>
          <h1><span>让多个 Agent</span><span>像团队一样研究。</span></h1>
          <p>
            一个面向复杂任务的深度研究系统：支持规划、并行检索、反思补充、证据汇总，
            并提供可视化工作流画布来自由编排角色、分支与汇聚关系。
          </p>
          <div className="welcome-actions">
            <button className="btn btn-primary btn-lg" onClick={onEnter}>管理员进入控制台</button>

          </div>
        </div>
        <div className="welcome-orbit" aria-hidden="true">
          <span className="orbit-core">DR</span>
          <span className="orbit-node node-a">PLAN</span>
          <span className="orbit-node node-b">SEARCH</span>
          <span className="orbit-node node-c">REFLECT</span>
          <span className="orbit-node node-d">REPORT</span>
        </div>
      </section>
      <section className="welcome-capabilities" id="capabilities">
        <article><b>01</b><strong>复杂任务研究</strong><p>从问题拆解到带引用报告，保留完整研究链路。</p></article>
        <article><b>02</b><strong>多 Agent 编排</strong><p>可视化配置串行、并行、条件、汇聚与反思控制。</p></article>
        <article><b>03</b><strong>模型与角色治理</strong><p>模型档案、角色模板、连接测试和故障转移统一管理。</p></article>
      </section>
      <WelcomeTelemetrySection />
    </main>
  )
}
