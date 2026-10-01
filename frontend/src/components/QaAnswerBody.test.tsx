import { fireEvent, render, screen } from '@testing-library/react'
import QaAnswerBody from './QaAnswerBody'
import type { QaEvidence } from '../types'

const first: QaEvidence = {
  source_url: 'https://workspace.invalid/attachments/paper',
  origin: 'paper',
  statement: '选择参考波段',
  evidence_quote: 'reference band selection',
}
const second: QaEvidence = {
  ...first,
  statement: '几何校正与匹配',
  evidence_quote: 'geometric matching',
}

it('uses clickable inline citations and selects evidence matching the current paragraph', () => {
  const locate = vi.fn()
  render(
    <QaAnswerBody
      text={'**直接回答**\n\n选择参考波段 [1]。\n\n几何校正与匹配 [1]。'}
      citations={[first.source_url]}
      evidence={[first, second]}
      onLocate={locate}
    />,
  )
  expect(screen.getByRole('heading', { name: '直接回答' })).toBeInTheDocument()
  fireEvent.click(screen.getAllByRole('button', { name: '定位引用 1 的论文依据' })[1])
  expect(locate).toHaveBeenCalledWith(second)
})

it('expands grouped markers while leaving code markers and unknown references inactive', () => {
  render(
    <QaAnswerBody
      text={'引用 [1, 2]，未知 [9]，代码 `[1]`。'}
      citations={[first.source_url, 'https://example.org/paper']}
      evidence={[first]}
      onLocate={vi.fn()}
    />,
  )
  expect(screen.getByRole('button', { name: '定位引用 1 的论文依据' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /查看引用 2/ })).toHaveAttribute(
    'href',
    'https://example.org/paper',
  )
  expect(screen.getAllByRole('button')).toHaveLength(1)
  expect(screen.getByText('[9]')).toHaveClass('is-unavailable')
})

it('does not make provisional streaming citations clickable', () => {
  render(
    <QaAnswerBody
      text="尚在生成 [1]。"
      citations={[first.source_url]}
      evidence={[first]}
      onLocate={vi.fn()}
      streaming
    />,
  )
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
})
