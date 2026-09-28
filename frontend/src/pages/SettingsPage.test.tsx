import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import type { ConfigView } from '../types'
import SettingsPage from './SettingsPage'

const mocks = vi.hoisted(() => ({
  useConfig: vi.fn(),
  useUpdateConfig: vi.fn(),
  useModels: vi.fn(),
  useSearchKeys: vi.fn(),
  mutate: vi.fn(),
}))

vi.mock('../hooks/useConfig', () => ({
  useConfig: mocks.useConfig,
  useUpdateConfig: mocks.useUpdateConfig,
}))

vi.mock('../hooks/useCatalog', () => ({
  useModels: mocks.useModels,
  useSearchKeys: mocks.useSearchKeys,
}))

vi.mock('../hooks/useSearchProfiles', () => ({ useSearchProfiles: () => ({ data: undefined }) }))

const QUALITY_SCHEMA = [
  {
    key: 'survey_min_citations',
    label: '综述引用下限',
    group: '引用与来源',
    kind: 'int',
    help: '文献综述至少要引用多少个不同的已核验来源。达不到时先扩大检索再返工。',
    min: 1,
    max: 200,
    unit: '篇',
    default: 20,
  },
  {
    key: 'register_check',
    label: '学术文体检查',
    group: '文体与结构',
    kind: 'bool',
    help: '检查口语化措辞与成对套话句式，命中后要求写作者改写。',
    min: null,
    max: null,
    unit: '',
    default: true,
  },
]

vi.mock('../hooks/useWorkbench', () => ({
  useQualitySchema: () => ({ data: QUALITY_SCHEMA, isLoading: false, isError: false }),
}))

const CONFIG: ConfigView = {
  llm_model: 'gpt-test',
  llm_base_url: null,
  llm_api_key_set: true,
  llm_api_key_hint: '***1234',
  tavily_api_key_set: true,
  tavily_api_key_hint: '***5678',
  serper_api_key_set: false,
  serper_api_key_hint: '',
  xai_api_key_set: false,
  xai_api_key_hint: '',
  search_backends: ['tavily', 'openalex'],
  max_sub_questions: 5,
  max_rounds: 2,
  max_concurrency: 4,
  results_per_search: 5,
  fulltext_enabled: true,
  fulltext_max_chars: 12000,
  request_timeout: 60,
  max_run_seconds: 3600,
  require_corroboration: false,
  quality: { survey_min_citations: 20, register_check: true },
}

describe('SettingsPage 严格双源门禁默认值', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useConfig.mockReturnValue({
      data: CONFIG,
      isLoading: false,
      isError: false,
      error: null,
    })
    mocks.useUpdateConfig.mockReturnValue({
      mutate: mocks.mutate,
      isSuccess: false,
      isPending: false,
      isError: false,
      error: null,
    })
    mocks.useModels.mockReturnValue({ data: [] })
    mocks.useSearchKeys.mockReturnValue({ data: [] })
  })

  it('显示当前状态并将开启值保存到全局配置', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    )

    const gate = await screen.findByRole('switch', { name: '严格双源门禁' })
    expect(gate).not.toBeChecked()

    await user.click(gate)
    await user.click(screen.getByRole('button', { name: '保存设置' }))

    expect(mocks.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ require_corroboration: true }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })

  it('在「高级」里提供工作流构建与角色广场入口', () => {
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    )
    const advanced = screen.getByRole('region', { name: '高级' })
    expect(within(advanced).getByRole('link', { name: /工作流构建/ })).toHaveAttribute(
      'href',
      '/workflows',
    )
    expect(within(advanced).getByRole('link', { name: /角色广场/ })).toHaveAttribute(
      'href',
      '/agents',
    )
  })

  it('交付质量字段可编辑并随全局配置保存，悬浮说明可被读屏读到', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    )
    const quality = screen.getByRole('region', { name: '交付质量' })
    const minimum = within(quality).getByRole('spinbutton', { name: '综述引用下限' })
    expect(minimum).toHaveValue(20)
    // 说明文本通过 aria-describedby 关联到输入框，悬停/聚焦时显示
    expect(minimum).toHaveAccessibleDescription(/至少要引用多少个不同的已核验来源/)
    await user.hover(within(quality).getByRole('button', { name: '综述引用下限说明' }))
    expect(within(quality).getAllByRole('tooltip')[0]).toHaveTextContent('扩大检索')

    await user.clear(minimum)
    await user.type(minimum, '30')
    await user.click(within(quality).getByRole('switch', { name: '学术文体检查' }))
    await user.click(screen.getByRole('button', { name: '保存设置' }))
    expect(mocks.mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        quality: expect.objectContaining({ survey_min_citations: 30, register_check: false }),
      }),
      expect.anything(),
    )
  })

  it('每个研究行为字段都有悬浮说明', () => {
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    )
    for (const name of ['子问题数上限', '反思补洞轮数', '请求超时（秒）']) {
      expect(screen.getByRole('spinbutton', { name })).toHaveAccessibleDescription(/.{20,}/)
    }
  })

  it('保存 arXiv 全文开关与字符预算', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    )

    const fulltext = await screen.findByRole('switch', { name: '启用 arXiv LaTeX 全文' })
    expect(fulltext).toBeChecked()
    await user.click(fulltext)
    await user.click(screen.getByRole('button', { name: '保存设置' }))

    expect(mocks.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ fulltext_enabled: false, fulltext_max_chars: 12000 }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })

  it('保存所选检索后端', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    )

    const serper = await screen.findByRole('checkbox', { name: 'Serper' })
    await user.click(serper)
    await user.click(screen.getByRole('button', { name: /保存设置/ }))

    expect(mocks.mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        search_profile_ids: ['builtin:tavily', 'builtin:openalex', 'builtin:serper'],
      }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })
})
