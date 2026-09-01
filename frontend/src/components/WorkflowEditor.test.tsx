import { fireEvent, render } from '@testing-library/react'
import type { WorkflowDef } from '../types'
import WorkflowEditor from './WorkflowEditor'

vi.mock('./WorkflowFlowCanvas', () => ({
  default: (props: {
    onSelectNode: (id: string) => void
    onConnect: (source: string, target: string) => string | undefined
    onViewportChange: (viewport: { x: number; y: number; zoom: number }) => void
  }) => (
    <div>
      <button type="button" data-testid="select-target" onClick={() => props.onSelectNode('b')}>
        select target
      </button>
      <button
        type="button"
        data-testid="change-viewport"
        onClick={() => props.onViewportChange({ x: 42, y: -18, zoom: 1.35 })}
      >
        change viewport
      </button>
      <button type="button" data-testid="add-parallel" onClick={() => props.onConnect('a', 'b')}>
        add parallel edge
      </button>
    </div>
  ),
}))

const roles = [
  { name: 'researcher', label: 'Researcher', icon: '', builtin: true },
  { name: 'synthesizer', label: 'Synthesizer', icon: '', builtin: true, produces_report: true },
]

const initial: WorkflowDef = {
  id: 'wf-1',
  name: 'parallel',
  display_name: 'Parallel',
  description: '',
  steps: [
    { kind: 'agent', agent: 'researcher' },
    { kind: 'agent', agent: 'synthesizer' },
  ],
  nodes: [
    {
      id: 'a',
      type: 'step',
      position: { x: 0, y: 0 },
      step: { kind: 'agent', agent: 'researcher' },
    },
    {
      id: 'b',
      type: 'step',
      position: { x: 0, y: 190 },
      step: { kind: 'agent', agent: 'synthesizer' },
    },
  ],
  edges: [
    { id: 'persisted-one', source: 'a', target: 'b', condition: 'state.first' },
    { id: 'persisted-two', source: 'a', target: 'b', condition: 'state.second' },
  ],
  viewport: { x: 7, y: 8, zoom: 0.9 },
  version: 4,
  enabled: true,
}

describe('WorkflowEditor persistence', () => {
  it('edits parallel edge conditions independently and submits the current viewport', () => {
    const onSubmit = vi.fn()
    const { container, getByTestId } = render(
      <WorkflowEditor initial={initial} roles={roles} onSubmit={onSubmit} onCancel={vi.fn()} />,
    )

    fireEvent.click(getByTestId('select-target'))
    const conditionInputs = Array.from(
      container.querySelectorAll<HTMLInputElement>('.dependency-condition'),
    )
    expect(conditionInputs).toHaveLength(2)
    fireEvent.change(conditionInputs[0], { target: { value: 'updated.first' } })
    fireEvent.click(getByTestId('change-viewport'))
    fireEvent.click(container.querySelector<HTMLButtonElement>('.btn.btn-primary')!)

    expect(onSubmit).toHaveBeenCalledOnce()
    const body = onSubmit.mock.calls[0][0]
    expect(body.edges).toEqual([
      { id: 'persisted-one', source: 'a', target: 'b', condition: 'updated.first' },
      { id: 'persisted-two', source: 'a', target: 'b', condition: 'state.second' },
    ])
    expect(body.viewport).toMatchObject({ x: 42, y: -18, zoom: 1.35 })
    expect(body.version).toBe(4)
  })

  it('creates another edge for an already connected node pair', () => {
    const onSubmit = vi.fn()
    const { container, getByTestId } = render(
      <WorkflowEditor initial={initial} roles={roles} onSubmit={onSubmit} onCancel={vi.fn()} />,
    )

    fireEvent.click(getByTestId('add-parallel'))
    fireEvent.click(container.querySelector<HTMLButtonElement>('.btn.btn-primary')!)

    expect(onSubmit.mock.calls[0][0].edges).toEqual([
      ...initial.edges,
      { id: 'edge-a-b', source: 'a', target: 'b', condition: null },
    ])
  })

  it('removes only one parallel edge when a dependency is unchecked', () => {
    const onSubmit = vi.fn()
    const { container, getByTestId } = render(
      <WorkflowEditor initial={initial} roles={roles} onSubmit={onSubmit} onCancel={vi.fn()} />,
    )

    fireEvent.click(getByTestId('select-target'))
    const dependency = container.querySelector<HTMLInputElement>(
      '.dependency-picker input[type="checkbox"]',
    )!
    expect(dependency).toBeChecked()
    fireEvent.click(dependency)
    fireEvent.click(container.querySelector<HTMLButtonElement>('.btn.btn-primary')!)

    expect(onSubmit.mock.calls[0][0].edges).toEqual([initial.edges[0]])
  })

  it('shows role duty descriptions in the library and tolerates roles without one', () => {
    const describedRoles = [
      {
        name: 'researcher',
        label: '研究员',
        description: '对子问题并行检索网络，只保留通过程序验证的发现。',
        icon: '',
        builtin: true,
      },
      { name: 'synthesizer', label: 'Synthesizer', icon: '', builtin: true, produces_report: true },
    ]
    const { container, getByText } = render(
      <WorkflowEditor
        initial={initial}
        roles={describedRoles}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(getByText('对子问题并行检索网络，只保留通过程序验证的发现。')).toBeInTheDocument()
    // 无描述的角色不渲染空描述行；有描述的按钮带完整 title 提示
    expect(container.querySelectorAll('.role-item-desc')).toHaveLength(1)
    const described = container.querySelector<HTMLButtonElement>('button[title]')
    expect(described?.title).toBe('对子问题并行检索网络，只保留通过程序验证的发现。')
  })
})
