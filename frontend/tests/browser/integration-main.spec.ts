import { test, expect, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'

const keys = {
  admin: 'synthetic-browser-admin-key',
  alice: 'synthetic-browser-alice-key',
  bob: 'synthetic-browser-bob-key',
  reader: 'synthetic-browser-reader-key',
}
const headers = (role: keyof typeof keys) => ({ Authorization: `Bearer ${keys[role]}` })
const browserFaults = new WeakMap<Page, string[]>()

async function login(page: Page, role: keyof typeof keys, route = '/history') {
  await page.addInitScript(() => {
    localStorage.setItem('dr_welcome_tour_seen', '1')
    sessionStorage.setItem('sr_intro_seen', '1')
    localStorage.setItem('sr_sidebar_collapsed', '1')
  })
  await page.goto(route)
  await page.locator('.intro-enter').click()
  await page.getByLabel('访问密钥', { exact: true }).fill(keys[role])
  await page.getByRole('button', { name: '验证并进入' }).click()
  await expect(page.locator('.app-shell')).toBeVisible()
}

test.beforeEach(async ({ page, baseURL }) => {
  const faults: string[] = []
  browserFaults.set(page, faults)
  page.on('pageerror', (error) => faults.push(error.message))
  // Do not mock any API response in the integration layer.
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== new URL(baseURL!).origin) {
      faults.push(`Unexpected external request: ${url.origin}${url.pathname}`)
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
})
test.afterEach(async ({ page }) => {
  expect(browserFaults.get(page)).toEqual([])
})

test('real auth and SQLite enforce researcher ownership and reader restrictions', async ({
  page,
  request,
}) => {
  expect((await request.get('/api/config')).status()).toBe(401)
  expect(
    (
      await request.get('/api/config', {
        headers: { Authorization: 'Bearer invalid-test-identity' },
      })
    ).status(),
  ).toBe(401)
  const metadata = await (
    await request.get('/__browser_fixture__/state', { headers: headers('admin') })
  ).json()
  await login(page, 'alice')
  await expect(
    page
      .locator('.main-content')
      .getByText('Browser integration original report', { exact: false })
      .first(),
  ).toBeVisible()
  await expect(page.getByText('Bob private record', { exact: true })).toHaveCount(0)
  expect(
    (await request.get(`/api/runs/${metadata.run_id}`, { headers: headers('bob') })).status(),
  ).toBe(404)
  expect(
    (
      await request.get(`/api/runs/${metadata.run_id}/reader/${metadata.document_id}/pdf`, {
        headers: headers('bob'),
      })
    ).status(),
  ).toBe(404)
  await page.getByRole('button', { name: /当前身份/ }).click()
  await page.getByLabel('访问密钥', { exact: true }).fill(keys.bob)
  await page.getByRole('button', { name: '验证并进入' }).click()
  await expect(
    page.locator('.main-content').getByText('Bob private record', { exact: false }).first(),
  ).toBeVisible()
  await expect(page.getByText('Browser integration original report', { exact: true })).toHaveCount(
    0,
  )
  await page.reload()
  await expect(
    page.locator('.main-content').getByText('Bob private record', { exact: false }).first(),
  ).toBeVisible()
  // This goes through real RBAC, not the fixture's separate no-model guard.
  expect(
    (await request.put('/api/config', { headers: headers('reader'), data: {} })).status(),
  ).toBe(403)
  await page.getByRole('button', { name: /当前身份/ }).click()
  await page.getByLabel('访问密钥', { exact: true }).fill(keys.reader)
  await page.getByRole('button', { name: '验证并进入' }).click()
  await page.goto('/settings')
  await expect(page.getByText('当前身份没有此操作权限')).toBeVisible()
})

test('real browser uploads parse a PDF attachment and CSV without starting a model task', async ({
  page,
  request,
}) => {
  const metadata = await (
    await request.get('/__browser_fixture__/state', { headers: headers('admin') })
  ).json()
  const original = await request.get(
    `/api/runs/${metadata.run_id}/reader/${metadata.document_id}/pdf`,
    { headers: headers('alice') },
  )
  expect(original.status()).toBe(200)
  await login(page, 'alice', '/')
  await page.locator('.task-card[data-template="paperRead"]').click()
  const uploaded = page.waitForResponse(
    (response) =>
      response.url().includes('/api/attachments/file') && response.request().method() === 'POST',
  )
  await page.getByLabel('上传附件').setInputFiles({
    name: 'browser-upload.pdf',
    mimeType: 'application/pdf',
    buffer: await original.body(),
  })
  await expect(page.locator('.attach-item.is-ready')).toContainText('browser-upload.pdf')
  const parsed = await (await uploaded).json()
  expect(parsed.summary.chunk_count).toBeGreaterThan(0)
  expect(
    parsed.attachment.chunks.map((chunk: { content: string }) => chunk.content).join('\n'),
  ).toContain(metadata.quote)
  await page.locator('.task-card[data-template="dataAnalysis"]').click()
  await page.locator('#dataset-file').setInputFiles({
    name: 'browser-metrics.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('method,psnr\nA,30\nB,32\n'),
  })
  await expect(page.locator('.home-dataset-file')).toContainText('2 行 × 2 列')
  await expect(page.locator('.attach-item.is-ready')).toContainText('browser-upload.pdf')
})

for (const width of [1440, 390]) {
  test(`real PDF bytes render and selected quote locates page 2 at ${width}`, async ({
    page,
    request,
  }, testInfo) => {
    const metadata = await (
      await request.get('/__browser_fixture__/state', { headers: headers('admin') })
    ).json()
    await page.setViewportSize({ width, height: 900 })
    await login(page, 'alice', `/runs/${metadata.run_id}/read`)
    await expect(page.locator('.pdf-toolbar-count')).toHaveText('共 2 页')
    await expect(page.locator('.pdf-highlight')).toHaveCount(0)
    await page.getByRole('button', { name: '浏览引用 1 的来源记录' }).click()
    const drawer = page.getByRole('dialog', { name: '浏览来源记录' })
    await expect(drawer).toContainText(metadata.quote)
    await expect(page.locator('.pdf-highlight')).toHaveCount(0)
    await drawer.getByRole('button', { name: '定位这条记录' }).click()
    await expect(page.locator('.pdf-toolbar-note')).toContainText('已定位完整引文 · 第 2 页')
    await expect(page.locator('.pdf-page[aria-label="第 2 页"] .pdf-highlight')).toBeVisible()
    expect(await page.locator('.pdf-page[aria-label="第 1 页"] .pdf-highlight').count()).toBe(0)
    await expect(page.locator('.qa-avatar .brand-ring')).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('actual-pdf-locator.png') })
    await page.reload()
    await expect(page.locator('.pdf-toolbar-count')).toHaveText('共 2 页')
    await expect(page.locator('.pdf-highlight')).toHaveCount(0)
    await expect(page.getByRole('article', { name: '第 1 轮问答' })).toContainText('38.4 dB')
  })
}

test('real render receipt retains old bytes while preview moves to a new version', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000)
  const metadata = await (
    await request.get('/__browser_fixture__/state', { headers: headers('admin') })
  ).json()
  const root = `/api/runs/${metadata.run_id}`
  const previousMarker = metadata.revision
    ? `Revision ${metadata.revision}`
    : 'Original frozen body'
  await login(page, 'alice', root.replace('/api', ''))
  await expect(
    page.locator('.run-report-card').getByText(previousMarker, { exact: false }).first(),
  ).toBeVisible()
  const oldDocument = await (
    await request.get(`${root}/document`, { headers: headers('alice') })
  ).json()
  const receiptResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/render-operations') &&
      response.request().postDataJSON()?.kind === 'export',
  )
  const downloadEvent = page.waitForEvent('download')
  await page.locator('.run-export-menu > summary').click()
  await page.getByRole('button', { name: '下载 .md', exact: true }).click()
  const receipt = await (await receiptResponse).json()
  const download = await downloadEvent
  const localPath = await download.path()
  expect(localPath).toBeTruthy()
  const firstBytes = await readFile(localPath!)
  expect(firstBytes.toString()).toContain(previousMarker)
  const changed = await request.post('/__browser_fixture__/next-version', {
    headers: headers('admin'),
  })
  expect(changed.ok()).toBe(true)
  const stale = await request.get(`${root}/document?version=${oldDocument.content_version}`, {
    headers: headers('alice'),
  })
  expect(stale.status()).toBe(409)
  const current = await (
    await request.get(`${root}/document`, { headers: headers('alice') })
  ).json()
  expect(current.content_version).not.toBe(oldDocument.content_version)
  await page.reload()
  await expect(
    page.getByText('New frozen body after a second editor saves.', { exact: false }).first(),
  ).toBeVisible()
  const prior = await request.get(
    `${root}/render-operations/${receipt.operation_id || receipt.id}/result`,
    { headers: headers('alice') },
  )
  expect(prior.status()).toBe(200)
  expect(await prior.body()).toEqual(firstBytes)
  expect(prior.headers()['x-content-version']).toBe(oldDocument.content_version)
  expect(prior.headers()['x-content-sha256']).toBe(
    createHash('sha256').update(firstBytes).digest('hex'),
  )
})
