import { render, screen } from '@testing-library/react'
import QaMessageView from './QaMessage'
import type { QaMessage } from '../types'

const message: QaMessage = {
  id: 'answer',
  position: 0,
  query: '模拟和真实条件能否比较？',
  answer: '阶段数不同，噪声条件仍待确认。',
  citations: [],
  evidence: [],
  thoughts: [],
  status: 'done',
  created_at: null,
}

it('shows unresolved details alongside the saved answer without hiding usable conclusions', () => {
  render(
    <QaMessageView
      message={{
        ...message,
        thoughts: [
          {
            tool: 'evidence_coverage',
            input: message.query,
            observation: '保留待确认项',
            unresolved_topics: ['模拟噪声条件'],
          },
        ],
      }}
    />,
  )
  expect(screen.getByRole('note')).toHaveTextContent('仍待确认：模拟噪声条件')
  expect(screen.getByText(message.answer)).toBeVisible()
  expect(screen.queryByText('evidence_coverage')).not.toBeInTheDocument()
})

it('does not add an uncertainty notice to answers without unresolved details', () => {
  render(<QaMessageView message={message} />)
  expect(screen.queryByRole('note')).not.toBeInTheDocument()
})
