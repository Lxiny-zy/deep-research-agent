import { act, fireEvent, render, screen } from '@testing-library/react'
import type { AgentCard, AgentCardInput, ModelProfile, ModelProfileInput, RoleInfo } from '../types'
import AgentSquarePage from './AgentSquarePage'

const mocks = vi.hoisted(() => ({
  agentCreate: vi.fn(),
  agentUpdate: vi.fn(),
  agentRemove: vi.fn(),
  modelCreate: vi.fn(),
  modelUpdate: vi.fn(),
  modelRemove: vi.fn(),
  keyCreate: vi.fn(),
  keyUpdate: vi.fn(),
  keyRemove: vi.fn(),
}))

// /api/roles 已按管线序返回内置角色；混入一个自定义角色验证内置区会过滤
const ROLES: RoleInfo[] = [
  { name: 'planner', label: '规划师', description: '拆解子问题', icon: '', builtin: true },
  { name: 'researcher', label: '研究员', description: '并行检索', icon: '', builtin: true },
  { name: 'reflector', label: '反思者', description: '评估证据', icon: '', builtin: true },
  {
    name: 'synthesizer',
    label: '综合者',
    description: '综合成报告',
    icon: '',
    builtin: true,
    produces_report: true,
  },
  { name: 'critic', label: '评审员', description: '批判性复核', icon: '', builtin: true },
  { name: 'my-researcher', label: '定制检索员', description: '', icon: '', builtin: false },
]

const agent: AgentCard = {
  id: 'agent-1',
  name: 'my-researcher',
  display_name: '定制检索员',
  description: '',
  behavior: 'research',
  system_prompt: '',
  icon: '',
  enabled: true,
  model_profile_id: null,
  model_profile_name: null,
}

vi.mock('../hooks/useCatalog', () => ({
  useAgents: () => ({ data: [agent], isLoading: false, isError: false }),
  useRoles: () => ({ data: ROLES, isLoading: false, isError: false }),
  useModels: () => ({ data: [], isLoading: false, isError: false }),
  useSearchKeys: () => ({ data: [], isLoading: false, isError: false }),
  useAgentMutations: () => ({
    create: { mutate: mocks.agentCreate },
    update: { mutate: mocks.agentUpdate },
    remove: { mutate: mocks.agentRemove },
  }),
  useModelMutations: () => ({
    create: { mutate: mocks.modelCreate },
    update: { mutate: mocks.modelUpdate },
    remove: { mutate: mocks.modelRemove },
  }),
  useSearchKeyMutations: () => ({
    create: { mutate: mocks.keyCreate, isPending: false },
    update: { mutate: mocks.keyUpdate },
    remove: { mutate: mocks.keyRemove },
  }),
}))

vi.mock('../components/AgentCardEditor', () => ({
  default: ({
    initial,
    onSubmit,
    onCancel,
    pending,
    error,
  }: {
    initial: AgentCard | null
    onSubmit: (body: AgentCardInput) => void
    onCancel: () => void
    pending?: boolean
    error?: string
  }) => (
    <div data-testid="agent-editor" data-agent={initial?.name ?? 'new'}>
      <button type="button" data-testid="agent-save" onClick={() => onSubmit({})}>
        save
      </button>
      <button type="button" data-testid="agent-close" onClick={onCancel}>
        close
      </button>
      <span data-testid="agent-pending">{String(!!pending)}</span>
      {error && <span data-testid="agent-error">{error}</span>}
    </div>
  ),
}))

vi.mock('../components/ModelProfileEditor', () => ({
  default: ({
    initial,
    onSubmit,
    onCancel,
    pending,
    error,
  }: {
    initial: ModelProfile | null
    onSubmit: (body: ModelProfileInput) => void
    onCancel: () => void
    pending?: boolean
    error?: string
  }) => (
    <div data-testid="model-editor" data-model={initial?.name ?? 'new'}>
      <button type="button" data-testid="model-save" onClick={() => onSubmit({})}>
        save
      </button>
      <button type="button" data-testid="model-close" onClick={onCancel}>
        close
      </button>
      <span data-testid="model-pending">{String(!!pending)}</span>
      {error && <span data-testid="model-error">{error}</span>}
    </div>
  ),
}))

describe('AgentSquarePage builtin roles', () => {
  it('renders builtin role cards in pipeline order, read-only, with the report badge', () => {
    const { container } = render(<AgentSquarePage />)

    const cards = [...container.querySelectorAll<HTMLElement>('.builtin-role-card')]
    expect(cards).toHaveLength(5) // 自定义角色不进内置区
    expect(cards.map((card) => card.querySelector('strong')?.textContent)).toEqual([
      '规划师',
      '研究员',
      '反思者',
      '综合者',
      '评审员',
    ])
    // 英文标识 + 管线位次编号
    expect(cards[0].querySelector('code')?.textContent).toBe('planner')
    expect(cards[0]).toHaveTextContent('01')
    expect(cards[4]).toHaveTextContent('05')
    // 「产出报告」徽章仅 synthesizer
    expect(screen.getAllByText('产出报告')).toHaveLength(1)
    expect(cards[3]).toHaveTextContent('产出报告')
    // 只读：卡内不渲染任何操作按钮
    for (const card of cards) expect(card.querySelectorAll('button')).toHaveLength(0)
  })
})

describe('AgentSquarePage edit sessions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('does not leak an agent save failure into the next editor session', () => {
    render(<AgentSquarePage />)

    fireEvent.click(screen.getByRole('button', { name: /新建角色/ }))
    fireEvent.click(screen.getByTestId('agent-save'))
    expect(screen.getByTestId('agent-pending')).toHaveTextContent('true')

    const callbacks = mocks.agentCreate.mock.calls[0][1]
    act(() => {
      callbacks.onError(new Error('名称已存在'))
      callbacks.onSettled()
    })
    expect(screen.getByTestId('agent-error')).toHaveTextContent('名称已存在')
    expect(screen.getByTestId('agent-pending')).toHaveTextContent('false')

    fireEvent.click(screen.getByTestId('agent-close'))
    fireEvent.click(screen.getByRole('button', { name: /新建角色/ }))
    expect(screen.queryByTestId('agent-error')).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-pending')).toHaveTextContent('false')
  })

  it('keeps a stale agent failure out of a newer session and lets success close only its own', () => {
    render(<AgentSquarePage />)

    fireEvent.click(screen.getByRole('button', { name: /编辑/ }))
    expect(screen.getByTestId('agent-editor')).toHaveAttribute('data-agent', 'my-researcher')
    fireEvent.click(screen.getByTestId('agent-save'))
    const oldCallbacks = mocks.agentUpdate.mock.calls[0][1]

    fireEvent.click(screen.getByTestId('agent-close'))
    fireEvent.click(screen.getByRole('button', { name: /新建角色/ }))
    expect(screen.getByTestId('agent-editor')).toHaveAttribute('data-agent', 'new')

    act(() => {
      oldCallbacks.onError(new Error('stale failure'))
      oldCallbacks.onSettled()
    })
    expect(screen.queryByTestId('agent-error')).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-editor')).toHaveAttribute('data-agent', 'new')
  })

  it('does not leak a model save failure into the next editor session', () => {
    render(<AgentSquarePage />)

    fireEvent.click(screen.getByRole('tab', { name: /模型档案/ }))
    fireEvent.click(screen.getByRole('button', { name: /新建档案/ }))
    fireEvent.click(screen.getByTestId('model-save'))
    expect(screen.getByTestId('model-pending')).toHaveTextContent('true')

    const callbacks = mocks.modelCreate.mock.calls[0][1]
    act(() => {
      callbacks.onError(new Error('base_url 无效'))
      callbacks.onSettled()
    })
    expect(screen.getByTestId('model-error')).toHaveTextContent('base_url 无效')

    fireEvent.click(screen.getByTestId('model-close'))
    fireEvent.click(screen.getByRole('button', { name: /新建档案/ }))
    expect(screen.queryByTestId('model-error')).not.toBeInTheDocument()
    expect(screen.getByTestId('model-pending')).toHaveTextContent('false')
  })

  it('closes the editor only after a successful save', () => {
    render(<AgentSquarePage />)

    fireEvent.click(screen.getByRole('button', { name: /新建角色/ }))
    fireEvent.click(screen.getByTestId('agent-save'))
    const callbacks = mocks.agentCreate.mock.calls[0][1]
    act(() => {
      callbacks.onSuccess()
      callbacks.onSettled()
    })
    expect(screen.queryByTestId('agent-editor')).not.toBeInTheDocument()
  })
})
