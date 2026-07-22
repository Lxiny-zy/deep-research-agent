import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { WorkflowDef, WorkflowDefInput } from '../types'
import WorkflowBuilderPage from './WorkflowBuilderPage'

const mocks = vi.hoisted(() => ({
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
  removeMutate: vi.fn(),
  refetch: vi.fn(),
  workflows: ['alpha', 'beta'].map((name, index) => ({
    id: `wf-${index + 1}`,
    name,
    display_name: name.toUpperCase(),
    description: '',
    steps: [{ kind: 'agent' as const, agent: 'synthesizer' }],
    nodes: [
      {
        id: `${name}-node`,
        type: 'step',
        position: { x: 0, y: 0 },
        step: { kind: 'agent' as const, agent: 'synthesizer' },
      },
    ],
    edges: [],
    viewport: { x: 0, y: 0, zoom: 1 },
    version: 1,
    enabled: true,
  })),
}))

vi.mock('../hooks/useCatalog', () => ({
  useCustomWorkflows: () => ({
    data: mocks.workflows,
    isLoading: false,
    isError: false,
    refetch: mocks.refetch,
  }),
  useRoles: () => ({ data: [] }),
  useWorkflowMutations: () => ({
    create: { mutate: mocks.createMutate },
    update: { mutate: mocks.updateMutate },
    remove: { mutate: mocks.removeMutate },
  }),
}))

vi.mock('../components/WorkflowEditor', () => ({
  default: ({
    initial,
    onSubmit,
    onCancel,
    pending,
    error,
  }: {
    initial: WorkflowDef | null
    onSubmit: (body: WorkflowDefInput) => void
    onCancel: () => void
    pending?: boolean
    error?: string
  }) => (
    <div data-testid="workflow-editor" data-workflow={initial?.name ?? 'new'}>
      <button
        type="button"
        data-testid="save-editor"
        onClick={() => onSubmit({ version: initial?.version })}
      >
        save
      </button>
      <button type="button" data-testid="close-editor" onClick={onCancel}>
        close
      </button>
      <span data-testid="editor-pending">{String(!!pending)}</span>
      <span data-testid="editor-version">{String(initial?.version ?? '')}</span>
      {error && <span data-testid="editor-error">{error}</span>}
    </div>
  ),
}))

function editButton(container: HTMLElement, index: number): HTMLButtonElement {
  const card = container.querySelectorAll<HTMLElement>('.role-card')[index]
  return card.querySelectorAll<HTMLButtonElement>('.role-card-foot button')[1]
}

describe('WorkflowBuilderPage edit sessions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.refetch.mockResolvedValue({
      data: [{ ...mocks.workflows[0], version: 2 }, mocks.workflows[1]],
    })
  })

  it('does not let an old save response close or contaminate a newer edit session', () => {
    const { container } = render(
      <MemoryRouter>
        <WorkflowBuilderPage />
      </MemoryRouter>,
    )

    fireEvent.click(editButton(container, 0))
    expect(screen.getByTestId('workflow-editor')).toHaveAttribute('data-workflow', 'alpha')
    fireEvent.click(screen.getByTestId('save-editor'))
    expect(screen.getByTestId('editor-pending')).toHaveTextContent('true')

    const oldCallbacks = mocks.updateMutate.mock.calls[0][1]
    fireEvent.click(screen.getByTestId('close-editor'))
    fireEvent.click(editButton(container, 1))
    expect(screen.getByTestId('workflow-editor')).toHaveAttribute('data-workflow', 'beta')
    expect(screen.getByTestId('editor-pending')).toHaveTextContent('false')

    act(() => oldCallbacks.onError(new Error('stale failure')))
    expect(screen.queryByTestId('editor-error')).not.toBeInTheDocument()
    act(() => oldCallbacks.onSuccess())
    act(() => oldCallbacks.onSettled())

    expect(screen.getByTestId('workflow-editor')).toHaveAttribute('data-workflow', 'beta')
  })

  it('keeps the current editor open and reports a version conflict', async () => {
    const { container } = render(
      <MemoryRouter>
        <WorkflowBuilderPage />
      </MemoryRouter>,
    )

    fireEvent.click(editButton(container, 0))
    fireEvent.click(screen.getByTestId('save-editor'))
    const callbacks = mocks.updateMutate.mock.calls[0][1]

    await act(async () => {
      callbacks.onError(new ApiError(409, 'workflow version is stale'))
      callbacks.onSettled()
      await Promise.resolve()
    })

    expect(screen.getByTestId('workflow-editor')).toHaveAttribute('data-workflow', 'alpha')
    expect(screen.getByTestId('editor-error')).toHaveTextContent(
      '保存冲突：workflow version is stale。当前草稿已保留。',
    )
  })

  it('refreshes only the version after conflict so retry carries the server version', async () => {
    const { container } = render(
      <MemoryRouter>
        <WorkflowBuilderPage />
      </MemoryRouter>,
    )

    fireEvent.click(editButton(container, 0))
    expect(screen.getByTestId('editor-version')).toHaveTextContent('1')
    fireEvent.click(screen.getByTestId('save-editor'))
    expect(mocks.updateMutate.mock.calls[0][0].body.version).toBe(1)

    const callbacks = mocks.updateMutate.mock.calls[0][1]
    await act(async () => {
      callbacks.onError(new ApiError(409, 'workflow version is stale'))
      callbacks.onSettled()
      await Promise.resolve()
    })

    expect(mocks.refetch).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('workflow-editor')).toHaveAttribute('data-workflow', 'alpha')
    expect(screen.getByTestId('editor-version')).toHaveTextContent('2')

    fireEvent.click(screen.getByTestId('save-editor'))
    expect(mocks.updateMutate.mock.calls[1][0].body.version).toBe(2)
  })
})
