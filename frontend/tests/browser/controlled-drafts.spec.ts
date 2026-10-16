import { test, expect } from './fixtures'

for (const path of ['/qa/controlled-conversation', '/runs/controlled-run/read']) {
  test(`unsent input survives navigation and refresh ${path}`, async ({ page, api }) => {
    void api
    await page.goto(path)
    const input = page.locator(path.startsWith('/qa') ? '.qa-composer textarea' : '.reader-composer textarea')
    await expect(input).toBeEnabled()
    await input.fill('尚未发送的下一问')
    await page.locator('.sidebar-nav a[href="/history"]').click()
    await page.goBack()
    await expect(input).toHaveValue('尚未发送的下一问')
    await page.reload()
    await expect(input).toHaveValue('尚未发送的下一问')
  })

  test(`late failure preserves next question ${path}`, async ({ page, api }) => {
    void api
    await page.goto(path)
    const input = page.locator(path.startsWith('/qa') ? '.qa-composer textarea' : '.reader-composer textarea')
    await expect(input).toBeEnabled()
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    let entered!: () => void
    const ready = new Promise<void>(resolve => { entered = resolve })
    await page.route('**/api/qa/conversations/*/messages/stream', async route => {
      entered()
      await gate
      await route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: '受控诊断错误' }) })
    })
    await input.fill('第一个已发送问题')
    await input.press('Enter')
    await ready
    await input.fill('第二个尚未发送问题')
    release()
    await expect(page.getByRole('alert')).toContainText('受控诊断错误')
    await expect(input).toHaveValue('第二个尚未发送问题')
  })
}
