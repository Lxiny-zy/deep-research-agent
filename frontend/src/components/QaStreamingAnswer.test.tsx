import { render, screen } from '@testing-library/react'
import QaStreamingAnswer from './QaStreamingAnswer'

it('shows citation verification progress after the answer draft has arrived', () => {
  const { rerender } = render(<QaStreamingAnswer text="回答原稿" waiting="正在等待…" />)
  expect(screen.getByRole('status')).toHaveTextContent('正在生成回答…')
  rerender(
    <QaStreamingAnswer
      text="回答原稿"
      waiting="正在等待…"
      activity={[
        { type: 'status', message: '正在组织回答…' },
        { type: 'status', message: '正在核对结论是否得到引用支持…' },
        { type: 'reasoning', call_id: 'check', reasoning_delta: '核验片段' },
      ]}
    />,
  )
  expect(screen.getByRole('status')).toHaveTextContent('正在核对结论是否得到引用支持…')
  expect(screen.getByRole('status')).not.toHaveTextContent('正在生成回答')
  expect(screen.getByTestId('qa-streaming-answer')).toHaveTextContent('回答原稿')
})

it('keeps reset and reconnect statuses visible over an existing draft', () => {
  render(
    <QaStreamingAnswer
      text="回答原稿"
      waiting="正在等待…"
      activity={[
        { type: 'status', message: '正在核验…' },
        { type: 'reset', message: '正在修订回答…' },
        { type: 'status', message: '连接中断，正在查询原任务并恢复连接…' },
      ]}
    />,
  )
  expect(screen.getByRole('status')).toHaveTextContent('连接中断，正在查询原任务并恢复连接…')
})
