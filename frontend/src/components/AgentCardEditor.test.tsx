import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import AgentCardEditor from './AgentCardEditor'
import { previewRolePrompt } from '../api/client'
import type { AgentCard, SearchProfile } from '../types'

vi.mock('../api/client', () => ({ previewRolePrompt: vi.fn() }))

const searchProfiles: SearchProfile[] = [
  {
    id: 'news',
    name: '新闻联网模型',
    provider: 'responses',
    endpoint: 'https://example.com/responses',
    model: 'search',
    key_ids: ['key-1'],
    enabled: true,
    builtin: false,
  },
]

it('新建研究角色默认补充提示词，专属检索必须选择档案', () => {
  const save = vi.fn()
  render(
    <AgentCardEditor
      profiles={[]}
      searchProfiles={searchProfiles}
      onSubmit={save}
      onCancel={vi.fn()}
    />,
  )
  fireEvent.change(screen.getByPlaceholderText('如 my-critic'), {
    target: { value: 'news-reader' },
  })
  expect(screen.getByRole('combobox', { name: '提示词模式' })).toHaveValue('append')
  fireEvent.click(screen.getByRole('checkbox', { name: '继承全局默认检索档案' }))
  expect(screen.getByRole('button', { name: '保存角色' })).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: '新闻联网模型' }))
  fireEvent.click(screen.getByRole('button', { name: '保存角色' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({ prompt_mode: 'append', search_profile_ids: ['news'] }),
  )
})

it('旧角色保留替换模式，并可恢复继承全局检索', () => {
  const save = vi.fn()
  const initial: AgentCard = {
    id: 'a',
    name: 'custom',
    display_name: 'Custom',
    description: '',
    behavior: 'research',
    system_prompt: 'old',
    icon: 'search',
    enabled: true,
    model_profile_id: null,
    model_profile_name: null,
    search_profile_ids: ['news'],
  }
  render(
    <AgentCardEditor
      initial={initial}
      profiles={[]}
      searchProfiles={searchProfiles}
      onSubmit={save}
      onCancel={vi.fn()}
    />,
  )
  expect(screen.getByRole('combobox', { name: '提示词模式' })).toHaveValue('replace')
  fireEvent.click(screen.getByRole('checkbox', { name: '继承全局默认检索档案' }))
  fireEvent.click(screen.getByRole('button', { name: '保存角色' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({ prompt_mode: 'replace', search_profile_ids: null }),
  )
})

it('预览来自后端，修改指令后隐藏过期预览', async () => {
  vi.mocked(previewRolePrompt).mockResolvedValue({
    default_prompt: 'default',
    contract: 'fixed',
    global_rules: 'rules',
    effective_system_prompt: 'rendered runtime prompt',
  })
  render(<AgentCardEditor profiles={[]} onSubmit={vi.fn()} onCancel={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '预览最终提示词' }))
  await waitFor(() => expect(screen.getByText('rendered runtime prompt')).toBeInTheDocument())
  fireEvent.change(screen.getByRole('textbox', { name: '角色指令（留空使用内置默认）' }), {
    target: { value: 'new instruction' },
  })
  expect(screen.queryByText('rendered runtime prompt')).not.toBeInTheDocument()
})
