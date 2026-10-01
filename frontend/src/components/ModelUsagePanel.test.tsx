import { render, screen } from '@testing-library/react'
import type { ResearchEvent } from '../types'
import ModelUsagePanel from './ModelUsagePanel'
import ModelReasoningPanel from './ModelReasoningPanel'

const event = (data: ResearchEvent['data']): ResearchEvent => ({
  stage: 'LLM',
  type: 'info',
  message: '',
  elapsed: 1,
  data,
})

it('keeps missing cache statistics distinct from a reported zero', () => {
  const { rerender } = render(
    <ModelUsagePanel
      events={[
        event({ llm_usage: { input_tokens: 100, output_tokens: 20, cached_input_tokens: null } }),
      ]}
    />,
  )
  expect(screen.getByText('供应商未返回')).toBeInTheDocument()
  expect(screen.getByText(/0 \/ 1 次已记录/)).toBeInTheDocument()
  rerender(
    <ModelUsagePanel
      events={[
        event({ llm_usage: { input_tokens: 100, output_tokens: 20, cached_input_tokens: 0 } }),
      ]}
    />,
  )
  expect(screen.queryByText('供应商未返回')).not.toBeInTheDocument()
  expect(screen.getByText('0')).toBeInTheDocument()
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
