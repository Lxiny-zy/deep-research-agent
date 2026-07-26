import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { WorkflowDef, WorkflowDefInput, WorkflowInfo } from '../types'
import WorkflowBuilderPage from './WorkflowBuilderPage'

const mocks = vi.hoisted(() => ({
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
  removeMutate: vi.fn(),
  refetch: vi.fn(),
  listWorkflows: vi.fn(),
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

// 后端 /api/workflows：内置模板（default/custom 为字符串 "True"/"False"）+ 自定义流程
const BUILTIN_WORKFLOWS: WorkflowInfo[] = [
  { name: 'deep', description: '完整深度研究', default: 'True', custom: 'False' },
  { name: 'quick', description: '快速查询', default: 'False', custom: 'False' },
  { name: 'reviewed', description: '深度研究 + 报告复核', default: 'False', custom: 'False' },
  { name: 'auto', description: '自组合', default: 'False', custom: 'False' },
  { name: 'teams', description: '多团队并行', default: 'False', custom: 'False' },
  { name: 'alpha', description: '自定义流程不该出现在模板区', default: 'False', custom: 'True' },
]

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  listWorkflows: mocks.listWorkflows,
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
    <div
      data-testid="workflow-editor"
      data-workflow={initial?.name ?? 'new'}
      data-steps={JSON.stringify(initial?.steps ?? null)}
    >
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

async function renderPage() {
  const utils = render(
    <MemoryRouter>
      <WorkflowBuilderPage />
    </MemoryRouter>,
  )
  // 冲掉 listWorkflows 的微任务，避免 act 外的状态更新
  await act(async () => {})
  return utils
}

function editButton(container: HTMLElement, index: number): HTMLButtonElement {
  const card = container.querySelectorAll<HTMLElement>('.role-card')[index]
  return card.querySelectorAll<HTMLButtonElement>('.role-card-foot button')[1]
}

function builtinCard(container: HTMLElement, index: number): HTMLElement {
  return container.querySelectorAll<HTMLElement>('.builtin-card')[index]
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.listWorkflows.mockResolvedValue(BUILTIN_WORKFLOWS)
  mocks.refetch.mockResolvedValue({
    data: [{ ...mocks.workflows[0], version: 2 }, mocks.workflows[1]],
  })
})

describe('WorkflowBuilderPage builtin templates', () => {
  it('renders the five builtin template cards with the default badge on deep', async () => {
    const { container } = await renderPage()

    const cards = container.querySelectorAll('.builtin-card')
    expect(cards).toHaveLength(5) // custom="True" 的行不进模板区
    expect([...cards].map((card) => card.querySelector('code')?.textContent)).toEqual([
      'deep',
      'quick',
      'reviewed',
      'auto',
      'teams',
    ])
    expect(screen.getByText('深度研究')).toBeInTheDocument()
    expect(screen.getAllByText('默认')).toHaveLength(1)
    expect(cards[0]).toHaveTextContent('默认')
    // 迷你流程链：deep 含反思循环
    expect(within(cards[0] as HTMLElement).getByText('反思循环')).toBeInTheDocument()
    // 运行时编排模板（auto/teams）有标记
    expect(cards[3]).toHaveTextContent('运行时编排')
    expect(cards[4]).toHaveTextContent('运行时编排')
  })

  it('clones deep into a prefilled create session and saves via create with a unique name', async () => {
    const { container } = await renderPage()

    fireEvent.click(within(builtinCard(container, 0)).getByRole('button', { name: /克隆为自定义/ }))
    const editor = screen.getByTestId('workflow-editor')
    expect(editor.dataset.workflow).toMatch(/^deep-copy-/)
    expect(JSON.parse(editor.dataset.steps!)).toEqual([
      { kind: 'agent', agent: 'planner' },
      { kind: 'agent', agent: 'researcher' },
      { kind: 'reflect_loop', reflector: 'reflector', researcher: 'researcher' },
      { kind: 'agent', agent: 'synthesizer' },
    ])

    fireEvent.click(screen.getByTestId('save-editor'))
    expect(mocks.updateMutate).not.toHaveBeenCalled()
    expect(mocks.createMutate).toHaveBeenCalledTimes(1)
    expect(mocks.createMutate.mock.calls[0][0].name).toMatch(/^deep-copy-/)
  })

  it('falls back to a blank studio for runtime-orchestrated templates', async () => {
    const { container } = await renderPage()

    fireEvent.click(within(builtinCard(container, 3)).getByRole('button', { name: /克隆为自定义/ }))
    expect(screen.getByTestId('workflow-editor')).toHaveAttribute('data-workflow', 'new')
  })
})

describe('WorkflowBuilderPage edit sessions', () => {
  it('does not let an old save response close or contaminate a newer edit session', async () => {
    const { container } = await renderPage()

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
    const { container } = await renderPage()

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
    const { container } = await renderPage()

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
