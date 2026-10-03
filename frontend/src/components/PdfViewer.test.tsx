import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PdfViewer, { PDF_PARSE_TIMEOUT_MS } from './PdfViewer'

const mocks = vi.hoisted(() => ({
  fetchReaderPdf: vi.fn(),
  getDocument: vi.fn(),
  quoteRects: vi.fn(),
}))
vi.mock('../api/client', () => ({ fetchReaderPdf: mocks.fetchReaderPdf }))
vi.mock('../lib/pdfHighlight', () => ({ quoteRects: mocks.quoteRects }))
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
  mocks.quoteRects.mockResolvedValue([{ x: 10, y: 200, width: 60, height: 11.5 }])
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
  expect(mocks.quoteRects.mock.calls[0][2]).toEqual([{ page: 0, item: 0, start: 0, end: 12 }])
  expect(screen.getByText('已定位完整引文 · 第 1 页')).toBeInTheDocument()
})

it('clears old highlights when a different quote cannot be located', async () => {
  const page = { ...firstPage, getTextContent: async () => ({ items: [{ str: 'target quote' }] }) }
  mocks.getDocument.mockReturnValue({
    promise: Promise.resolve({ numPages: 1, getPage: async () => page }),
    destroy: vi.fn().mockResolvedValue(undefined),
  })
  const { container, rerender } = render(
    <PdfViewer runId="r" documentId="d" highlight={{ quote: 'target quote', token: 1 }} />,
  )
  await screen.findByText('已定位完整引文 · 第 1 页')
  expect(container.querySelectorAll('.pdf-highlight')).toHaveLength(1)
  rerender(
    <PdfViewer
      runId="r"
      documentId="d"
      highlight={{ quote: 'target quote with an absent ending', token: 2 }}
    />,
  )
  await screen.findByText('未找到唯一的完整引文，无法准确定位')
  expect(container.querySelectorAll('.pdf-highlight')).toHaveLength(0)
})

it('highlights both pages when a complete quotation crosses a page boundary', async () => {
  const pages = ['A quotation starts', 'and ends here.']
  mocks.getDocument.mockReturnValue({
    promise: Promise.resolve({
      numPages: 2,
      getPage: async (number: number) => ({
        ...firstPage,
        getTextContent: async () => ({ items: [{ str: pages[number - 1] }] }),
      }),
    }),
    destroy: vi.fn().mockResolvedValue(undefined),
  })
  const { container } = render(
    <PdfViewer
      runId="r"
      documentId="d"
      highlight={{ quote: 'A quotation starts and ends here.', token: 1 }}
    />,
  )
  await screen.findByText('已定位完整引文 · 第 1、2 页')
  expect(
    [...container.querySelectorAll('.pdf-page')].map(
      (page) => page.querySelectorAll('.pdf-highlight').length,
    ),
  ).toEqual([1, 1])
})

it('reports text extraction failures without leaving a stale successful location', async () => {
  mocks.getDocument.mockReturnValue({
    promise: Promise.resolve({
      numPages: 1,
      getPage: async () => ({
        ...firstPage,
        getTextContent: async () => {
          throw new Error('broken text layer')
        },
      }),
    }),
    destroy: vi.fn().mockResolvedValue(undefined),
  })
  render(<PdfViewer runId="r" documentId="d" highlight={{ quote: 'target quote', token: 1 }} />)
  await screen.findByText('引文定位失败，请重新点击定位')
})

it('does not let a slow earlier location replace the newly selected quote', async () => {
  let finishEarlier!: (rects: { x: number; y: number; width: number; height: number }[]) => void
  mocks.quoteRects
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishEarlier = resolve
        }),
    )
    .mockResolvedValueOnce([{ x: 70, y: 220, width: 24, height: 10 }])
  const page = {
    ...firstPage,
    getTextContent: async () => ({ items: [{ str: 'First quote. Second quote.' }] }),
  }
  mocks.getDocument.mockReturnValue({
    promise: Promise.resolve({ numPages: 1, getPage: async () => page }),
    destroy: vi.fn().mockResolvedValue(undefined),
  })
  const { container, rerender } = render(
    <PdfViewer runId="r" documentId="d" highlight={{ quote: 'First quote.', token: 1 }} />,
  )
  await waitFor(() => expect(mocks.quoteRects).toHaveBeenCalledTimes(1))
  rerender(<PdfViewer runId="r" documentId="d" highlight={{ quote: 'Second quote.', token: 2 }} />)
  await screen.findByText('已定位完整引文 · 第 1 页')
  await act(async () => finishEarlier([{ x: 10, y: 100, width: 500, height: 20 }]))
  expect(container.querySelector('.pdf-highlight')).toHaveStyle({ left: '70px', width: '24px' })
})

it('times out stalled quote extraction and allows a new location attempt', async () => {
  const getTextContent = vi
    .fn()
    .mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValueOnce({ items: [{ str: 'target quote' }] })
  mocks.getDocument.mockReturnValue({
    promise: Promise.resolve({
      numPages: 1,
      getPage: async () => ({ ...firstPage, getTextContent }),
    }),
    destroy: vi.fn().mockResolvedValue(undefined),
  })
  const { rerender } = render(<PdfViewer runId="r" documentId="d" />)
  await screen.findByText('共 1 页')
  vi.useFakeTimers()
  rerender(<PdfViewer runId="r" documentId="d" highlight={{ quote: 'target quote', token: 1 }} />)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(PDF_PARSE_TIMEOUT_MS)
  })
  expect(screen.getByText('引文定位超时，请重新点击定位')).toBeInTheDocument()
  vi.useRealTimers()
  rerender(<PdfViewer runId="r" documentId="d" highlight={{ quote: 'target quote', token: 2 }} />)
  await screen.findByText('已定位完整引文 · 第 1 页')
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
