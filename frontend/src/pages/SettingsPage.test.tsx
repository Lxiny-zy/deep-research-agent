import { render, screen } from '@testing-library/react'
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

vi.mock('../hooks/useRevealOnScroll', () => ({ useRevealOnScroll: vi.fn() }))

const CONFIG: ConfigView = {
  llm_model: 'gpt-test',
  llm_base_url: null,
  llm_api_key_set: true,
  llm_api_key_hint: '***1234',
  tavily_api_key_set: true,
  tavily_api_key_hint: '***5678',
  max_sub_questions: 5,
  max_rounds: 2,
  max_concurrency: 4,
  results_per_search: 5,
  fulltext_enabled: true,
  fulltext_max_chars: 12000,
  request_timeout: 60,
  max_run_seconds: 3600,
  require_corroboration: false,
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
})
