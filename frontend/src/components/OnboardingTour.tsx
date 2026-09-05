import { useState } from 'react'
import { createPortal } from 'react-dom'
import { AppIcon, type AppIconName } from './AppIcon'
import { useDialogFocus } from '../hooks/useDialogFocus'

const steps: { icon: AppIconName; title: string; text: string; label: string }[] = [
  {
    icon: 'scan-search',
    title: '从一个问题开始',
    text: '写下真正想弄清的问题，确认研究范围，再开启探索。',
    label: 'QUESTION',
  },
  {
    icon: 'network',
    title: '沿证据，走得更深',
    text: '规划、检索与反思持续推进。结论背后的引用与证据，始终可以复核。',
    label: 'EVIDENCE',
  },
  {
    icon: 'history',
    title: '让每次研究都有后续',
    text: '报告与来源留在研究历史中。回到已有结论，继续追问新的问题。',
    label: 'INSIGHT',
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
          <span>DEEP RESEARCH / START HERE</span>
          <button
            type="button"
            className="tour-icon-button"
            onClick={onClose}
            aria-label="跳过引导"
            title="跳过引导"
          >
            <AppIcon name="x" size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="welcome-tour-visual" aria-hidden="true">
          <span className="tour-number">0{step + 1}</span>
          <AppIcon name={steps[step].icon} size={72} strokeWidth={1} />
          <span className="tour-word">{steps[step].label}</span>
        </div>
        <div className="welcome-tour-copy" aria-live="polite" aria-atomic="true">
          <h2 id="welcome-tour-title">{steps[step].title}</h2>
          <p>{steps[step].text}</p>
        </div>
        <div className="welcome-tour-progress" aria-label={`引导进度 ${step + 1} / 3`}>
          {steps.map((_, index) => (
            <i key={index} className={index <= step ? 'active' : ''} />
          ))}
        </div>
        <div className="welcome-tour-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            跳过
          </button>
          <div>
            {step > 0 && (
              <button
                type="button"
                className="btn btn-ghost icon-button"
                title="上一步"
                aria-label="上一步"
                onClick={() => setStep(step - 1)}
              >
                <AppIcon name="arrow-left" size={17} aria-hidden="true" />
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => (step === 2 ? onComplete() : setStep(step + 1))}
            >
              {step === 2 ? '开始研究' : '下一步'}
              <AppIcon name="arrow-right" size={17} aria-hidden="true" />
            </button>
          </div>
        </div>
      </section>
    </div>,
    document.body,
  )
}
