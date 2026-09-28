import { AppIcon, type AppIconName } from './AppIcon'

const FEATURES: { icon: AppIconName; title: string; text: string }[] = [
  {
    icon: 'sparkles',
    title: '覆盖科研全场景',
    text: '课题调研、文献综述、论文精读、同行评审、数据分析、汇报幻灯片与思维导图。',
  },
  {
    icon: 'file-search',
    title: '读懂你的文件',
    text: '上传 PDF、Word、PPT、Excel，模型先读文件再检索，引用标注到页码与章节。',
  },
  {
    icon: 'shield',
    title: '证据逐字核验',
    text: '每条结论都对照原文核对，引用可追溯到来源的具体段落，杜绝编造文献与数字。',
  },
  {
    icon: 'check-circle',
    title: '质量把关与返工',
    text: '交付前检查引用下限、章节结构与学术文体，不合格自动返工，仍不达标如实标注。',
  },
]

const FLOW: { icon: AppIconName; label: string }[] = [
  { icon: 'route', label: '规划' },
  { icon: 'search-code', label: '检索' },
  { icon: 'shield', label: '核验' },
  { icon: 'edit', label: '写作' },
  { icon: 'download', label: '交付' },
]

const DELIVERABLES = ['Word', 'PDF', 'PPT', 'HTML', 'Excel', '思维导图']

/** 未登录时的产品首页：价值主张 + 产品示意 + 能力卡片。 */
export default function WelcomePage({
  onEnter,
  onTour,
}: {
  onEnter: () => void
  onTour?: () => void
}) {
  return (
    <div className="welcome">
      <div className="welcome-aurora" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <header className="welcome-nav">
        <span className="welcome-brand">
          <span className="sidebar-logo" aria-hidden="true">
            <AppIcon name="network" size={18} strokeWidth={2} />
          </span>
          Science Research
        </span>
        <nav className="welcome-nav-actions" aria-label="欢迎页导航">
          {onTour && (
            <button type="button" className="btn btn-ghost" onClick={onTour}>
              <AppIcon name="help" size={15} aria-hidden="true" />
              使用引导
            </button>
          )}
          <button type="button" className="btn btn-primary" onClick={onEnter}>
            进入工作台
          </button>
        </nav>
      </header>

      <main className="welcome-main">
        <section className="welcome-hero">
          <span className="welcome-eyebrow">
            <span className="welcome-eyebrow-dot" aria-hidden="true" />
            面向科研人员的研究工作台
          </span>
          <h1>
            把文献与数据
            <br />
            <span className="welcome-gradient-text">交给可核验的研究助手</span>
          </h1>
          <p>
            从一个问题或一份文件开始：检索、阅读、逐字核验证据，再按学术规范写成可直接使用的交付物。
          </p>
          <div className="welcome-cta">
            <button type="button" className="btn btn-primary btn-lg" onClick={onEnter}>
              进入工作台
              <AppIcon name="arrow-right" size={16} aria-hidden="true" />
            </button>
            {onTour && (
              <button type="button" className="btn btn-secondary btn-lg" onClick={onTour}>
                使用引导
              </button>
            )}
          </div>
          <ul className="welcome-deliverables" aria-label="支持的交付格式">
            {DELIVERABLES.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section className="welcome-preview" aria-label="一次任务的工作流程">
          <div className="welcome-preview-card">
            <div className="welcome-preview-head">
              <span className="welcome-preview-dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              <span>文献综述 · 快照光谱成像重建方法</span>
            </div>
            <ol className="welcome-flow">
              {FLOW.map((step, index) => (
                <li key={step.label} style={{ animationDelay: `${index * 180}ms` }}>
                  <span className="welcome-flow-icon" aria-hidden="true">
                    <AppIcon name={step.icon} size={15} />
                  </span>
                  <span>{step.label}</span>
                </li>
              ))}
            </ol>
            <div className="welcome-preview-body" aria-hidden="true">
              <span className="welcome-line w90" />
              <span className="welcome-line w75" />
              <span className="welcome-line w82" />
              <span className="welcome-cite">
                <AppIcon name="shield" size={12} />
                已核验引用 24 / 要求 20
              </span>
            </div>
            <div className="welcome-preview-foot">
              <span>质量验收</span>
              <strong>8 / 8 通过</strong>
            </div>
          </div>
        </section>
      </main>

      <section className="welcome-features" aria-label="主要能力">
        {FEATURES.map((feature) => (
          <article key={feature.title} className="welcome-feature">
            <span className="welcome-feature-icon" aria-hidden="true">
              <AppIcon name={feature.icon} size={18} />
            </span>
            <h2>{feature.title}</h2>
            <p>{feature.text}</p>
          </article>
        ))}
      </section>

      <footer className="welcome-footer">
        <span>Science Research</span>
        <span>模型通过 API 调用云端服务，交付物在本地生成。</span>
      </footer>
    </div>
  )
}
