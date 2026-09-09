import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import SearchProfilesManager from './SearchProfilesManager'
import { listSearchProfiles, saveSearchProfile } from '../api/client'

vi.mock('../api/client', () => ({
  listSearchProfiles: vi.fn(),
  saveSearchProfile: vi.fn(),
  deleteSearchProfile: vi.fn(),
  testSearchProfile: vi.fn(),
}))

it('创建外接搜索档案时保存完整端点、模型和选中的多 Key', async () => {
  vi.mocked(listSearchProfiles).mockResolvedValue([])
  vi.mocked(saveSearchProfile).mockImplementation(async (body) => ({
    ...body,
    id: 'new',
    builtin: false,
  }))
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={qc}>
      <SearchProfilesManager
        keys={[
          {
            id: 'k1',
            provider: 'responses',
            label: '主账号',
            priority: 0,
            enabled: true,
            api_key_hint: '…1111',
          },
          {
            id: 'k2',
            provider: 'responses',
            label: '备用',
            priority: 1,
            enabled: true,
            api_key_hint: '…2222',
          },
          {
            id: 'wrong',
            provider: 'tavily',
            label: '另一个渠道',
            priority: 0,
            enabled: true,
            api_key_hint: '…3333',
          },
        ]}
      />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: '新建检索档案' }))
  fireEvent.change(screen.getByRole('textbox', { name: '档案名称' }), {
    target: { value: '外接新闻' },
  })
  fireEvent.change(screen.getByRole('combobox', { name: '检索协议' }), {
    target: { value: 'responses' },
  })
  fireEvent.change(screen.getByRole('textbox', { name: '请求端点' }), {
    target: { value: 'https://gateway.example/v1/responses' },
  })
  fireEvent.change(screen.getByRole('textbox', { name: '搜索模型' }), {
    target: { value: 'search-model' },
  })
  fireEvent.click(screen.getByRole('checkbox', { name: /主账号/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: /备用/ }))
  expect(screen.queryByRole('checkbox', { name: /另一个渠道/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '保存检索档案' }))
  await waitFor(() =>
    expect(saveSearchProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        name: '外接新闻',
        provider: 'responses',
        endpoint: 'https://gateway.example/v1/responses',
        model: 'search-model',
        key_ids: ['k1', 'k2'],
      }),
      undefined,
    ),
  )
  qc.clear()
})
