import { useState } from 'react'
import { createPortal } from 'react-dom'
import { AppIcon, type AppIconName } from './AppIcon'
import { useDialogFocus } from '../hooks/useDialogFocus'

const steps: { icon: AppIconName; title: string; text: string }[] = [
  {
    icon: 'sparkles',
    title: '选一项科研任务',
    text: '综述、精读、审稿、数据分析、汇报幻灯片或思维导图。检索深度按任务选择。',
  },
  {
    icon: 'shield',
    title: '每条结论都可核验',
    text: '证据逐字核对原文，交付前经过引用、结构与学术文体检查，不合格会自动返工。',
  },
  {
    icon: 'download',
    title: '拿走可用的交付物',
    text: 'Word、PDF、PPT 与导图同源生成，留在任务记录里，可随时下载或继续追问。',
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
