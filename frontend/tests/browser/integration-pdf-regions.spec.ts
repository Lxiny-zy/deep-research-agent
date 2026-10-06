import { test, expect, type Page, type APIRequestContext } from '@playwright/test'

const headers = { Authorization: 'Bearer synthetic-browser-alice-key' }
async function setup(page: Page, request: APIRequestContext) {
  const meta = await (await request.get('/__browser_fixture__/state', { headers })).json()
  const raw = await (
    await request.get(`/api/runs/${meta.run_id}/reader/${meta.document_id}/pdf`, { headers })
  ).body()
  await page.addInitScript(() => {
    localStorage.setItem('dr_welcome_tour_seen', '1')
    sessionStorage.setItem('sr_intro_seen', '1')
  })
  await page.goto('/')
  await page.locator('.intro-enter').click()
  await page.getByLabel('访问密钥', { exact: true }).fill('synthetic-browser-alice-key')
  await page.getByRole('button', { name: '验证并进入' }).click()
  await page.locator('.task-card[data-template="slides"]').click()
  await page.locator('#query').fill('使用选择的原图解释实验方法。')
  await page
    .getByLabel('上传附件')
    .setInputFiles({ name: 'region-source.pdf', mimeType: 'application/pdf', buffer: raw })
  await expect(page.locator('.attach-item.is-ready')).toContainText('region-source.pdf')
  await page.getByRole('button', { name: '选择图像区域', exact: true }).click()
  await expect(page.getByRole('button', { name: '开始框选' })).toBeEnabled()
  return raw
}
async function rectangle(page: Page, figure: string) {
  await page.getByRole('textbox', { name: '原文图号' }).fill(figure)
  for (const [label, value] of [
    ['左边界（%）', '10'],
    ['上边界（%）', '20'],
    ['右边界（%）', '50'],
    ['下边界（%）', '60'],
  ])
    await page.getByRole('spinbutton', { name: label }).fill(value)
  await page.getByRole('button', { name: '添加这个区域' }).click()
}

test('PDF regions remain attached to the original file and change submission identity', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000)
  const raw = await setup(page, request)
  await page.getByLabel('预览页码').selectOption('2')
  await expect(page.getByRole('button', { name: '添加这个区域' })).toBeEnabled()
  await rectangle(page, 'Fig. 1')
  await page.getByLabel('预览页码').selectOption('1')
  await expect(page.getByRole('button', { name: '添加这个区域' })).toBeEnabled()
  await rectangle(page, 'Figure 1')
  await expect(page.getByRole('alert')).toContainText('这个图号已有区域')
  for (const label of ['Fig. 2', '图3', 'Figure 4a']) await rectangle(page, label)
  await expect(page.getByRole('button', { name: '添加这个区域' })).toBeDisabled()
  await page.screenshot({ path: testInfo.outputPath('selected-regions.png') })
  await page.getByRole('button', { name: '使用这 4 个区域' }).click()
  const firstRequest = page.waitForRequest(
    (req) => req.url().endsWith('/api/runs') && req.method() === 'POST',
  )
  await page.locator('.home-actionbar .btn-lg').click()
  const first = await firstRequest
  expect(first.postDataJSON().attachments[0].image_regions).toHaveLength(4)
  expect(first.postDataJSON().attachments[0].image_regions[0]).toEqual({
    page: 2,
    figure_label: 'Fig. 1',
    bounds: [0.1, 0.2, 0.5, 0.6],
  })
  expect(first.postDataJSON().attachments[0]).not.toHaveProperty('file')
  await expect(page.locator('.home-actionbar-error')).toBeVisible()

  await page
    .getByLabel('上传附件')
    .setInputFiles({ name: 'other-source.pdf', mimeType: 'application/pdf', buffer: raw })
  await expect(page.locator('.attach-item.is-ready')).toHaveCount(2)
  await page
    .locator('.attach-item')
    .filter({ hasText: 'other-source.pdf' })
    .getByRole('button', { name: '选择图像区域' })
    .click()
  await expect(page.getByText('其他附件已占满本任务的 4 个区域。')).toBeVisible()
  await expect(page.getByRole('button', { name: '添加这个区域' })).toBeDisabled()
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  const unchangedFilesRequest = page.waitForRequest(
    (req) => req.url().endsWith('/api/runs') && req.method() === 'POST',
  )
  await page.locator('.home-actionbar .btn-lg').click()
  const beforeRegionChange = await unchangedFilesRequest
  await expect(page.locator('.home-actionbar-error')).toBeVisible()
  await page
    .locator('.attach-item')
    .filter({ hasText: 'region-source.pdf' })
    .getByRole('button', { name: /选择图像区域/ })
    .click()
  await page.getByRole('button', { name: '移除区域 4' }).click()
  await page.getByRole('button', { name: '使用这 3 个区域' }).click()
  const changedRequest = page.waitForRequest(
    (req) => req.url().endsWith('/api/runs') && req.method() === 'POST',
  )
  await page.locator('.home-actionbar .btn-lg').click()
  const changed = await changedRequest
  expect(changed.postDataJSON().attachments[0].image_regions).toHaveLength(3)
  expect(changed.headers()['idempotency-key']).not.toBe(
    beforeRegionChange.headers()['idempotency-key'],
  )
  await expect(page.locator('.home-actionbar-error')).toBeVisible()
  await page.getByRole('button', { name: '移除 region-source.pdf', exact: true }).click()
  await page.getByRole('button', { name: '选择图像区域', exact: true }).click()
  await expect(page.getByRole('heading', { name: '已选区域（0/4）' })).toBeVisible()
})

test('mobile PDF pointer selection uses normalized visible-page bounds', async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await setup(page, request)
  await page.getByRole('button', { name: '开始框选' }).click()
  const surface = page.locator('.pdf-region-surface')
  await surface.scrollIntoViewIfNeeded()
  const rect = await surface.boundingBox()
  const touch = await page.context().newCDPSession(page)
  await touch.send('Emulation.setTouchEmulationEnabled', { enabled: true })
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: rect!.x + rect!.width * 0.15, y: rect!.y + rect!.height * 0.2 }],
  })
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [{ x: rect!.x + rect!.width * 0.65, y: rect!.y + rect!.height * 0.6 }],
  })
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await touch.detach()
  expect(
    Number(await page.getByRole('spinbutton', { name: '左边界（%）' }).inputValue()),
  ).toBeCloseTo(15, 0)
  expect(
    Number(await page.getByRole('spinbutton', { name: '下边界（%）' }).inputValue()),
  ).toBeCloseTo(60, 0)
  await page.getByRole('textbox', { name: '原文图号' }).fill('Fig. 1')
  await page.getByRole('button', { name: '添加这个区域' }).click()
  await page.getByRole('button', { name: '使用这 1 个区域' }).scrollIntoViewIfNeeded()
  expect(
    await page.locator('.pdf-region-dialog').evaluate((el) => el.scrollWidth <= el.clientWidth),
  ).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('mobile-region-picker.png') })
  await page.getByRole('button', { name: '使用这 1 个区域' }).click()
  await expect(page.getByRole('dialog', { name: '选择 PDF 图像区域' })).toHaveCount(0)
})
