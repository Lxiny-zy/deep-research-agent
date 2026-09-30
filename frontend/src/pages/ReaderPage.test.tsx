import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReaderPage from './ReaderPage'
import { documentForEvidence } from '../lib/readerDocuments'
import type { QaConversation, ReaderDocument, RunReader } from '../types'

const mocks = vi.hoisted(() => ({
  getReader: vi.fn(),
  getRun: vi.fn(),
  listProjects: vi.fn(),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  createConversation: vi.fn(),
  askQuestion: vi.fn(),
}))
vi.mock('../api/client', () => mocks)
vi.mock('../components/PdfViewer', () => ({
  default: (props: { documentId: string; highlight?: { quote: string } | null }) => (
    <div data-testid="pdf" data-doc={props.documentId} data-quote={props.highlight?.quote ?? ''} />
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
})

describe('ReaderPage', () => {
  it('shows the original PDF and asks with only the paper by default', async () => {
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
    expect(mocks.askQuestion).toHaveBeenCalledWith('c1', '用了什么数据集？', undefined, {
      sources: [],
      projectId: undefined,
    })
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
      expect(mocks.askQuestion).toHaveBeenCalledWith('c1', '和综述比呢？', undefined, {
        sources: ['library', 'web'],
        projectId: 'p1',
      }),
    )
  })

  it('groups citations by origin and locates paper quotes in the PDF', async () => {
    mocks.listConversations.mockResolvedValue([answered])
    renderPage()
    const locate = await screen.findByRole('button', { name: /mst\.pdf 第 4 页/ })
    expect(screen.getByRole('list', { name: '本论文引用' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'A survey' })).toHaveAttribute(
      'href',
      'https://b.org/survey',
    )
    fireEvent.click(locate)
    expect(screen.getByTestId('pdf')).toHaveAttribute('data-quote', 'PSNR reaches 38.4 dB on CAVE')
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
