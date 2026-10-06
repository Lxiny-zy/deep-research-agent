import { webcrypto } from 'node:crypto'
import { createRenderOperation, downloadRenderResult, type RenderOperationReceipt } from './client'
const receipt: RenderOperationReceipt = {
  id: 'op',
  operation_id: 'op',
  run_id: 'run',
  kind: 'export',
  request_id: 'request',
  status: 'done',
  status_url: '',
  content_version: 'frozen',
}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
})
afterEach(() => vi.restoreAllMocks())
it('maps academic PDF to the backend receipt format while preserving version and request id', async () => {
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(
      new Response(JSON.stringify(receipt), { headers: { 'Content-Type': 'application/json' } }),
    )
  await createRenderOperation('run', {
    kind: 'export',
    format: 'paper_pdf',
    version: 'frozen',
    request_id: 'same-id',
  })
  expect(fetch).toHaveBeenCalledWith(
    '/api/runs/run/render-operations',
    expect.objectContaining({
      body: JSON.stringify({
        kind: 'export',
        format: 'paper-pdf',
        version: 'frozen',
        request_id: 'same-id',
      }),
    }),
  )
})
it('checks submitted version and downloaded bytes before returning an export', async () => {
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode('verified'))),
    (n) => n.toString(16).padStart(2, '0'),
  ).join('')
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response('verified', {
      headers: {
        'X-Content-Version': 'frozen',
        'X-Content-SHA256': hash,
        'Content-Disposition': 'attachment; filename="result.md"',
      },
    }),
  )
  expect((await downloadRenderResult('run', receipt)).filename).toBe('result.md')
})
it.each([
  ['other-version', 'unused', '内容版本不一致'],
  ['frozen', 'corrupted', '完整性核验失败'],
])('rejects mismatched result %s', async (version, hash, message) => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response('bytes', { headers: { 'X-Content-Version': version, 'X-Content-SHA256': hash } }),
  )
  await expect(downloadRenderResult('run', receipt)).rejects.toThrow(message)
})
