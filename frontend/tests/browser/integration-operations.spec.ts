import { test, expect } from '@playwright/test'

test('real operational overview scopes identities and preserves unknown usage on mobile', async ({
  page,
  request,
}, testInfo) => {
  const requests: string[] = []
  page.on('request', (req) => {
    if (req.url().includes('/api/operations?')) requests.push(req.url())
  })
  await page.addInitScript(() => {
    localStorage.setItem('dr_welcome_tour_seen', '1')
    sessionStorage.setItem('sr_intro_seen', '1')
  })
  await page.goto('/history')
  await page.locator('.intro-enter').click()
  await page.getByLabel('访问密钥', { exact: true }).fill('synthetic-browser-alice-key')
  await page.getByRole('button', { name: '验证并进入' }).click()
  await expect(page.locator('.operations-overview > summary')).toBeVisible()
  expect(requests).toHaveLength(0)
  const mine = page.waitForResponse(
    (response) =>
      response.url().includes('/api/operations?') && response.url().includes('scope=mine'),
  )
  await page.locator('.operations-overview > summary').click()
  const privateResponse = await mine
  const own = await privateResponse.json()
  expect(own.scope).toBe('mine')
  expect(own.model_calls.recorded_attempts).toBe(2)
  expect(own.model_calls.retries).toBe(1)
  expect(own.model_calls.usage.total_tokens).toMatchObject({ known_total: null, unknown_calls: 2 })
  expect(await privateResponse.text()).not.toContain('fixture-only-private')
  const total = page
    .getByRole('table', { name: '提供方返回的用量' })
    .getByRole('row')
    .filter({ hasText: '总量' })
  await expect(total.locator('td').first()).toHaveText('未知')
  await expect(page.getByRole('combobox', { name: '统计对象' })).toHaveCount(0)
  const forbidden = await request.get('/api/operations?scope=workspace', {
    headers: { Authorization: 'Bearer synthetic-browser-alice-key' },
  })
  expect(forbidden.status()).toBe(403)

  await page.getByRole('button', { name: /当前身份/ }).click()
  await page.getByLabel('访问密钥', { exact: true }).fill('synthetic-browser-admin-key')
  await page.getByRole('button', { name: '验证并进入' }).click()
  await page.locator('.operations-overview > summary').click()
  const all = page.waitForResponse(
    (response) =>
      response.url().includes('/api/operations?') && response.url().includes('scope=workspace'),
  )
  await page.getByRole('combobox', { name: '统计对象' }).selectOption('workspace')
  const workspace = await (await all).json()
  expect(workspace.scope).toBe('workspace')
  expect(workspace.coverage.research_records).toBeGreaterThan(own.coverage.research_records)
  expect(workspace.model_calls.recorded_attempts).toBe(3)
  await page.setViewportSize({ width: 390, height: 844 })
  const table = page.getByLabel('各场景运行统计，可横向滚动', { exact: true })
  await table.focus()
  await expect(table).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => table.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('operations-mobile.png') })
})
