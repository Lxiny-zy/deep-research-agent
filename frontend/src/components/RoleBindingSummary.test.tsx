import { render, screen } from '@testing-library/react'
import RoleBindingSummary from './RoleBindingSummary'
import type { ResearchEvent } from '../types'

it('shows actual role model bindings without treating defaults as custom cards', () => {
  const event = (binding: unknown): ResearchEvent => ({
    stage: 'ORCHESTRATOR', type: 'info', elapsed: 0, message: '', data: { role_binding: binding },
  })
  const binding = { role: 'survey_writer', card: '领域综述', profile: '写作档案', model: 'model-a', model_source: 'card' }
  render(<RoleBindingSummary events={[
    event(binding), event(binding), event({ role: 'researcher', model_source: 'default' }), event({ role: [] }),
  ]} />)
  expect(screen.getByText('本次角色与模型配置 · 2 个角色')).toBeInTheDocument()
  expect(screen.getByText('撰写文献综述')).toBeInTheDocument()
  expect(screen.getByText(/卡片：领域综述.*model-a.*使用卡片绑定模型/)).toBeInTheDocument()
  expect(screen.getByText(/内置角色.*使用默认模型/)).toBeInTheDocument()
})
