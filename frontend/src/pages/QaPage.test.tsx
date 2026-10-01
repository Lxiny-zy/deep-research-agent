import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import QaPage from './QaPage'
import { RequestTimeoutError } from '../api/transport'
import type { QaConversation } from '../types'

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  createConversation: vi.fn(),
  askQuestion: vi.fn(),
  deleteConversation: vi.fn(),
  listProjects: vi.fn(),
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

function PageRoutes() {
  const location = useLocation()
  return (
    <Routes key={location.pathname}>
      <Route path="/qa/:id?" element={<QaPage />} />
    </Routes>
  )
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <PageRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('QaPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listConversations.mockResolvedValue([{ ...conversation, messages: [] }])
    mocks.getConversation.mockResolvedValue(conversation)
    mocks.listProjects.mockResolvedValue([])
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
    await waitFor(() =>
      expect(mocks.askQuestion).toHaveBeenCalledWith(
        'c2',
        '新问题',
        undefined,
        { sources: [], projectId: undefined },
        expect.any(Function),
      ),
    )
    expect(mocks.createConversation).toHaveBeenCalledWith('新问题')
  })

  it('shows errors without losing the page', async () => {
    mocks.askQuestion.mockRejectedValue(new Error('服务不可用'))
    renderAt('/qa/c1')
    await screen.findByText('CASSI 是什么？')
    fireEvent.change(screen.getByLabelText('输入问题'), { target: { value: '追问' } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('服务不可用')
    expect(screen.getByLabelText('输入问题')).toHaveValue('追问')
  })

  it('shows streamed text before completion and replaces it with the validated answer', async () => {
    const final = { ...conversation.messages[0], query: '演示问题', answer: '核验后的正式结论' }
    mocks.createConversation.mockResolvedValue({ ...conversation, id: 'c2', messages: [] })
    mocks.getConversation.mockResolvedValue({ ...conversation, id: 'c2', messages: [final] })
    let finish!: (message: typeof final) => void
    mocks.askQuestion.mockImplementationOnce((_id, _query, _signal, _scope, onDelta) => {
      onDelta('正在生成的临时结论')
      return new Promise((resolve) => {
        finish = resolve
      })
    })
    renderAt('/qa')
    fireEvent.change(screen.getByLabelText('输入问题'), { target: { value: final.query } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    expect(await screen.findByTestId('qa-streaming-answer')).toHaveTextContent('正在生成的临时结论')
    expect(screen.queryByText('核验后的正式结论')).not.toBeInTheDocument()
    finish(final)
    expect(await screen.findByText('核验后的正式结论')).toBeInTheDocument()
    expect(screen.queryByTestId('qa-streaming-answer')).not.toBeInTheDocument()
  })

  it('recovers the new answer when the same question was answered after the cache loaded', async () => {
    renderAt('/qa/c1')
    await screen.findByText('CASSI 是什么？')
    const previous = { ...conversation.messages[0], id: 'm2', position: 1, answer: '上一轮回答' }
    const durable = {
      ...conversation,
      message_count: 2,
      messages: [...conversation.messages, previous],
    }
    const latest = { ...previous, id: 'm3', position: 2, answer: '这次回答已恢复' }
    mocks.getConversation
      .mockResolvedValueOnce(durable)
      .mockResolvedValueOnce(durable)
      .mockResolvedValue({ ...durable, message_count: 3, messages: [...durable.messages, latest] })
    mocks.askQuestion.mockRejectedValueOnce(new RequestTimeoutError())
    fireEvent.change(screen.getByLabelText('输入问题'), { target: { value: previous.query } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    expect(await screen.findByText('这次回答已恢复', {}, { timeout: 3500 })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('正在生成回答…')).not.toBeInTheDocument())
    expect(mocks.askQuestion).toHaveBeenCalledTimes(1)
    expect(mocks.createConversation).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps the first question visible while waiting and retries in the same conversation', async () => {
    mocks.createConversation.mockResolvedValue({ ...conversation, id: 'c2', messages: [] })
    let rejectAnswer!: (error: Error) => void
    mocks.askQuestion.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectAnswer = reject
        }),
    )
    renderAt('/qa')
    fireEvent.change(screen.getByLabelText('输入问题'), { target: { value: '首轮问题' } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    await waitFor(() =>
      expect(mocks.askQuestion).toHaveBeenCalledWith(
        'c2',
        '首轮问题',
        undefined,
        { sources: [], projectId: undefined },
        expect.any(Function),
      ),
    )
    expect(screen.getByText('正在生成回答…')).toBeInTheDocument()
    expect(screen.getByText('首轮问题')).toBeInTheDocument()
    rejectAnswer(new Error('暂时失败'))
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时失败')
    expect(screen.getByLabelText('输入问题')).toHaveValue('首轮问题')
    mocks.askQuestion.mockResolvedValueOnce(conversation.messages[0])
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    await screen.findByText('CASSI 是什么？')
    expect(mocks.createConversation).toHaveBeenCalledTimes(1)
    expect(mocks.askQuestion).toHaveBeenCalledTimes(2)
  })
})
