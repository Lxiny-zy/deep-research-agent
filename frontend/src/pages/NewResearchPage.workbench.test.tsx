import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { TaskTemplate } from '../types'
import NewResearchPage from './NewResearchPage'

const mocks = vi.hoisted(() => ({
  listWorkflows: vi.fn(),
  createRun: vi.fn(),
  assessIntent: vi.fn(),
}))

vi.mock('../api/client', () => mocks)
vi.mock('../hooks/useConfig', () => ({
  useConfig: () => ({ data: { require_corroboration: false } }),
}))
vi.mock('../hooks/useLibrary', () => ({ useProjects: () => ({ data: [] }) }))
vi.mock('../components/ResourcePreflightPanel', () => ({ default: () => null }))

function template(key: TaskTemplate['key'], title: string, workflow: string): TaskTemplate {
  return {
    key,
    title,
    tagline: `${title}说明`,
    description: '',
    icon: 'shield',
    workflow,
    input_kind: key === 'peerReview' ? 'paper' : key === 'dataAnalysis' ? 'dataset' : 'topic',
    input_label: `${title}输入`,
    input_placeholder: `${title}占位`,
    sections: [],
    deliverables: ['md', 'pdf'],
    examples: [],
    accepts_attachments: true,
    tier_default: 'standard',
    min_citations: 1,
    tags: [],
  }
}

const RESEARCH = {
  ...template('autoResearch', '深度研究', 'deep'),
  default_strategy: 'deep' as const,
  strategies: [
    { key: 'deep' as const, label: '深度检索', description: '多轮补洞', workflow: 'deep' },
    { key: 'quick' as const, label: '快速检索', description: '检索一轮', workflow: 'quick' },
  ],
}

const TEMPLATES = [
  RESEARCH,
  template('peerReview', '同行评审', 'peer_review'),
  template('dataAnalysis', '数据分析', 'data_analysis'),
]

vi.mock('../hooks/useWorkbench', () => ({
  useTemplates: () => ({ data: TEMPLATES }),
  useContractPreview: () => ({ data: undefined, isFetching: false, error: null }),
  useTiers: () => ({
    data: [
      {
        key: 'light',
        title: '轻量',
        description: '快',
        max_sub_questions: 3,
        max_rounds: 0,
        results_per_search: 4,
        max_tokens: 80000,
      },
      {
        key: 'standard',
        title: '标准',
        description: '常规',
        max_sub_questions: 5,
        max_rounds: 1,
        results_per_search: 5,
        max_tokens: 200000,
      },
      {
        key: 'deep',
        title: '深度',
        description: '系统',
        max_sub_questions: 8,
        max_rounds: 2,
        results_per_search: 8,
        max_tokens: 500000,
      },
    ],
  }),
  useUsage: () => ({
    data: {
      period: 'day',
      resets_at: '',
      runs: { used: 2, limit: 10 },
      tokens: { used: 0, limit: null },
      exhausted: false,
    },
  }),
}))

describe('NewResearchPage task templates', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    mocks.listWorkflows.mockResolvedValue([
      { name: 'deep', description: '', default: 'True', custom: 'False' },
    ])
    mocks.createRun.mockReset()
    mocks.createRun.mockResolvedValue({ run_id: 'r1' })
    mocks.assessIntent.mockReset()
  })

  it('submits a specialised template directly with its key and no workflow', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(/同行评审/))
    expect(screen.getByLabelText('同行评审输入')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /研究流程/ })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('同行评审输入'), {
      target: { value: 'https://arxiv.org/abs/2205.10102' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始同行评审' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    const [body] = mocks.createRun.mock.calls[0]
    expect(body).toMatchObject({
      query: 'https://arxiv.org/abs/2205.10102',
      template: 'peerReview',
      workflow: null,
      clarified: true,
    })
    // 专项任务不经意图澄清；档位默认取模板的 tier_default
    expect(mocks.assessIntent).not.toHaveBeenCalled()
    expect(body.tier).toBe('standard')
  })

  it('lets the user override the tier and shows the daily quota', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    expect(screen.getByText(/今日研究 2\/10 次/)).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('轻量'))
    fireEvent.change(screen.getByLabelText('深度研究输入'), { target: { value: '一个问题' } })
    mocks.assessIntent.mockResolvedValue({
      ready: true,
      resolved_query: '一个问题',
      question: '',
      options: [],
      gap: 'none',
      blocked: false,
      intent: 'x',
      reason: '',
    })
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({
      tier: 'light',
      template: 'autoResearch',
    })
  })

  it('submits the chosen retrieval strategy instead of a workflow', async () => {
    mocks.assessIntent.mockResolvedValue({
      ready: true,
      resolved_query: '一个问题',
      question: '',
      options: [],
      gap: 'none',
      blocked: false,
      intent: 'x',
      reason: '',
    })
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    // 深度检索是策略而不是任务：默认选中，可切换为快速检索
    expect(screen.getByRole('radiogroup', { name: '检索策略' })).toBeInTheDocument()
    expect(screen.getByLabelText('深度检索')).toBeChecked()
    fireEvent.click(screen.getByLabelText('快速检索'))
    fireEvent.change(screen.getByLabelText('深度研究输入'), { target: { value: '一个问题' } })
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({
      template: 'autoResearch',
      strategy: 'quick',
      workflow: null,
    })
  })

  it('attaches an uploaded CSV as the dataset field', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(/数据分析/))
    const file = new File(['method,psnr\nA,30\nB,31\n'], 'psnr.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByLabelText(/上传 CSV/), { target: { files: [file] } })
    expect(await screen.findByText(/已选择 psnr.csv/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('数据分析输入'), { target: { value: '差异显著吗？' } })
    fireEvent.click(screen.getByRole('button', { name: '开始数据分析' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({
      template: 'dataAnalysis',
      dataset: 'method,psnr\nA,30\nB,31\n',
    })
  })
})
