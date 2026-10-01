import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PdfViewer, { PDF_PARSE_TIMEOUT_MS } from './PdfViewer'

const mocks = vi.hoisted(() => ({ fetchReaderPdf: vi.fn(), getDocument: vi.fn() }))
vi.mock('../api/client', () => ({ fetchReaderPdf: mocks.fetchReaderPdf }))
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  Util: { transform: (_viewport: unknown, transform: number[]) => transform },
  getDocument: mocks.getDocument,
}))

const firstPage = {
  getViewport: () => ({ width: 600, height: 800 }),
  render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.fetchReaderPdf.mockResolvedValue(new ArrayBuffer(4))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    {} as CanvasRenderingContext2D,
  )
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

it('places the cited text in the middle of the PDF scroll viewport', async () => {
  const page = {
    ...firstPage,
    getTextContent: async () => ({
      items: [{ str: 'target quote', transform: [1, 0, 0, 10, 10, 210], width: 60 }],
    }),
  }
  mocks.getDocument.mockReturnValue({
    promise: Promise.resolve({ numPages: 1, getPage: async () => page }),
    destroy: vi.fn().mockResolvedValue(undefined),
  })
  const { container, rerender } = render(<PdfViewer runId="r" documentId="d" />)
  await screen.findByText('共 1 页')
  const scroller = container.querySelector('.pdf-scroller') as HTMLDivElement
  const holder = container.querySelector('.pdf-page') as HTMLDivElement
  Object.defineProperty(scroller, 'clientHeight', { value: 600 })
  scroller.scrollTop = 400
  vi.spyOn(scroller, 'getBoundingClientRect').mockReturnValue({ top: 200 } as DOMRect)
  vi.spyOn(holder, 'getBoundingClientRect').mockReturnValue({ top: 600 } as DOMRect)
  scroller.scrollTo = vi.fn()
  rerender(<PdfViewer runId="r" documentId="d" highlight={{ quote: 'target quote', token: 1 }} />)
  await waitFor(() =>
    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 705.75, behavior: 'smooth' }),
  )
})

it('shows the first page without waiting for every page to parse', async () => {
  const pdf = {
    numPages: 20,
    getPage: vi.fn((number) => (number === 1 ? Promise.resolve(firstPage) : new Promise(() => {}))),
  }
  mocks.getDocument.mockReturnValue({
    promise: Promise.resolve(pdf),
    destroy: vi.fn().mockResolvedValue(undefined),
  })
  render(<PdfViewer runId="r1" documentId="doc" />)
  expect(await screen.findByText('共 20 页')).toBeInTheDocument()
  expect(screen.queryByText(/正在解析/)).not.toBeInTheDocument()
})

it('shows download progress and surfaces a stalled parser with a retry', async () => {
  vi.useFakeTimers()
  let downloaded!: (value: ArrayBuffer) => void
  mocks.fetchReaderPdf.mockImplementation((_run, _doc, _signal, progress) => {
    progress(1048576, 2097152)
    return new Promise((resolve) => {
      downloaded = resolve
    })
  })
  const destroy = vi.fn().mockResolvedValue(undefined)
  mocks.getDocument.mockReturnValue({ promise: new Promise(() => {}), destroy })
  render(<PdfViewer runId="r1" documentId="doc" />)
  expect(screen.getByText(/1.0 MB/)).toHaveTextContent('2.0 MB')
  await act(async () => {
    downloaded(new ArrayBuffer(4))
  })
  expect(screen.getByText(/正在解析/)).toBeInTheDocument()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(PDF_PARSE_TIMEOUT_MS)
  })
  expect(screen.getByRole('alert')).toHaveTextContent('PDF 解析超时')
  expect(destroy).toHaveBeenCalled()
  vi.useRealTimers()
  mocks.fetchReaderPdf.mockResolvedValue(new ArrayBuffer(4))
  mocks.getDocument.mockReturnValue({
    promise: Promise.resolve({ numPages: 1, getPage: async () => firstPage }),
    destroy,
  })
  fireEvent.click(screen.getByRole('button', { name: '重新加载' }))
  await waitFor(() => expect(screen.getByText('共 1 页')).toBeInTheDocument())
})
