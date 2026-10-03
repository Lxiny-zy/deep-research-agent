import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { TaskTemplate } from '../types'
import NewResearchPage from './NewResearchPage'

const mocks = vi.hoisted(() => ({
  listWorkflows: vi.fn(),
  createRun: vi.fn(),
  assessIntent: vi.fn(),
  parseDatasetFile: vi.fn(),
}))

const COLUMNS = [
  { name: 'method', type: '文本' },
  { name: 'psnr', type: '数值' },
]

function sheet(name: string, csv: string, rows: number) {
  return { name, csv, rows, columns: COLUMNS, chars: csv.length }
}

vi.mock('../api/client', () => mocks)
vi.mock('../hooks/useConfig', () => ({
  useConfig: () => ({ data: { require_corroboration: false } }),
}))
vi.mock('../hooks/useLibrary', () => ({
  useProjects: () => ({ data: [{ id: 'p1', name: '光谱重建', included_source_count: 2 }] }),
}))
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
    supports_library: !['peerReview', 'paperRead', 'dataAnalysis'].includes(key),
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
  {
    ...template('litReview', '文献综述', 'lit_review'),
    default_strategy: 'deep' as const,
    strategies: [
      { key: 'deep' as const, label: '深度检索', description: '检索文献', workflow: 'lit_review' },
      {
        key: 'none' as const,
        label: '仅指定文献',
        description: '比较指定材料',
        workflow: 'lit_review_provided',
      },
    ],
  },
  template('peerReview', '同行评审', 'peer_review'),
  template('dataAnalysis', '数据分析', 'data_analysis'),
  ...(['slides', 'mindmap'] as const).map((key) => ({
    ...template(key, key === 'slides' ? '幻灯片' : '思维导图', key),
    default_strategy: 'quick' as const,
    strategies: [
      { key: 'quick' as const, label: '快速检索', description: '检索资料', workflow: key },
      {
        key: 'none' as const,
        label: '仅指定材料',
        description: '整理指定材料',
        workflow: `${key}_provided`,
      },
    ],
  })),
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

  it('only offers a library project to templates that consume library search', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    expect(await screen.findByLabelText(/资料库项目/)).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText(/同行评审/))
    expect(screen.queryByLabelText(/资料库项目/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText(/数据分析/))
    expect(screen.queryByLabelText(/资料库项目/)).not.toBeInTheDocument()
  })

  it('requires failed attachments to be removed before submitting a partial selection', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(/文献综述/))
    fireEvent.change(screen.getByLabelText('文献综述输入'), { target: { value: '研究这些文件' } })
    fireEvent.change(screen.getByLabelText('上传附件'), {
      target: { files: [new File(['bad'], 'bad.exe')] },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始文献综述' }))
    expect(await screen.findByText(/有附件未成功解析/)).toBeInTheDocument()
    expect(mocks.createRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '移除 bad.exe' }))
    fireEvent.click(screen.getByRole('button', { name: '开始文献综述' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
  })

  it('submits a specialised template directly with its key and no workflow', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.change(await screen.findByLabelText(/资料库项目/), { target: { value: 'p1' } })
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
      project_id: null,
      clarified: true,
    })
    // 专项任务不经意图澄清；档位默认取模板的 tier_default
    expect(mocks.assessIntent).not.toHaveBeenCalled()
    expect(body.tier).toBe('standard')
  })

  it('submits a closed review without the previously selected library project', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(/文献综述/))
    fireEvent.change(await screen.findByLabelText(/资料库项目/), { target: { value: 'p1' } })
    fireEvent.click(screen.getByLabelText('仅指定文献'))
    expect(screen.queryByLabelText(/资料库项目/)).not.toBeInTheDocument()
    expect(screen.getByText(/会逐份核对并引用/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('文献综述输入'), {
      target: { value: '比较 https://paper.test/a.pdf 和 https://paper.test/b.pdf' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始文献综述' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({
      template: 'litReview',
      strategy: 'none',
      workflow: null,
      project_id: null,
    })
    expect(mocks.assessIntent).not.toHaveBeenCalled()
  })

  it('submits a follow-up with the source run library project', async () => {
    render(
      <MemoryRouter initialEntries={['/?followup=1&project=p1']}>
        <NewResearchPage />
      </MemoryRouter>,
    )
    expect(await screen.findByLabelText(/资料库项目/)).toHaveValue('p1')
    fireEvent.click(screen.getByLabelText(/文献综述/))
    fireEvent.change(screen.getByLabelText('文献综述输入'), {
      target: { value: '进一步比较这些方法的局限' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始文献综述' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({ project_id: 'p1' })
  })

  it('shows an unavailable inherited library and allows explicitly clearing it', async () => {
    render(
      <MemoryRouter initialEntries={['/?followup=1&project=removed-project']}>
        <NewResearchPage />
      </MemoryRouter>,
    )
    const project = await screen.findByLabelText(/资料库项目/)
    expect(project).toHaveValue('removed-project')
    expect(screen.getByRole('option', { name: /原资料库项目暂不可用/ })).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText(/文献综述/))
    fireEvent.change(screen.getByLabelText('文献综述输入'), {
      target: { value: '改用公开资料继续比较' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始文献综述' }))
    expect(await screen.findByText(/所选资料库项目已不可用/)).toBeInTheDocument()
    expect(mocks.createRun).not.toHaveBeenCalled()
    fireEvent.change(project, { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '开始文献综述' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({ project_id: null })
  })

  it('restores the library project when switching back to an open review', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(/文献综述/))
    fireEvent.change(await screen.findByLabelText(/资料库项目/), { target: { value: 'p1' } })
    fireEvent.click(screen.getByLabelText('仅指定文献'))
    fireEvent.click(screen.getByLabelText('深度检索'))
    expect(screen.getByLabelText(/资料库项目/)).toHaveValue('p1')
    fireEvent.change(screen.getByLabelText('文献综述输入'), {
      target: { value: '高光谱重建方法综述' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始文献综述' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({
      template: 'litReview',
      strategy: 'deep',
      project_id: 'p1',
    })
  })

  it.each([
    ['slides', '幻灯片'],
    ['mindmap', '思维导图'],
  ])('submits %s with closed materials and restores open-search choices', async (key, title) => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(new RegExp(title)))
    fireEvent.change(await screen.findByLabelText(/资料库项目/), { target: { value: 'p1' } })
    fireEvent.click(screen.getByLabelText('仅指定材料'))
    expect(screen.queryByLabelText(/资料库项目/)).not.toBeInTheDocument()
    expect(screen.getByText(/不补充外部资料/)).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('快速检索'))
    expect(screen.getByLabelText(/资料库项目/)).toHaveValue('p1')
    fireEvent.click(screen.getByLabelText('仅指定材料'))
    fireEvent.change(screen.getByLabelText(`${title}输入`), {
      target: { value: '整理 https://paper.test/a.pdf 和 https://paper.test/b.pdf' },
    })
    fireEvent.click(screen.getByRole('button', { name: `开始${title}` }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({
      template: key,
      strategy: 'none',
      workflow: null,
      project_id: null,
    })
    expect(mocks.assessIntent).not.toHaveBeenCalled()
  })

  it('lets the user override the tier and shows the daily quota', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    expect(screen.getByText(/今日研究 2\/10 次/)).toBeInTheDocument()
    expect(screen.queryByText(/预算.*token/)).not.toBeInTheDocument()
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

  it('attaches an uploaded CSV as the dataset field with its source', async () => {
    const csv = 'method,psnr\nA,30\nB,31\n'
    mocks.parseDatasetFile.mockResolvedValue({
      filename: 'psnr.csv',
      sheets: [sheet('', csv, 2)],
      skipped: [],
    })
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(/数据分析/))
    const file = new File([csv], 'psnr.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByLabelText(/上传 CSV/), { target: { files: [file] } })
    expect(await screen.findByText(/已选择 psnr.csv/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('数据分析输入'), { target: { value: '差异显著吗？' } })
    fireEvent.click(screen.getByRole('button', { name: '开始数据分析' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({
      template: 'dataAnalysis',
      dataset: csv,
      dataset_source: { filename: 'psnr.csv', sheet: '' },
      demo_data: false,
    })
  })

  it('requires a sheet choice for multi-sheet workbooks', async () => {
    mocks.parseDatasetFile.mockResolvedValue({
      filename: 'runs.xlsx',
      sheets: [sheet('CAVE', 'method,psnr\nA,30\n', 1), sheet('KAIST', 'method,psnr\nB,31\n', 1)],
      skipped: [],
    })
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(/数据分析/))
    const file = new File(['x'], 'runs.xlsx')
    fireEvent.change(screen.getByLabelText(/上传 CSV/), { target: { files: [file] } })
    const select = await screen.findByLabelText('分析哪张工作表')
    fireEvent.change(screen.getByLabelText('数据分析输入'), { target: { value: '差异显著吗？' } })
    fireEvent.click(screen.getByRole('button', { name: '开始数据分析' }))
    expect(await screen.findByText(/请先选择要分析的一张/)).toBeInTheDocument()
    expect(mocks.createRun).not.toHaveBeenCalled()

    fireEvent.change(select, { target: { value: 'KAIST' } })
    fireEvent.click(screen.getByRole('button', { name: '开始数据分析' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({
      dataset: 'method,psnr\nB,31\n',
      dataset_source: { filename: 'runs.xlsx', sheet: 'KAIST' },
    })
  })

  it('only sends demo data when the user opts in', async () => {
    render(
      <MemoryRouter>
        <NewResearchPage />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText(/数据分析/))
    fireEvent.click(screen.getByLabelText(/用示例数据演示/))
    fireEvent.change(screen.getByLabelText('数据分析输入'), { target: { value: '差异显著吗？' } })
    fireEvent.click(screen.getByRole('button', { name: '开始数据分析' }))
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalled())
    expect(mocks.createRun.mock.calls[0][0]).toMatchObject({ dataset: null, demo_data: true })
  })
})
