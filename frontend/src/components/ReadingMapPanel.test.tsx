import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ReadingMapPanel from './ReadingMapPanel'

const mocks = vi.hoisted(() => ({ getReadingMap: vi.fn(), getReadingUnit: vi.fn() }))
vi.mock('../api/readingMap', async () => ({
  ...(await vi.importActual('../api/readingMap')),
  ...mocks,
}))
const version = 'a'.repeat(64)
const anchor = {
  id: 'anchor',
  evidence_id: 'e',
  source_url: 'https://workspace.invalid/attachments/pdf',
  document_id: 'att-pdf',
  pdf_available: true,
  quote: 'Complete source quote',
  quote_truncated: false,
  quote_redacted: false,
  context_before: '',
  context_after: '',
  page_hint: 2,
  locator: 'page 2',
}
const unit = (id: string) => ({
  id,
  unit_id: id,
  section: id,
  preview: id,
  verification_status: 'supported',
  evidence_count: id === 'Beta' ? 1 : 0,
  kind: 'prose',
})
const detail = (id: string) => ({
  document_version: version,
  unit: unit(id),
  text: `${id} body`,
  text_offset: 0,
  text_total_chars: 10,
  text_truncated: false,
  text_redacted: false,
  review_bound: true,
  anchors: id === 'Beta' ? [anchor] : [],
  anchor_offset: 0,
  anchor_total: id === 'Beta' ? 1 : 0,
  anchor_limit: 8,
  evidence_status: [],
  evidence_status_truncated: false,
  peer_item: null,
  measurement_context: [],
})

beforeEach(() => {
  vi.resetAllMocks()
  mocks.getReadingMap.mockResolvedValue({
    document_version: version,
    source_version: version,
    template: 'autoResearch',
    review_bound: true,
    review_issues: [],
    total: 2,
    offset: 0,
    limit: 20,
    units: [unit('Alpha'), unit('Beta')],
    focus_items: [],
    focus_total: 0,
    focus_truncated: false,
    peer_review: null,
  })
  mocks.getReadingUnit.mockImplementation((_run, _version, _hsi, id: string) =>
    Promise.resolve(detail(id)),
  )
})

it.each(['selection', 'hidden', 'version'] as const)(
  'does not navigate when a %s change overtakes location revalidation',
  async (mode) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 10_000 } },
    })
    const locate = vi.fn()
    const view = (active = true, documentVersion = version) => (
      <QueryClientProvider client={client}>
        <ReadingMapPanel
          runId="run"
          documentVersion={documentVersion}
          embedded
          active={active}
          onLocate={locate}
          onRefreshVersion={() => {}}
        />
      </QueryClientProvider>
    )
    const rendered = render(view())
    fireEvent.click(await screen.findByRole('button', { name: /2. Beta/ }))
    await screen.findByRole('button', { name: '定位这条原文' })
    let release!: (value: ReturnType<typeof detail>) => void
    mocks.getReadingUnit.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    fireEvent.click(screen.getByRole('button', { name: '定位这条原文' }))
    await waitFor(() => expect(release).toBeDefined())
    if (mode === 'selection') fireEvent.click(screen.getByRole('button', { name: /1. Alpha/ }))
    else rendered.rerender(view(mode !== 'hidden', mode === 'version' ? 'b'.repeat(64) : version))
    await act(async () => {
      release(detail('Beta'))
      await Promise.resolve()
    })
    expect(locate).not.toHaveBeenCalled()
  },
)
