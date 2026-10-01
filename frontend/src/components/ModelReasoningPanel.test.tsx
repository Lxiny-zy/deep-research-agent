import { fireEvent, render, screen } from '@testing-library/react'
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

it('follows live thinking until the user scrolls up, and resumes at the bottom', () => {
  const contents = (text: string) => [
    event({ call_id: 'one', model: 'test', reasoning_delta: text }),
  ]
  const { container, rerender } = render(<ModelReasoningPanel events={contents('first')} live />)
  const scroller = container.querySelector('.model-reasoning-text') as HTMLDivElement
  let height = 300
  Object.defineProperty(scroller, 'clientHeight', { value: 100 })
  Object.defineProperty(scroller, 'scrollHeight', { get: () => height })
  rerender(<ModelReasoningPanel events={contents('first second')} live />)
  expect(scroller.scrollTop).toBe(200)
  scroller.scrollTop = 40
  fireEvent.scroll(scroller)
  height = 400
  rerender(<ModelReasoningPanel events={contents('first second third')} live />)
  expect(scroller.scrollTop).toBe(40)
  scroller.scrollTop = 300
  fireEvent.scroll(scroller)
  height = 500
  rerender(<ModelReasoningPanel events={contents('first second third fourth')} live />)
  expect(scroller.scrollTop).toBe(400)
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
  expect(screen.getByText('第一个片段，后续内容')).toBeInTheDocument()
  expect(screen.getByText('另一次调用')).toBeInTheDocument()
  expect(container.querySelector('details')).not.toHaveAttribute('open')
})
