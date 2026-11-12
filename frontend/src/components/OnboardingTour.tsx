import { useState } from 'react'
import { createPortal } from 'react-dom'
import { AppIcon, type AppIconName } from './AppIcon'
import { useDialogFocus } from '../hooks/useDialogFocus'

const steps: { icon: AppIconName; title: string; text: string }[] = [
  {
    icon: 'flask',
    title: '选定科研任务与命题',
    text: '覆盖课题调研、文献综述、论文精读、同行评审、数据分析、汇报幻灯片与思维导图，可按需搭配私有文献库与检索策略。',
  },
  {
    icon: 'shield',
    title: '原文逐字核验与补证',
    text: '每条论断均与来源原文逐字比对并检验语义支持度；证据缺口自动触发补充检索，交付前再经引用、数值与学术文体门禁复核。',
  },
  {
    icon: 'download',
    title: '同源生成多格式交付',
    text: '定稿同源导出 Markdown、Word、PDF、PPT、Excel 与交互导图，完整留存于任务记录中，随时支持原文回查与多轮追问。',
  },
]

export default function OnboardingTour({
  onClose,
  onComplete,
}: {
  onClose: () => void
  onComplete: () => void
}) {
  const [step, setStep] = useState(0)
  const ref = useDialogFocus(onClose)
  const last = step === steps.length - 1
  return createPortal(
    <div className="welcome-tour-backdrop">
      <section
        ref={ref}
        className="welcome-tour"
        role="dialog"
        aria-modal="true"
        aria-labelledby="welcome-tour-title"
        tabIndex={-1}
      >
        <div className="welcome-tour-topline">
          <span className="welcome-tour-step">
            第 {step + 1} 步，共 {steps.length} 步
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-sm icon-button"
            onClick={onClose}
            aria-label="跳过引导"
            title="跳过引导"
          >
            <AppIcon name="x" size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="welcome-tour-visual" aria-hidden="true">
          <AppIcon name={steps[step].icon} size={28} strokeWidth={1.8} />
        </div>
        <div className="welcome-tour-copy" aria-live="polite" aria-atomic="true">
          <h2 id="welcome-tour-title">{steps[step].title}</h2>
          <p>{steps[step].text}</p>
        </div>
        <div
          className="welcome-tour-progress"
          role="progressbar"
          aria-label="引导进度"
          aria-valuemin={1}
          aria-valuemax={steps.length}
          aria-valuenow={step + 1}
          aria-valuetext={`第 ${step + 1} 步，共 ${steps.length} 步`}
        >
          {steps.map((_, index) => (
            <i key={index} className={index <= step ? 'active' : ''} />
          ))}
        </div>
        <div className="welcome-tour-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            跳过
          </button>
          <div className="row gap-sm">
            {step > 0 && (
              <button type="button" className="btn btn-secondary" onClick={() => setStep(step - 1)}>
                <AppIcon name="arrow-left" size={15} aria-hidden="true" />
                上一步
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => (last ? onComplete() : setStep(step + 1))}
            >
              {last ? '开始使用' : '下一步'}
              <AppIcon name="arrow-right" size={15} aria-hidden="true" />
            </button>
          </div>
        </div>
      </section>
    </div>,
    document.body,
  )
}
