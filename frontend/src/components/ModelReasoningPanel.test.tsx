import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ResearchEvent } from '../types'
import ModelReasoningPanel from './ModelReasoningPanel'
import QaActivityView from './QaActivityView'

const event = (data: ResearchEvent['data']): ResearchEvent => ({
  stage: 'LLM',
  type: 'info',
  message: '',
  elapsed: 1,
  data,
})

it('does not show usage or cache details in the chat', () => {
  const { container } = render(
    <QaActivityView
      items={[
        { type: 'usage', llm_usage: { input_tokens: 100, cached_input_tokens: 80 } },
        { type: 'cache', message: '复用已核验的论文证据' },
      ]}
    />,
  )
  expect(container).toBeEmptyDOMElement()
})

it('follows live thinking until the user scrolls up, and resumes at the bottom', async () => {
  const contents = (text: string) => [
    event({ call_id: 'one', model: 'test', reasoning_delta: text }),
  ]
  const { container, rerender } = render(<ModelReasoningPanel events={contents('first')} live />)
  const scroller = container.querySelector('.model-reasoning-text') as HTMLDivElement
  let height = 300
  Object.defineProperty(scroller, 'clientHeight', { value: 100 })
  Object.defineProperty(scroller, 'scrollHeight', { get: () => height })
  rerender(<ModelReasoningPanel events={contents('first second')} live />)
  await waitFor(() => expect(scroller.scrollTop).toBe(200))
  scroller.scrollTop = 40
  fireEvent.scroll(scroller)
  height = 400
  rerender(<ModelReasoningPanel events={contents('first second third')} live />)
  await screen.findByText('有新内容')
  expect(scroller.scrollTop).toBe(40)
  expect(scroller).toHaveTextContent('first second')
  expect(scroller).not.toHaveTextContent('third')
  scroller.scrollTop = 300
  fireEvent.scroll(scroller)
  height = 500
  rerender(<ModelReasoningPanel events={contents('first second third fourth')} live />)
  await waitFor(() => expect(scroller.scrollTop).toBe(400))
  expect(scroller).toHaveTextContent('first second third fourth')
})

it('groups supplied reasoning per call and leaves the panel collapsed', () => {
  const { container } = render(
    <ModelReasoningPanel
      events={[
        event({ call_id: 'one', model: 'test', reasoning_delta: '第一个片段' }),
        event({ call_id: 'two', model: 'test', reasoning_delta: '另一次调用' }),
        event({ call_id: 'one', model: 'test', reasoning_delta: '，后续内容' }),
      ]}
    />,
  )
  expect(screen.getByText('2 次模型调用')).toBeInTheDocument()
  expect(container.querySelector('details')).not.toHaveAttribute('open')
  expect(screen.queryByText('第一个片段，后续内容')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('思考过程'))
  fireEvent.click(screen.getByText('模型调用 1'))
  fireEvent.click(screen.getByText('模型调用 2'))
  expect(screen.getByText('第一个片段，后续内容')).toBeInTheDocument()
  expect(screen.getByText('另一次调用')).toBeInTheDocument()
})

it('ends a model thinking indicator once that call reports usage, while the request continues', async () => {
  const reasoning = {
    type: 'reasoning' as const,
    call_id: 'one',
    model: 'test',
    reasoning_delta: '先分析',
  }
  const { rerender } = render(<QaActivityView items={[reasoning]} live />)
  expect(screen.getByText('思考中')).toBeInTheDocument()
  rerender(
    <QaActivityView
      items={[
        reasoning,
        { type: 'usage', llm_usage: { call_id: 'one', input_tokens: 100 } },
        { type: 'status', message: '正在核对结论是否得到引用支持…' },
      ]}
      live
    />,
  )
  await waitFor(() => expect(screen.queryByText('思考中')).not.toBeInTheDocument())
  expect(screen.getByText('已结束')).toBeInTheDocument()
  expect(screen.getByText('模型调用 1').closest('details')).not.toHaveAttribute('open')
})

it('does not let the previous call usage end a later active call', () => {
  render(
    <ModelReasoningPanel
      events={[
        event({ call_id: 'one', model: 'test', reasoning_delta: '第一轮' }),
        event({ call_id: 'two', model: 'test', reasoning_delta: '第二轮' }),
        event({ llm_usage: { call_id: 'one' } }),
      ]}
      live
    />,
  )
  expect(screen.getByText('模型调用 1').closest('details')).not.toHaveAttribute('open')
  expect(screen.getByText('模型调用 2').closest('details')).toHaveAttribute('open')
  expect(screen.getAllByText('思考中')).toHaveLength(1)
})
