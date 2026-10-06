import { test, expect, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const admin = { Authorization: 'Bearer synthetic-browser-admin-key' }
const alice = { Authorization: 'Bearer synthetic-browser-alice-key' }
async function openPanel(page: Page, runId: string) {
  await page.addInitScript(() => {
    localStorage.setItem('dr_welcome_tour_seen', '1')
    sessionStorage.setItem('sr_intro_seen', '1')
    localStorage.setItem('sr_sidebar_collapsed', '1')
  })
  await page.goto(`/runs/${runId}`)
  await page.locator('.intro-enter').click()
  await page.getByLabel('访问密钥', { exact: true }).fill('synthetic-browser-alice-key')
  await page.getByRole('button', { name: '验证并进入' }).click()
  await page.locator('.acceptance-panel > summary').click()
  await expect(page.getByLabel('总体人工结论')).toHaveValue('pending')
}
async function firstOption(page: Page, label: string) {
  const select = page.getByRole('combobox', { name: label, exact: true })
  await expect(select).toBeVisible()
  await expect.poll(() => select.locator('option').count()).toBeGreaterThan(1)
  const value = await select.locator('option').nth(1).getAttribute('value')
  await select.selectOption(value!)
  return value!
}

test('acceptance records preserve selected excerpts, first outcome and prior package across version conflict', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000)
  const meta = await (await request.get('/__browser_fixture__/state', { headers: admin })).json()
  await openPanel(page, meta.run_id)
  await page.getByLabel('首次实际结果').selectOption('error')
  if (
    !(await page
      .locator('.acceptance-issue-editor')
      .evaluate((element) => (element as HTMLDetailsElement).open))
  )
    await page.locator('.acceptance-issue-editor > summary').click()
  await firstOption(page, '正文或图表位置')
  await expect(page.getByLabel('将必要的正文片段附入记录')).not.toBeChecked()
  await firstOption(page, '查看原文依据')
  await expect(page.getByLabel('将这段原文附入问题记录')).not.toBeChecked()
  await page.getByLabel('具体观察').fill('需要人工检查这个位置，先保留待确认。')
  await page.getByRole('button', { name: '加入问题清单' }).click()
  const firstResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/acceptance/records') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '保存人工验收记录', exact: true }).click()
  const response = await firstResponse
  expect(response.status()).toBe(201)
  expect(response.request().postDataJSON().issues[0]).toMatchObject({
    excerpt: '',
    evidence_selections: [],
    conclusion: 'pending',
  })
  const first = await response.json()
  expect(first.first_attempt_status).toBe('error')
  expect(first.conclusion).toBe('pending')
  const firstDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载最小问题包' }).click()
  const firstBytes = await readFile((await (await firstDownload).path())!)
  expect(firstBytes.toString()).not.toContain('Which PSNR was reported?')

  await page.getByLabel('补充说明').fill('文档变化时必须保留这段未提交观察。')
  expect((await request.post('/__browser_fixture__/next-version', { headers: admin })).ok()).toBe(
    true,
  )
  const conflictResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/acceptance/records') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '保存人工验收记录', exact: true }).click()
  expect((await conflictResponse).status()).toBe(409)
  await expect(page.getByLabel('补充说明')).toHaveValue('文档变化时必须保留这段未提交观察。')
  await page.getByRole('button', { name: '加载最新报告版本' }).click()
  await page.getByRole('button', { name: '清空未提交内容并登记当前版本' }).click()
  await page.getByLabel('本次性质').selectOption('recovery')
  await page.getByLabel('先前验收记录').selectOption(first.id)
  await expect(page.getByLabel('首次实际结果')).toBeDisabled()
  if (
    !(await page
      .locator('.acceptance-issue-editor')
      .evaluate((element) => (element as HTMLDetailsElement).open))
  )
    await page.locator('.acceptance-issue-editor > summary').click()
  await firstOption(page, '正文或图表位置')
  await firstOption(page, '查看原文依据')
  await page.getByLabel('将这段原文附入问题记录').check()
  await page.getByLabel('必要的依据片段').fill('Alpha scored 95')
  await page.getByLabel('具体观察').fill('恢复观察明确选择原文片段。')
  await page.getByRole('button', { name: '加入问题清单' }).click()
  await page.getByLabel('总体人工结论').selectOption('pass')
  const recoveryResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/acceptance/records') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '保存人工验收记录', exact: true }).click()
  const recoveredResponse = await recoveryResponse
  expect(recoveredResponse.status()).toBe(201)
  const recovered = await recoveredResponse.json()
  expect(recovered.parent_record_id).toBe(first.id)
  expect(recovered.first_attempt_status).toBe('error')
  expect(recovered.document_version).not.toBe(first.document_version)
  expect(recoveredResponse.request().postDataJSON().issues[0].evidence_selections[0].excerpt).toBe(
    'Alpha scored 95',
  )
  const prior = await request.get(
    `/api/runs/${meta.run_id}/acceptance/records/${first.id}/package`,
    { headers: alice },
  )
  expect(await prior.body()).toEqual(firstBytes)
  const original = await (
    await request.get(`/api/runs/${meta.run_id}/acceptance/records/${first.id}`, { headers: alice })
  ).json()
  expect(original.conclusion).toBe('pending')
})

test('mobile acceptance form keeps required controls reachable', async ({
  page,
  request,
}, testInfo) => {
  const meta = await (await request.get('/__browser_fixture__/state', { headers: admin })).json()
  await page.setViewportSize({ width: 390, height: 844 })
  await openPanel(page, meta.run_id)
  await page.locator('.acceptance-issue-editor > summary').click()
  await firstOption(page, '正文或图表位置')
  await page.getByLabel('具体观察').fill('手机页面人工观察。')
  await page.getByRole('button', { name: '加入问题清单' }).click()
  await page.getByRole('button', { name: '保存人工验收记录', exact: true }).scrollIntoViewIfNeeded()
  await expect(page.getByRole('button', { name: '保存人工验收记录', exact: true })).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('acceptance-mobile.png') })
})
