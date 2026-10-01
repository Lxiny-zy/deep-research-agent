import { fireEvent, render, screen } from '@testing-library/react'
import type { ResearchEvent } from '../types'
import EventTimeline from './EventTimeline'

const scrollTo = vi.fn()
const previousScrollTo = HTMLElement.prototype.scrollTo
beforeEach(() => {
  HTMLElement.prototype.scrollTo = scrollTo
  scrollTo.mockClear()
})
afterEach(() => {
  HTMLElement.prototype.scrollTo = previousScrollTo
})

const activity = (message: string, elapsed = 0): ResearchEvent => ({
  stage: 'RESEARCHER',
  type: 'info',
  message,
  elapsed,
})
const reasoning = (elapsed: number): ResearchEvent => ({
  stage: 'LLM',
  type: 'info',
  message: '模型返回的思考内容',
  elapsed,
  data: { call_id: 'one', model: 'test', reasoning_delta: '思考分片' },
})

it('does not turn hundreds of live or replayed model fragments into activity rows or pages', () => {
  const events = [
    activity('正在检索论文'),
    ...Array.from({ length: 600 }, (_, i) => reasoning(i)),
    { ...activity('模型用量已返回'), stage: 'LLM', data: { llm_usage: { input_tokens: 100 } } },
    activity('证据核验完成', 601),
  ]
  const { container, rerender } = render(<EventTimeline events={events} streaming />)
  expect(container.querySelectorAll('.event-row')).toHaveLength(2)
  expect(screen.queryByText('模型返回的思考内容')).not.toBeInTheDocument()
  expect(screen.queryByText('模型用量已返回')).not.toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '事件分页' })).not.toBeInTheDocument()
  rerender(<EventTimeline events={events} />)
  expect(container.querySelectorAll('.event-row')).toHaveLength(2)
})

it('keeps rows and scroll stationary on reasoning updates, following only new activities', () => {
  const start = activity('正在抽取证据')
  const { container, rerender } = render(<EventTimeline events={[start]} streaming />)
  const row = container.querySelector('.event-row')
  scrollTo.mockClear()
  rerender(<EventTimeline events={[start, reasoning(1), reasoning(2)]} streaming />)
  expect(container.querySelector('.event-row')).toBe(row)
  expect(scrollTo).not.toHaveBeenCalled()
  const finish = activity('抽取完成', 3)
  rerender(<EventTimeline events={[start, reasoning(1), reasoning(2), finish]} streaming />)
  expect(scrollTo).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: '暂停自动跟随事件' }))
  scrollTo.mockClear()
  rerender(<EventTimeline events={[start, finish, activity('开始核验', 4)]} streaming />)
  expect(scrollTo).not.toHaveBeenCalled()
})

it('retains model failures and genuine activity pagination', () => {
  const events: ResearchEvent[] = [
    ...Array.from({ length: 100 }, (_, i) => activity(`检索 ${i}`, i)),
    reasoning(101),
    { ...reasoning(102), type: 'error', message: '模型请求失败' },
  ]
  render(<EventTimeline events={events} />)
  expect(screen.getByText('第 2 / 2 页')).toBeInTheDocument()
  expect(screen.getByText('模型请求失败')).toBeInTheDocument()
  expect(screen.queryByText('LLM · LLM')).not.toBeInTheDocument()
})
