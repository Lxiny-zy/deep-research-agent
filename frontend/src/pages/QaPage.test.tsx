import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import QaPage from './QaPage'
import type { QaConversation } from '../types'

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  createConversation: vi.fn(),
  askQuestion: vi.fn(),
  deleteConversation: vi.fn(),
}))
vi.mock('../api/client', () => mocks)

const conversation: QaConversation = {
  id: 'c1',
  title: '误差文献',
  created_at: null,
  updated_at: null,
  message_count: 1,
  messages: [
    {
      id: 'm1',
      position: 0,
      query: 'CASSI 是什么？',
      answer: 'CASSI 是编码孔径快照光谱成像 [1]。',
      citations: ['https://a.com'],
      evidence: [
        {
          statement: 's',
          source_url: 'https://a.com',
          evidence_quote: 'q',
          source_reference: 'Wagadarikar 2008',
        },
      ],
      thoughts: [{ tool: 'search_and_verify', input: 'q', observation: '保留 1 条已核验证据' }],
      status: 'done',
      created_at: null,
    },
  ],
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/qa/:id?" element={<QaPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('QaPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listConversations.mockResolvedValue([{ ...conversation, messages: [] }])
    mocks.getConversation.mockResolvedValue(conversation)
  })

  it('renders answers with citations and the verification trail', async () => {
    renderAt('/qa/c1')
    expect(await screen.findByText('CASSI 是什么？')).toBeInTheDocument()
    const answer = screen.getByRole('list', { name: '引用来源' })
    expect(within(answer).getByRole('link', { name: 'Wagadarikar 2008' })).toHaveAttribute(
      'href',
      'https://a.com',
    )
    // 右侧面板把本会话引用汇总一次（去重）
    const panel = screen.getByRole('complementary', { name: '问答说明与引用' })
    expect(within(panel).getAllByRole('link', { name: 'Wagadarikar 2008' })).toHaveLength(1)
    expect(screen.getByText('保留 1 条已核验证据')).toBeInTheDocument()
  })

  it('creates a conversation on the first question', async () => {
    mocks.createConversation.mockResolvedValue({ ...conversation, id: 'c2', messages: [] })
    mocks.askQuestion.mockResolvedValue(conversation.messages[0])
    renderAt('/qa')
    fireEvent.change(screen.getByLabelText('输入问题'), { target: { value: '新问题' } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    await waitFor(() => expect(mocks.askQuestion).toHaveBeenCalledWith('c2', '新问题'))
    expect(mocks.createConversation).toHaveBeenCalledWith('新问题')
  })

  it('shows errors without losing the page', async () => {
    mocks.askQuestion.mockRejectedValue(new Error('服务不可用'))
    renderAt('/qa/c1')
    await screen.findByText('CASSI 是什么？')
    fireEvent.change(screen.getByLabelText('输入问题'), { target: { value: '追问' } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('服务不可用')
  })
})
