// Diagnostic assertions record current defects; these are not desired product behavior.
import { test, expect, assertNoPageOverflow } from '../../frontend/tests/browser/fixtures'

test('draft is lost after leaving and returning to QA', async ({ page, api }, testInfo) => {
  void api
  await page.goto('/qa/controlled-conversation')
  const composer = page.locator('.qa-composer textarea')
  await expect(composer).toBeEnabled()
  await composer.fill('尚未发送的第二个研究问题')
  await page.locator('.sidebar-nav a[href="/history"]').click()
  await page.locator('.sidebar-nav a[href="/qa"]').click()
  await expect(page.locator('.qa-composer textarea')).toHaveValue('')
  await page.screenshot({ path: testInfo.outputPath('draft-loss.png'), fullPage: true })
})

test('late request failure overwrites the next draft', async ({ page, api }, testInfo) => {
  void api
  await page.goto('/qa/controlled-conversation')
  const composer = page.locator('.qa-composer textarea')
  await expect(composer).toBeEnabled()
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  let entered!: () => void
  const ready = new Promise<void>(resolve => { entered = resolve })
  await page.route('**/api/qa/conversations/*/messages/stream', async route => {
    entered()
    await gate
    await route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: '受控诊断错误' }) })
  })
  await composer.fill('第一个已发送问题')
  await composer.press('Enter')
  await ready
  await composer.fill('第二个尚未发送问题')
  release()
  await expect(page.getByRole('alert')).toContainText('受控诊断错误')
  await expect(composer).toHaveValue('第一个已发送问题')
  await page.screenshot({ path: testInfo.outputPath('draft-overwritten.png'), fullPage: true })
})

test('controlled mobile QA fits the viewport', async ({ page, api }, testInfo) => {
  void api
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/qa/controlled-conversation')
  await expect(page.locator('.qa-composer textarea')).toBeEnabled()
  await assertNoPageOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('mobile-qa.png'), fullPage: true })
})
