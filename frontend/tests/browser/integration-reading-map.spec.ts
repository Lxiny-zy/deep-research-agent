import { test, expect, type Page } from '@playwright/test'

const admin = { Authorization: 'Bearer synthetic-browser-admin-key' }
async function login(page: Page, run: string) {
  await page.addInitScript(() => {
    localStorage.setItem('dr_welcome_tour_seen', '1')
    sessionStorage.setItem('sr_intro_seen', '1')
  })
  await page.goto(`/runs/${run}`)
  await page.locator('.intro-enter').click()
  await page.getByLabel('访问密钥', { exact: true }).fill('synthetic-browser-alice-key')
  await page.getByRole('button', { name: '验证并进入' }).click()
  await page.locator('.reading-map > summary').click()
  await expect(page.locator('.reading-map-review')).toContainText('当前版本有逐段审核记录')
}

for (const width of [1440, 390]) {
  test(`reading map keeps multiple candidates explicit and locates the selected PDF at ${width}`, async ({
    page,
    request,
  }, testInfo) => {
    const meta = await (await request.get('/__browser_fixture__/state', { headers: admin })).json()
    await page.setViewportSize({ width, height: 900 })
    await login(page, meta.reading_run_id)
    await page
      .locator('.reading-unit-button')
      .filter({ hasText: meta.reading_alpha_quote })
      .first()
      .click()
    const detail = page.getByRole('region', { name: '所选段落与原文依据' })
    await expect(detail.locator('.reading-anchor')).toHaveCount(2)
    await expect(detail).toContainText('有多个候选位置')
    await expect(detail.getByRole('button', { name: '定位这条原文' })).toHaveCount(0)
    await page
      .locator('.reading-unit-button')
      .filter({ hasText: meta.reading_beta_quote })
      .first()
      .click()
    await expect(detail.locator('.reading-anchor')).toHaveCount(1)
    await detail.getByRole('button', { name: '定位这条原文' }).click()
    await expect(page).toHaveURL(new RegExp(`/runs/${meta.reading_run_id}/read$`))
    expect(page.url()).not.toContain('Beta')
    await expect(page.locator('.pdf-toolbar-note')).toContainText('已定位完整引文 · 第 2 页')
    await expect(page.locator('.pdf-page[aria-label="第 2 页"] .pdf-highlight')).toBeVisible()
    await page.getByRole('tab', { name: '阅读导览', exact: true }).click()
    await expect(page.locator('.pdf-highlight')).toHaveCount(0)
    await page
      .locator('.reading-unit-button')
      .filter({ hasText: meta.reading_alpha_quote })
      .first()
      .click()
    await expect(page.locator('.reading-anchor')).toHaveCount(2)
    await page.getByRole('tab', { name: '原文', exact: true }).click()
    await expect(page.locator('.pdf-highlight')).toHaveCount(0)
    await page.getByRole('tab', { name: '阅读导览', exact: true }).click()
    await expect(page.locator('.reading-unit-button[aria-pressed="true"]')).toContainText(
      meta.reading_alpha_quote,
    )
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('reading-map.png') })
  })
}

test('full-text counterexamples remain separate and locate the actual source quotation', async ({
  page,
  request,
}) => {
  const meta = await (await request.get('/__browser_fixture__/state', { headers: admin })).json()
  await login(page, meta.reading_counter_run_id)
  await page.locator('.reading-unit-button').filter({ hasText: '本论文未给出' }).first().click()
  const fulltext = page.getByRole('region', { name: '全文回查片段与反例' })
  await expect(fulltext).toContainText('回查反例')
  await expect(fulltext).toContainText(meta.reading_counter_quote)
  await expect(fulltext.getByRole('button', { name: '定位这条原文' })).toHaveCount(0)
  await fulltext.getByRole('button', { name: '定位回查片段' }).click()
  await expect(page).toHaveURL(new RegExp(`/runs/${meta.reading_counter_run_id}/read$`))
  await expect(page.locator('.pdf-toolbar-note')).toContainText('已定位完整引文 · 第 1 页')
})

test('a changed document rejects stale reading anchors and reload exposes an unbound review', async ({
  page,
  request,
}) => {
  const meta = await (await request.get('/__browser_fixture__/state', { headers: admin })).json()
  await login(page, meta.reading_run_id)
  await page
    .locator('.reading-unit-button')
    .filter({ hasText: meta.reading_alpha_quote })
    .first()
    .click()
  await expect(page.locator('.reading-anchor')).toHaveCount(2)
  await page
    .locator('.reading-unit-button')
    .filter({ hasText: meta.reading_beta_quote })
    .first()
    .click()
  await expect(page.getByRole('button', { name: '定位这条原文' })).toBeVisible()
  expect(
    (await request.post('/__browser_fixture__/invalidate-reading', { headers: admin })).ok(),
  ).toBe(true)
  await page
    .locator('.reading-unit-button')
    .filter({ hasText: meta.reading_alpha_quote })
    .first()
    .click()
  await expect(page.locator('.reading-map')).toContainText('文档版本已变化，请重新加载报告后查看。')
  await expect(page.getByRole('button', { name: '定位这条原文' })).toHaveCount(0)
  await page.getByRole('button', { name: '重新加载报告', exact: true }).click()
  await expect(page.locator('.reading-map-review')).toContainText('没有可用的逐段审核绑定')
})
