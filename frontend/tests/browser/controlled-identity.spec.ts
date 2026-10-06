import { test, expect, assertNoPageOverflow } from './fixtures'

test('controlled anonymous login rejects invalid keys and enters through the actual login form', async ({
  page,
  api,
}) => {
  api.role = null
  await page.goto('/qa')
  await page.locator('.intro-enter').click()
  await page.getByLabel('访问密钥', { exact: true }).fill('controlled-invalid')
  await page.getByRole('button', { name: '验证并进入' }).click()
  await expect(page.locator('.auth-error')).toBeVisible()
  await expect(page.locator('.app-shell')).toHaveCount(0)
  await page.getByLabel('访问密钥', { exact: true }).fill('controlled-researcher')
  await page.getByRole('button', { name: '验证并进入' }).click()
  await expect(page.locator('.qa-layout')).toBeVisible()
  await expect(page.locator('.auth-modal')).toHaveCount(0)
  await expect(page.locator('.sidebar a[href="/settings"]')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('dr_api_key'))).toBeNull()
})

for (const role of ['admin', 'researcher', 'reader'] as const) {
  test(`controlled ${role} navigation and deep link restrictions`, async ({ page, api }) => {
    api.role = role
    await page.goto('/history')
    await expect(page.locator('.app-shell')).toBeVisible()
    await expect(page.locator('.sidebar a[href="/settings"]')).toHaveCount(role === 'admin' ? 1 : 0)
    await expect(page.locator('.sidebar a[href="/qa"]')).toHaveCount(role === 'reader' ? 0 : 1)
    if (role !== 'admin') {
      await page.goto('/settings')
      await expect(page.getByText('当前身份没有此操作权限')).toBeVisible()
    }
    if (role === 'reader') {
      await page.goto('/qa/controlled-conversation')
      await expect(page.locator('.qa-layout')).toHaveCount(0)
    }
    await assertNoPageOverflow(page)
  })
}

test('controlled credential change clears private conversation cache', async ({ page, api }) => {
  await page.goto('/qa/controlled-conversation')
  await expect(page.getByRole('article', { name: '第 1 轮问答' })).toContainText('CASSI 是什么')
  api.role = 'reader'
  api.messages = []
  api.conversationTitle = '另一个身份的会话'
  await page.evaluate(() =>
    window.dispatchEvent(new StorageEvent('storage', { key: 'dr_api_key' })),
  )
  await expect(page.locator('.qa-layout')).toHaveCount(0)
  await expect(page.getByText('CASSI 是什么？', { exact: true })).toHaveCount(0)
})
