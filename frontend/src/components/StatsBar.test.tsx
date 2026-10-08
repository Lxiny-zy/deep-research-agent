import { render, screen } from '@testing-library/react'
import type { ResearchProgress } from '../lib/runProgress'
import StatsBar from './StatsBar'

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
})
afterEach(() => vi.unstubAllGlobals())

const progress: ResearchProgress = {
  percent: 99, completed: 6, total: 6, activeStep: null,
  currentLabel: '研究已结束 · 报告与交付待复核', terminal: true, estimated: false,
}

it('does not make a finished review-required task look stuck at 99 percent', () => {
  render(<StatsBar stats={null} detail={null} progress={progress} connectionStatus="needs_review" />)
  expect(screen.getByText(progress.currentLabel)).toBeInTheDocument()
  expect(screen.getByText('待复核')).toBeInTheDocument()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(screen.queryByText('99')).not.toBeInTheDocument()
})

it.each([false, true])('keeps progress for running or successfully finished tasks (%s)', (terminal) => {
  render(<StatsBar stats={null} detail={null}
    progress={{ ...progress, terminal, percent: terminal ? 100 : 50 }} />)
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', terminal ? '100' : '50')
})
