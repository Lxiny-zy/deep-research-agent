import { test, expect } from '@playwright/test'

// Explicit opt-in only. This suite never submits research/QA or calls model APIs.
// Provision isolated identities server-side; do not use production credentials.
test('isolated real API anonymous and authenticated browser boundary', async ({
  page,
  baseURL,
}) => {
  const key = process.env.DR_BROWSER_TEST_KEY
  if (!key) throw new Error('Set DR_BROWSER_TEST_KEY to an isolated test access key.')
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== new URL(baseURL!).origin) {
      return route.abort('blockedbyclient')
    }
    if (url.pathname.startsWith('/api/') && request.method() !== 'GET')
      return route.abort('blockedbyclient')
    return route.continue()
  })
  await page.addInitScript(() => {
    localStorage.setItem('dr_welcome_tour_seen', '1')
    sessionStorage.setItem('sr_intro_seen', '1')
  })
  const anonymous = await page.request.get('/api/config')
  expect(anonymous.status(), 'This environment must require authentication').toBe(401)
  await page.goto('/history')
  await expect(page.locator('.app-shell')).toHaveCount(0)
  await page.locator('.intro-enter').click()
  // Avoid locator.fill's retry log printing a credential if the input is absent.
  await page.getByLabel('访问密钥', { exact: true }).evaluate((input, value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }, key)
  await page.getByRole('button', { name: '验证并进入' }).click()
  await expect(page.locator('.app-shell')).toBeVisible()
  await expect(page.locator('.auth-modal')).toHaveCount(0)
  await page.reload()
  await expect(page.locator('.app-shell')).toBeVisible()
})
