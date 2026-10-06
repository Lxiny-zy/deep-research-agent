import { render, screen } from '@testing-library/react'
import type { ResearchEvent } from '../types'
import { appendQaActivity, qaActivityEvents, savedQaActivity } from '../lib/qaActivity'
import { modelCalls } from '../lib/modelCalls'
import ModelCallSummary from './ModelCallSummary'

function event(call: Record<string, unknown>): ResearchEvent {
  return { stage: 'LLM', type: 'info', message: '', elapsed: 0, data: { model_call: call } }
}

it('counts actual requests once across start, completion and replay without reasoning', () => {
  const first = { call_id: 'one', operation: 'structured', model: 'fixture', status: 'started' }
  const final = { ...first, status: 'failed', usage_state: 'unavailable' }
  const retry = { call_id: 'two', operation: 'structured', status: 'succeeded', attempt: 2, retry_reason: 'schema_retry', usage: { total_tokens: 0 } }
  render(<ModelCallSummary events={[event(first), event(final), event(first), event(retry), event(retry)]} live={false} />)
  expect(screen.getByText('2 次请求 · 1 次重试')).toBeInTheDocument()
  expect(screen.getByText('用量未返回')).toBeInTheDocument()
  expect(screen.getByText('0 token')).toBeInTheDocument()
  expect(screen.getByText('输出格式修正')).toBeInTheDocument()
  expect(screen.queryByText('等待响应')).not.toBeInTheDocument()
})

it('preserves incomplete usage and an unknown terminal state', () => {
  render(<ModelCallSummary events={[event({ call_id: 'one', status: 'started', usage: { input_tokens: 4 } })]} live={false} />)
  expect(screen.getByText('状态未确认')).toBeInTheDocument()
  expect(screen.getByText('输入 4 / 输出 未知 token')).toBeInTheDocument()
})

it('persists and replays the same model call without changing its final state', () => {
  const call = { call_id: 'one', status: 'cancelled' }
  const saved = savedQaActivity([{ tool: 'model_call', input: '', observation: '', call }])
  const replayed = appendQaActivity(saved, { type: 'model_call', model_call: { ...call, status: 'started' } })
  expect(replayed).toHaveLength(1)
  expect(modelCalls(qaActivityEvents(replayed))[0].status).toBe('cancelled')
})

it('ignores malformed metadata instead of rendering objects or claiming zero usage', () => {
  render(<ModelCallSummary events={[event({ call_id: 'one', status: 'failed', model: {}, role: [], usage: { total_tokens: -1 } })]} live={false} />)
  expect(screen.getByText('用量未返回')).toBeInTheDocument()
  expect(screen.queryByText('0 token')).not.toBeInTheDocument()
})
