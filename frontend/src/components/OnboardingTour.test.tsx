import { fireEvent, render, screen } from '@testing-library/react'
import OnboardingTour from './OnboardingTour'
import tourCss from '../styles/prism/tour.css?raw'
import detailsCss from '../styles/prism/details.css?raw'

const css = tourCss + detailsCss

describe('OnboardingTour', () => {
  it('steps through the tour and completes on the last step', () => {
    const onClose = vi.fn()
    const onComplete = vi.fn()
    render(<OnboardingTour onClose={onClose} onComplete={onComplete} />)

    expect(screen.getByRole('dialog', { name: '选一项科研任务' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }))
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }))
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '3')
    fireEvent.click(screen.getByRole('button', { name: /开始使用/ }))
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('has a styled dialog surface (regression: the card rendered fully transparent)', () => {
    // 引导卡片曾因样式随旧 CSS 一起归档而没有任何背景，整片透明
    for (const selector of ['.welcome-tour {', '.welcome-tour-actions', '.welcome-tour-progress']) {
      expect(css).toContain(selector)
    }
    expect(css).toMatch(/\.welcome-tour \{[^}]*background:/)
  })
})
