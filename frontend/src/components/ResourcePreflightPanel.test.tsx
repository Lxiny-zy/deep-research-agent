import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { checkResourcePreflight } from '../api/client'
import ResourcePreflightPanel from './ResourcePreflightPanel'

vi.mock('../api/client', () => ({ checkResourcePreflight: vi.fn() }))

it('检查后展示角色实际资源和缺失凭据，并提供修复入口', async () => {
  vi.mocked(checkResourcePreflight).mockResolvedValue({
    ok: false,
    workflow: 'custom',
    errors: ['新闻角色：没有可用 Key'],
    warnings: ['未发送联网请求'],
    roles: [
      {
        role: 'news',
        model: 'research-model',
        model_profile: '推理模型',
        inherits_search: false,
        search_profiles: [{ id: 'p', name: '新闻检索', ready: false, key_count: 0 }],
      },
    ],
  })
  render(
    <MemoryRouter>
      <ResourcePreflightPanel workflow="custom" />
    </MemoryRouter>,
  )
  fireEvent.click(screen.getByRole('button', { name: '检查角色与检索配置' }))
  expect(await screen.findByText('新闻角色：没有可用 Key')).toBeInTheDocument()
  expect(screen.getByText(/专属检索：新闻检索/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '管理检索资源' })).toHaveAttribute(
    'href',
    '/agents?tab=keys',
  )
  expect(checkResourcePreflight).toHaveBeenCalledWith('custom')
})
