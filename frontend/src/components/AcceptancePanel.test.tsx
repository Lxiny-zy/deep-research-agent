import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import AcceptancePanel from './AcceptancePanel'
import { AcceptanceError } from '../api/acceptance'

const mocks = vi.hoisted(() => ({
  getAcceptanceContext: vi.fn(),
  getAcceptanceSlice: vi.fn(),
  listAcceptanceRecords: vi.fn(),
  getAcceptanceRecord: vi.fn(),
  createAcceptanceRecord: vi.fn(),
}))
vi.mock('../api/acceptance', async () => ({
  ...(await vi.importActual('../api/acceptance')),
  ...mocks,
}))
vi.mock('../hooks/useConfig', () => ({
  useConfig: () => ({ data: { access: { role: 'researcher' } } }),
}))
const version = 'a'.repeat(64),
  location = 'b'.repeat(64),
  evidence = 'c'.repeat(64)

beforeEach(() => {
  vi.resetAllMocks()
  mocks.getAcceptanceContext.mockResolvedValue({
    document_version: version,
    observed_status: 'done',
    template: 'autoResearch',
    coverage_issues: [],
    prose_review_issues: [],
    requirements: [],
    locations: [
      {
        id: location,
        kind: 'prose',
        preview: 'Selected paragraph.',
        pointer: {},
        verification_status: 'not_checked',
      },
    ],
    evidence: [{ id: evidence, citation: 1, source_ids: [], semantic_status: 'supported' }],
    sources: [],
  })
  mocks.listAcceptanceRecords.mockResolvedValue({ items: [], next_cursor: null })
  mocks.getAcceptanceSlice.mockResolvedValue({
    document_version: version,
    excerpt: 'Selected paragraph.',
    total_characters: 19,
    offset: 0,
    excerpt_redacted: false,
  })
})

async function open() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <AcceptancePanel
        runId="run"
        documentVersion={version}
        includeHsiTables={false}
        onRefreshVersion={() => {}}
      />
    </QueryClientProvider>,
  )
  const details = view.container.querySelector('details')!
  details.open = true
  fireEvent(details, new Event('toggle'))
  await screen.findByRole('combobox', { name: '总体人工结论' })
  return view
}

it('keeps a retry idempotent and does not silently promote pending to pass', async () => {
  mocks.createAcceptanceRecord.mockRejectedValue(new AcceptanceError(503, '暂不可用'))
  await open()
  fireEvent.change(screen.getByRole('textbox', { name: '补充说明' }), {
    target: { value: 'manual note' },
  })
  fireEvent.click(screen.getByRole('button', { name: '保存人工验收记录' }))
  await screen.findByText('暂不可用')
  fireEvent.click(screen.getByRole('button', { name: '保存人工验收记录' }))
  await waitFor(() => expect(mocks.createAcceptanceRecord).toHaveBeenCalledTimes(2))
  const first = mocks.createAcceptanceRecord.mock.calls[0][1]
  const retry = mocks.createAcceptanceRecord.mock.calls[1][1]
  expect(first).toMatchObject({
    document_version: version,
    conclusion: 'pending',
    first_attempt_status: 'unknown',
    issues: [],
    note: 'manual note',
  })
  expect(retry.request_id).toBe(first.request_id)
})

it('a version conflict keeps user input and does not request a new version automatically', async () => {
  mocks.createAcceptanceRecord.mockRejectedValue(
    new AcceptanceError(409, '文档已变化', 'document_version_changed'),
  )
  await open()
  fireEvent.change(screen.getByRole('textbox', { name: '补充说明' }), {
    target: { value: 'preserve this draft' },
  })
  fireEvent.click(screen.getByRole('button', { name: '保存人工验收记录' }))
  await screen.findByRole('button', { name: '加载最新报告版本' })
  expect(screen.getByRole('textbox', { name: '补充说明' })).toHaveValue('preserve this draft')
  expect(screen.getByRole('button', { name: '保存人工验收记录' })).toBeDisabled()
  expect(mocks.getAcceptanceContext.mock.calls.every((call) => call[1] === version)).toBe(true)
})

it('redacted previews cannot be attached as original evidence', async () => {
  mocks.getAcceptanceSlice.mockResolvedValue({
    document_version: version,
    excerpt: '[REDACTED]',
    total_characters: 40,
    offset: 0,
    excerpt_redacted: true,
  })
  const view = await open()
  const editor = view.container.querySelector<HTMLDetailsElement>('.acceptance-issue-editor')!
  editor.open = true
  fireEvent.change(screen.getByRole('combobox', { name: '正文或图表位置' }), {
    target: { value: location },
  })
  await screen.findByText('片段含遮盖内容，仅保存位置标识。')
  expect(
    screen.queryByRole('checkbox', { name: '将必要的正文片段附入记录' }),
  ).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: '具体观察' }), {
    target: { value: 'Check selected position' },
  })
  fireEvent.click(screen.getByRole('button', { name: '加入问题清单' }))
  mocks.createAcceptanceRecord.mockRejectedValue(new AcceptanceError(503, '暂不可用'))
  fireEvent.click(screen.getByRole('button', { name: '保存人工验收记录' }))
  await waitFor(() => expect(mocks.createAcceptanceRecord).toHaveBeenCalled())
  expect(mocks.createAcceptanceRecord.mock.calls[0][1].issues[0]).toMatchObject({
    location_id: location,
    excerpt: '',
    evidence_selections: [],
  })
})
