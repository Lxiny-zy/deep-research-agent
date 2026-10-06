import { test, expect, assertNoPageOverflow, templates } from './fixtures'

for (const width of [1504, 390]) {
  for (const theme of ['light', 'dark']) {
    test(`workbench first screen keeps the research input visible ${width} ${theme}`, async ({
      page,
      api,
    }, testInfo) => {
      api.role = 'researcher'
      await page.setViewportSize({ width, height: 844 })
      await page.addInitScript((value) => localStorage.setItem('sr_theme', value), theme)
      await page.goto('/')
      await expect(page.locator('#query')).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await page.evaluate(() => document.fonts.ready)
      const input = await page.locator('#query').boundingBox()
      expect(input!.y).toBeGreaterThanOrEqual(0)
      expect(input!.y + input!.height).toBeLessThanOrEqual(844)
      await assertNoPageOverflow(page)
      await page.screenshot({ path: testInfo.outputPath('workbench-first-screen.png') })
    })
  }
}

test('task overflow hint follows available choices and keyboard selection remains visible', async ({
  page,
  api,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  api.taskTemplates = templates.slice(0, 2)
  await page.goto('/')
  await expect(page.locator('.task-grid input')).toHaveCount(2)
  await expect(page.locator('.task-scroll-hint')).toHaveCount(0)
  api.taskTemplates = templates
  await page.reload()
  await expect(page.locator('.task-scroll-hint')).toHaveText('左右滑动查看更多任务 ↔')
  await page.locator('#query').fill('用户正在编辑的研究问题')
  const first = page.locator('.task-grid input').first()
  await first.focus()
  await first.press('ArrowLeft')
  const last = page.locator('.task-grid input').last()
  await expect(last).toBeChecked()
  const contained = await page.locator('.task-grid').evaluate((grid) => {
    const parent = grid.getBoundingClientRect()
    const item = grid.querySelector('.is-selected')!.getBoundingClientRect()
    return item.left >= parent.left - 1 && item.right <= parent.right + 1
  })
  expect(contained).toBe(true)
  await expect(page.locator('#query')).toHaveValue('用户正在编辑的研究问题')
  await assertNoPageOverflow(page)
})
