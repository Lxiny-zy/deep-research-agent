import { webcrypto } from 'node:crypto'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  ApiError,
  createRenderOperation,
  downloadRenderResult,
  getRenderOperation,
  setApiKey,
  type RenderOperationReceipt,
} from '../api/client'
import { downloadBlob, downloadText } from '../lib/download'
import ReportActions from './ReportActions'

vi.mock('../api/client', async () => ({
  ...(await vi.importActual('../api/client')),
  createRenderOperation: vi.fn(),
  getRenderOperation: vi.fn(),
  downloadRenderResult: vi.fn(),
}))
vi.mock('../lib/download', () => ({
  downloadBlob: vi.fn(),
  downloadText: vi.fn(),
  slugify: (s: string) => s,
}))
const create = vi.mocked(createRenderOperation)
const result = vi.mocked(downloadRenderResult)
const status = vi.mocked(getRenderOperation)
const receipt = (requestId: string, state = 'done'): RenderOperationReceipt => ({
  id: 'op-1',
  operation_id: 'op-1',
  run_id: 'run-1',
  kind: 'export',
  request_id: requestId,
  status: state,
  status_url: '/api/runs/run-1/render-operations/op-1',
  content_version: 'version-a',
})
function renderActions(over: Partial<Parameters<typeof ReportActions>[0]> = {}) {
  return render(
    <ReportActions
      markdown="# 报告"
      query="q"
      runId="run-1"
      documentReady
      contentVersion="version-a"
      tableOptions={[{ id: 'hsi_reconstruction', label: '重建算法' }]}
      capabilities={{ pdf: true, xlsx: true }}
      {...over}
    />,
  )
}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
  create.mockImplementation(async (_, request) => receipt(request.request_id))
  result.mockResolvedValue({ blob: new Blob(['complete']), filename: 'report.md' })
})

it('requires a known document version and tables for table exports', () => {
  const view = renderActions({ contentVersion: undefined })
  expect(screen.getByRole('button', { name: /下载 PDF/ })).toBeDisabled()
  view.rerender(
    <ReportActions
      markdown="x"
      query="q"
      runId="run-1"
      contentVersion="v"
      documentReady
      capabilities={{ pdf: true }}
    />,
  )
  expect(screen.getByRole('button', { name: /下载 CSV/ })).toBeDisabled()
  expect(screen.getByRole('button', { name: /下载 PDF/ })).toBeEnabled()
})

it.each(['CSV', 'PDF', '.md'])(
  'pins %s to the preview version and downloads the submitted receipt',
  async (format) => {
    renderActions()
    await userEvent.click(screen.getByRole('button', { name: `下载 ${format}` }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalled())
    expect(create).toHaveBeenCalledWith(
      'run-1',
      expect.objectContaining({
        kind: 'export',
        version: 'version-a',
        format: format === '.md' ? 'md' : format.toLowerCase(),
        request_id: expect.any(String),
        include_hsi_tables: false,
        table_id: format === 'CSV' ? 'hsi_reconstruction' : undefined,
      }),
      expect.any(AbortSignal),
    )
    expect(result).toHaveBeenCalledWith(
      'run-1',
      expect.objectContaining({ content_version: 'version-a' }),
    )
  },
)

it('a version conflict stays visible and never silently downloads another file', async () => {
  create.mockRejectedValue(new ApiError(409, 'document_version_changed'))
  renderActions()
  await userEvent.click(screen.getByRole('button', { name: '下载 .md' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('document_version_changed')
  expect(downloadBlob).not.toHaveBeenCalled()
  expect(downloadText).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '下载离线正文副本' }))
  expect(downloadText).toHaveBeenCalledWith(
    'q-run-1-offline.md',
    expect.stringContaining('"content_version":"version-a"'),
  )
})

it('offline files carry version, missing scope and verification state inside the file', async () => {
  renderActions({ runId: undefined, documentReady: false, supportFailed: true })
  await userEvent.click(screen.getByRole('button', { name: '下载 .md' }))
  const [name, text] = vi.mocked(downloadText).mock.calls[0]
  expect(name).toBe('q-offline.md')
  expect(text).toContain('待核验草稿')
  expect(text).toContain('evidence_appendix')
  expect(text).toContain('file_hash_verification')
  expect(text).toContain('# 报告')
  expect(create).not.toHaveBeenCalled()
})

it('reconnects after a lost response with the original request id after remount', async () => {
  create.mockRejectedValueOnce(new Error('offline'))
  const view = renderActions()
  await userEvent.click(screen.getByRole('button', { name: '下载 .md' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('offline')
  const first = create.mock.calls[0][1].request_id
  view.unmount()
  renderActions()
  await waitFor(() => expect(downloadBlob).toHaveBeenCalled())
  expect(create.mock.calls[1][1].request_id).toBe(first)
})

it('reconnects a saved operation by its receipt without another POST', async () => {
  create.mockImplementationOnce(async (_, request) => receipt(request.request_id, 'running'))
  const view = renderActions()
  await userEvent.click(screen.getByRole('button', { name: '下载 .md' }))
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
  await waitFor(() =>
    expect(Object.values(localStorage).some((value) => value.includes('op-1'))).toBe(true),
  )
  const first = create.mock.calls[0][1].request_id
  status.mockResolvedValue(receipt(first))
  view.unmount()
  renderActions()
  await waitFor(() => expect(downloadBlob).toHaveBeenCalled())
  expect(create).toHaveBeenCalledTimes(1)
  expect(status).toHaveBeenCalledWith('run-1', 'op-1', expect.any(AbortSignal), first)
})

it('does not restore another authenticated identity’s operation for the same run', async () => {
  setApiKey('identity-one')
  create.mockRejectedValueOnce(new Error('lost response'))
  const view = renderActions()
  await userEvent.click(screen.getByRole('button', { name: '下载 .md' }))
  await screen.findByRole('alert')
  view.unmount()
  setApiKey('identity-two')
  renderActions()
  await userEvent.click(screen.getByRole('button', { name: '下载 .md' }))
  await waitFor(() => expect(downloadBlob).toHaveBeenCalled())
  expect(create).toHaveBeenCalledTimes(2)
  expect(create.mock.calls[0][1].request_id).not.toBe(create.mock.calls[1][1].request_id)
  expect(status).not.toHaveBeenCalled()
  expect(Object.keys(localStorage).join('')).not.toContain('identity-one')
})

it('keeps a server failure terminal on refresh and starts a new operation only after an explicit action', async () => {
  create.mockImplementationOnce(async (_, request) => receipt(request.request_id, 'error'))
  const view = renderActions()
  await userEvent.click(screen.getByRole('button', { name: '下载 .md' }))
  await screen.findByRole('alert')
  const failedId = create.mock.calls[0][1].request_id
  status.mockResolvedValue(receipt(failedId, 'error'))
  view.unmount()
  renderActions()
  await screen.findByRole('alert')
  expect(create).toHaveBeenCalledTimes(1)
  await userEvent.click(screen.getByRole('button', { name: '下载 .md' }))
  await waitFor(() => expect(downloadBlob).toHaveBeenCalled())
  expect(create).toHaveBeenCalledTimes(2)
  expect(create.mock.calls[1][1].request_id).not.toBe(failedId)
})
