import { render, screen } from '@testing-library/react'
import type { IntentDecision } from '../types'
import IntentPanel from './IntentPanel'

// 意图面板要把「判定 / 级联层级 / 成本 / 依据」都讲清楚——安全审计主线里，
// 一个不可解释的拦截和不拦截同样糟糕。

function makeDecision(over: Partial<IntentDecision> = {}): IntentDecision {
  return {
    intent: 'comparative',
    confidence: 0.87,
    tier: 'model',
    risk: 'none',
    risk_confidence: 0,
    signals: [],
    escalated: false,
    scores: {},
    reason: '',
    ...over,
  }
}

it('renders nothing when the run has no intent decision', () => {
  const { container } = render(<IntentPanel intent={null} />)
  expect(container).toBeEmptyDOMElement()
})

it('shows the task verdict, tier and zero-cost note', () => {
  render(<IntentPanel intent={makeDecision()} />)

  expect(screen.getByText('对比取舍')).toBeInTheDocument()
  expect(screen.getByText('置信度 87%')).toBeInTheDocument()
  expect(screen.getByText('L2 本地模型')).toBeInTheDocument()
  expect(screen.getByText(/未调用大模型/)).toBeInTheDocument()
})

it('marks escalated decisions as having called the model', () => {
  render(<IntentPanel intent={makeDecision({ tier: 'llm', escalated: true })} />)

  expect(screen.getByText('L3 大模型')).toBeInTheDocument()
  expect(screen.getByText(/本次判定调用了大模型/)).toBeInTheDocument()
})

it('surfaces a blocked request with its risk label', () => {
  const { container } = render(
    <IntentPanel
      intent={makeDecision({
        intent: 'unknown',
        confidence: 0,
        tier: 'rule',
        risk: 'system_prompt_probe',
        risk_confidence: 0.95,
      })}
    />,
  )

  expect(screen.getByText('系统提示词探测')).toBeInTheDocument()
  expect(screen.getByText('请求已在研究开始前拦截')).toBeInTheDocument()
  expect(container.querySelector('.intent-panel')).toHaveClass('is-blocked')
})

// 回归：off_task_instruction 不在后端的 blocked 名单里，请求会正常跑完。
// 早期版本用 `risk !== 'none'` 判定，于是 UI 会写「已在研究开始前拦截」，与事实相反。
it('does not claim a non-blocking risk was intercepted', () => {
  const { container } = render(
    <IntentPanel
      intent={makeDecision({
        risk: 'off_task_instruction',
        risk_confidence: 0.7,
      })}
    />,
  )

  expect(screen.getByText('越权指令')).toBeInTheDocument()
  expect(screen.queryByText('请求已在研究开始前拦截')).not.toBeInTheDocument()
  expect(screen.getByText(/未达拦截门槛，研究照常执行/)).toBeInTheDocument()
  expect(container.querySelector('.intent-panel')).not.toHaveClass('is-blocked')
})

it('lists the matched signals as auditable evidence', () => {
  render(
    <IntentPanel
      intent={makeDecision({
        tier: 'rule',
        risk: 'prompt_injection',
        signals: [
          { tier: 'rule', code: 'override_previous_instructions', detail: 'ignore all previous' },
        ],
      })}
    />,
  )

  expect(screen.getByText('override_previous_instructions')).toBeInTheDocument()
  expect(screen.getByText('ignore all previous')).toBeInTheDocument()
})

it('renders the top class probabilities from the local model', () => {
  render(
    <IntentPanel
      intent={makeDecision({
        scores: {
          comparative: 0.62,
          exploratory: 0.21,
          factual_lookup: 0.11,
          causal_analysis: 0.04,
          temporal_trend: 0.02,
        },
      })}
    />,
  )

  expect(screen.getByText('62%')).toBeInTheDocument()
  expect(screen.getByText('21%')).toBeInTheDocument()
  // 只展示前 4 类，最低的一类不应出现。
  expect(screen.queryByText('2%')).not.toBeInTheDocument()
})
