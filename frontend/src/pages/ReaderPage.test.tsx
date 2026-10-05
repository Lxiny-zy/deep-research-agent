import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReaderPage from './ReaderPage'
import { documentForEvidence } from '../lib/readerDocuments'
import { RequestTimeoutError } from '../api/transport'
import type { QaConversation, ReaderDocument, RunReader } from '../types'

const mocks = vi.hoisted(() => ({
  getReader: vi.fn(),
  getRun: vi.fn(),
  listProjects: vi.fn(),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  createConversation: vi.fn(),
  askQuestion: vi.fn(),
  getQaRequest: vi.fn(),
}))
vi.mock('../api/client', async () => ({ ...(await vi.importActual('../api/client')), ...mocks }))
vi.mock('../components/PdfViewer', () => ({
  default: (props: { documentId: string; highlight?: { quote: string; token: number } | null }) => (
    <div
      data-testid="pdf"
      data-doc={props.documentId}
      data-quote={props.highlight?.quote ?? ''}
      data-token={props.highlight?.token}
    />
  ),
}))

const ATT = 'a'.repeat(24)
const documents: ReaderDocument[] = [
  { id: `att-${ATT}`, kind: 'attachment', title: 'mst.pdf', pdf: true, note: '' },
  {
    id: 'paper-0',
    kind: 'paper',
    title: 'doi:10.1/x',
    url: 'https://doi.org/10.1/x',
    pdf: false,
    note: '该链接没有可直接显示的 PDF，可在原网站查看',
  },
]
const reader: RunReader = { run_id: 'r1', status: 'done', documents, has_report: true }

const answered: QaConversation = {
  id: 'c1',
  title: '实验',
  created_at: null,
  updated_at: null,
  message_count: 1,
  run_id: 'r1',
  messages: [
    {
      id: 'm1',
      request_id: null,
      position: 0,
      query: '用了什么数据集？',
      answer: '在 CAVE 上评测 [1]，另有综述提到 KAIST [2]。',
      citations: [`https://workspace.invalid/attachments/${ATT}?chunk=3`, 'https://b.org/survey'],
      evidence: [
        {
          statement: 's1',
          source_url: `https://workspace.invalid/attachments/${ATT}?chunk=3`,
          evidence_quote: 'PSNR reaches 38.4 dB on CAVE',
          source_reference: 'mst.pdf 第 4 页',
          origin: 'paper',
        },
        {
          statement: 's2',
          source_url: 'https://b.org/survey',
          evidence_quote: 'KAIST',
          source_title: 'A survey',
          origin: 'web',
        },
      ],
      thoughts: [],
      status: 'done',
      created_at: null,
    },
  ],
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/runs/r1/read']}>
        <Routes>
          <Route path="/runs/:id/read" element={<ReaderPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  window.dispatchEvent(new Event('dr:credentials-cleared'))
  mocks.getReader.mockResolvedValue(reader)
  mocks.getRun.mockResolvedValue({
    id: 'r1',
    query: '精读 MST',
    status: 'done',
    results: [],
    report: { markdown: '## 精读报告\n\n方法要点。', citations: [] },
  })
  mocks.listProjects.mockResolvedValue([{ id: 'p1', name: '光谱重建' }])
  mocks.listConversations.mockResolvedValue([])
  mocks.createConversation.mockResolvedValue({ ...answered, messages: [], message_count: 0 })
  mocks.askQuestion.mockResolvedValue(answered.messages[0])
  mocks.getConversation.mockResolvedValue(answered)
  mocks.getQaRequest.mockResolvedValue(answered.messages[0])
})

describe('ReaderPage', () => {
  it('continues a failed answer from its original message without fetching more sources', async () => {
    const parent = {
      ...answered.messages[0],
      status: 'fallback' as const,
      revision: { available: true },
    }
    mocks.listConversations.mockResolvedValue([{ ...answered, messages: [] }])
    mocks.getConversation.mockResolvedValue({ ...answered, messages: [parent] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '继续修订回答' }))
    await waitFor(() =>
      expect(mocks.askQuestion).toHaveBeenCalledWith(
        'c1',
        parent.query,
        expect.any(AbortSignal),
        {
          sources: [],
          projectId: undefined,
          revisionMessageId: 'm1',
          requestId: expect.any(String),
        },
        expect.any(Function),
        expect.any(Function),
      ),
    )
    expect(mocks.createConversation).not.toHaveBeenCalled()
  })

  it('reattaches a persisted pending question with its original id and source scope', async () => {
    const pending = {
      ...answered.messages[0],
      status: 'pending',
      answer: '',
      request_id: 'existing-request',
      request_payload: { sources: ['web'], project_id: null },
    }
    mocks.listConversations.mockResolvedValue([answered])
    mocks.getConversation.mockResolvedValue({ ...answered, messages: [pending] })
    let finish!: (value: unknown) => void
    mocks.askQuestion.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    renderPage()
    await waitFor(() =>
      expect(mocks.askQuestion).toHaveBeenCalledWith(
        'c1',
        pending.query,
        expect.any(AbortSignal),
        { sources: ['web'], projectId: undefined, requestId: 'existing-request' },
        expect.any(Function),
        expect.any(Function),
      ),
    )
    const final = { ...pending, status: 'done', answer: '恢复后的结果' }
    mocks.getConversation.mockResolvedValue({ ...answered, messages: [final] })
    finish(final)
    expect(await screen.findByText('恢复后的结果')).toBeInTheDocument()
    expect(mocks.createConversation).not.toHaveBeenCalled()
    expect(mocks.askQuestion).toHaveBeenCalledTimes(1)
  })

  it('keeps the parsed PDF viewer and scroll state mounted across report tab switches', async () => {
    renderPage()
    const pdf = await screen.findByTestId('pdf')
    pdf.scrollTop = 500
    fireEvent.click(screen.getByRole('tab', { name: '精读报告' }))
    expect(pdf).toBeInTheDocument()
    expect(pdf).not.toBeVisible()
    fireEvent.click(screen.getByRole('tab', { name: '原文' }))
    expect(screen.getByTestId('pdf')).toBe(pdf)
    expect(pdf).toBeVisible()
    expect(pdf.scrollTop).toBe(500)
  })

  it('streams a draft while reading and replaces it with the saved answer', async () => {
    const final = {
      ...answered.messages[0],
      answer: '已核验的精读结论',
      thoughts: [
        {
          tool: 'model_reasoning',
          input: 'test-model',
          observation: '接口返回的思考片段',
          call_id: 'call-1',
        },
      ],
    }
    mocks.getConversation.mockResolvedValue({ ...answered, messages: [], message_count: 0 })
    let finish!: (message: typeof final) => void
    mocks.askQuestion.mockImplementationOnce(
      (_id, _query, _signal, _scope, onDelta, onActivity) => {
        onActivity({
          type: 'reasoning',
          call_id: 'call-1',
          model: 'test-model',
          reasoning_delta: '接口返回的思考片段',
        })
        onDelta('精读结论正在生成')
        return new Promise((resolve) => {
          finish = resolve
        })
      },
    )
    renderPage()
    await screen.findByTestId('pdf')
    fireEvent.change(screen.getByLabelText('向这篇论文提问'), { target: { value: final.query } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    expect(await screen.findByTestId('qa-streaming-answer')).toHaveTextContent('精读结论正在生成')
    const pendingTurn = screen.getByTestId('qa-streaming-answer').closest('.qa-turn')
    expect(pendingTurn?.querySelectorAll('.qa-avatar')).toHaveLength(1)
    expect(pendingTurn?.querySelector('.brand-ring')).toHaveClass('is-active')
    expect(screen.getByText('接口返回的思考片段')).toBeVisible()
    expect(screen.queryByText('已核验的精读结论')).not.toBeInTheDocument()
    mocks.getConversation.mockResolvedValue({ ...answered, messages: [final] })
    finish(final)
    expect(await screen.findByText('已核验的精读结论')).toBeInTheDocument()
    const savedTurn = screen.getByText('已核验的精读结论').closest('.qa-turn')
    expect(savedTurn?.querySelectorAll('.qa-avatar')).toHaveLength(1)
    expect(savedTurn?.querySelector('.brand-ring')).not.toHaveClass('is-active')
    await waitFor(() => expect(screen.queryByTestId('qa-streaming-answer')).not.toBeInTheDocument())
    expect(screen.getByText('接口返回的思考片段')).toBeInTheDocument()
    expect(screen.getByText('思考过程').closest('details')).not.toHaveAttribute('open')
  })

  it('does not allow questions until the paper intake run is done', async () => {
    mocks.getReader.mockResolvedValue({ ...reader, status: 'running' })
    renderPage()
    expect(await screen.findByTestId('pdf')).toBeInTheDocument()
    expect(screen.getByLabelText('向这篇论文提问')).toBeDisabled()
    expect(screen.getByRole('button', { name: '提问' })).toBeDisabled()
    expect(screen.getByText(/论文正在导入/)).toBeInTheDocument()
  })

  it('recovers a late answer after the SSE request times out', async () => {
    mocks.askQuestion.mockRejectedValueOnce(new RequestTimeoutError())
    renderPage()
    await screen.findByTestId('pdf')
    fireEvent.change(screen.getByLabelText('向这篇论文提问'), {
      target: { value: '用了什么数据集？' },
    })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    await waitFor(() => expect(mocks.askQuestion).toHaveBeenCalledTimes(1))
    expect(await screen.findByRole('button', { name: '浏览引用 1 的来源记录' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/正在翻阅原文并核验/)).not.toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(mocks.createConversation).toHaveBeenCalledTimes(1)
  })

  it('disables questions when a completed run has no usable paper sources', async () => {
    mocks.getReader.mockResolvedValue({ ...reader, can_ask: false })
    renderPage()
    await screen.findByTestId('pdf')
    expect(screen.getByLabelText('向这篇论文提问')).toBeDisabled()
    expect(screen.getByText('没有可用的论文原文材料，暂时不可提问')).toBeInTheDocument()
    expect(mocks.askQuestion).not.toHaveBeenCalled()
  })

  it('does not recover an old repeated answer from a stale conversation cache', async () => {
    mocks.listConversations.mockResolvedValue([answered])
    renderPage()
    await screen.findByRole('button', { name: '浏览引用 1 的来源记录' })
    const oldRepeat = { ...answered.messages[0], id: 'm2', position: 1, answer: '之前的回答' }
    const durable = { ...answered, message_count: 2, messages: [...answered.messages, oldRepeat] }
    const latest = { ...oldRepeat, id: 'm3', position: 2, answer: '本轮迟到的回答' }
    mocks.getConversation.mockResolvedValue({
      ...durable,
      message_count: 3,
      messages: [...durable.messages, latest],
    })
    mocks.getQaRequest.mockResolvedValue(latest)
    mocks.askQuestion.mockRejectedValueOnce(new RequestTimeoutError())
    fireEvent.change(screen.getByLabelText('向这篇论文提问'), {
      target: { value: oldRepeat.query },
    })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    expect(await screen.findByText('本轮迟到的回答', {}, { timeout: 3500 })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/正在翻阅原文并核验/)).not.toBeInTheDocument())
    expect(mocks.askQuestion).toHaveBeenCalledTimes(1)
    expect(mocks.createConversation).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it.each(['done', 'needs_review'] as const)(
    'allows original-paper questions for a %s report',
    async (status) => {
      mocks.getReader.mockResolvedValue({ ...reader, status, can_ask: true })
      renderPage()
      expect(await screen.findByTestId('pdf')).toHaveAttribute('data-doc', `att-${ATT}`)
      const paper = screen.getByRole('checkbox', { name: '本论文' })
      expect(paper).toBeChecked()
      expect(paper).toBeDisabled()
      expect(screen.getByRole('checkbox', { name: '联网检索' })).not.toBeChecked()

      fireEvent.change(screen.getByLabelText('向这篇论文提问'), {
        target: { value: '用了什么数据集？' },
      })
      fireEvent.click(screen.getByRole('button', { name: '提问' }))
      await waitFor(() => expect(mocks.askQuestion).toHaveBeenCalled())
      expect(mocks.createConversation).toHaveBeenCalledWith('用了什么数据集？', 'r1')
      expect(mocks.askQuestion).toHaveBeenCalledWith(
        'c1',
        '用了什么数据集？',
        expect.any(AbortSignal),
        { sources: [], projectId: undefined, requestId: expect.any(String) },
        expect.any(Function),
        expect.any(Function),
      )
    },
  )

  it('aborts only the stream connection when leaving during a model response', async () => {
    mocks.listConversations.mockResolvedValue([answered])
    let signal!: AbortSignal
    mocks.askQuestion.mockImplementationOnce((_cid, _query, connectionSignal) => {
      signal = connectionSignal
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), { once: true })
      })
    })
    const page = renderPage()
    await screen.findByText(answered.messages[0].query)
    fireEvent.change(screen.getByLabelText('向这篇论文提问'), { target: { value: '继续解释' } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    await waitFor(() => expect(mocks.askQuestion).toHaveBeenCalledTimes(1))
    expect(signal.aborted).toBe(false)
    await act(async () => {
      page.unmount()
    })
    expect(signal.aborted).toBe(true)
    expect(mocks.getQaRequest).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('dr_pending_qa_c1')).toBeTruthy()
  })

  it('requires a project before the library joins, then sends the chosen sources', async () => {
    renderPage()
    await screen.findByTestId('pdf')
    fireEvent.click(screen.getByRole('checkbox', { name: '资料库' }))
    fireEvent.click(screen.getByRole('checkbox', { name: '联网检索' }))
    fireEvent.change(screen.getByLabelText('向这篇论文提问'), { target: { value: '和综述比呢？' } })
    expect(screen.getByRole('button', { name: '提问' })).toBeDisabled()
    expect(screen.getByText('勾选了资料库，请先选一个项目')).toBeInTheDocument()

    const select = screen.getByLabelText('选择资料库项目')
    await screen.findByRole('option', { name: '光谱重建' })
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByRole('button', { name: '提问' }))
    await waitFor(() =>
      expect(mocks.askQuestion).toHaveBeenCalledWith(
        'c1',
        '和综述比呢？',
        expect.any(AbortSignal),
        { sources: ['library', 'web'], projectId: 'p1', requestId: expect.any(String) },
        expect.any(Function),
        expect.any(Function),
      ),
    )
  })

  it('locates inline paper citations and opens external citations without a source list', async () => {
    mocks.listConversations.mockResolvedValue([answered])
    renderPage()
    const locate = await screen.findByRole('button', { name: '浏览引用 1 的来源记录' })
    expect(screen.queryByRole('list', { name: '本论文引用' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看引用 2：A survey' })).toHaveAttribute(
      'href',
      'https://b.org/survey',
    )
    fireEvent.click(locate)
    expect(screen.getByTestId('pdf')).not.toHaveAttribute(
      'data-quote',
      'PSNR reaches 38.4 dB on CAVE',
    )
    fireEvent.click(screen.getByRole('button', { name: '定位这条记录' }))
    expect(screen.getByTestId('pdf')).toHaveAttribute('data-quote', 'PSNR reaches 38.4 dB on CAVE')
  })

  it('retries the same citation even when the clock has not advanced', async () => {
    mocks.listConversations.mockResolvedValue([answered])
    renderPage()
    const locate = await screen.findByRole('button', { name: '浏览引用 1 的来源记录' })
    const clock = vi.spyOn(Date, 'now').mockReturnValue(12345)
    try {
      fireEvent.click(locate)
      fireEvent.click(screen.getByRole('button', { name: '定位这条记录' }))
      const firstToken = screen.getByTestId('pdf').getAttribute('data-token')
      fireEvent.click(locate)
      fireEvent.click(screen.getByRole('button', { name: '定位这条记录' }))
      expect(screen.getByTestId('pdf').getAttribute('data-token')).not.toBe(firstToken)
    } finally {
      clock.mockRestore()
    }
  })

  it('switches to the report and explains documents without a PDF', async () => {
    renderPage()
    await screen.findByTestId('pdf')
    fireEvent.change(screen.getByLabelText('选择论文'), { target: { value: 'paper-0' } })
    expect(screen.getByText(/没有可直接显示的 PDF/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /在原网站打开/ })).toHaveAttribute(
      'href',
      'https://doi.org/10.1/x',
    )
    fireEvent.click(screen.getByRole('tab', { name: /精读报告/ }))
    expect(await screen.findByRole('heading', { name: '精读报告' })).toBeInTheDocument()
  })
})

describe('documentForEvidence', () => {
  it('maps attachment chunks and paper links to their documents', () => {
    expect(
      documentForEvidence(`https://workspace.invalid/attachments/${ATT}?chunk=1`, documents)?.id,
    ).toBe(`att-${ATT}`)
    expect(documentForEvidence('https://doi.org/10.1/x#p2', documents)?.id).toBe('paper-0')
    expect(documentForEvidence('https://elsewhere.org', documents)).toBeUndefined()
  })
})
