import {
  test,
  expect,
  assertHitTarget,
  assertNoPageOverflow,
  assertFullyInViewport,
} from './fixtures'

const viewports = [
  [1504, 993],
  [1280, 720],
  [1024, 768],
  [901, 700],
  [900, 700],
  [760, 800],
  [390, 844],
  [320, 640],
  [1200, 580],
]

for (const theme of ['light', 'dark']) {
  for (const [width, height] of viewports) {
    test(`controlled layout ${width}x${height} ${theme}`, async ({ page, api }, testInfo) => {
      await page.setViewportSize({ width, height })
      await page.addInitScript((value) => localStorage.setItem('sr_theme', value), theme)
      api.submissionError = `受控故障：${'long-endpoint-name-'.repeat(18)}；输入已保留。`
      await page.goto('/')
      await expect(page.locator('.home-config')).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await page.locator('#query').fill('快照式高光谱成像的深度展开方法有哪些适用条件？')
      await page.evaluate(() => document.fonts.ready)

      async function geometry() {
        await assertNoPageOverflow(page)
        const layout = await page.evaluate(() => {
          const panel = document.querySelector('.home-config')!
          const footer = document.querySelector('.home-actionbar')!
          const main = document.querySelector('.home-main')!
          const button = footer.querySelector('button')!
          const p = panel.getBoundingClientRect(),
            f = footer.getBoundingClientRect()
          const m = main.getBoundingClientRect(),
            b = button.getBoundingClientRect()
          return {
            panelGap: f.top - p.bottom,
            mainGap: f.top - m.bottom,
            position: getComputedStyle(panel).position,
            top: getComputedStyle(panel).top,
            footerPosition: getComputedStyle(footer).position,
            padding: getComputedStyle(footer).paddingLeft,
            buttonContained: b.left >= f.left - 1 && b.right <= f.right + 1,
          }
        })
        expect(layout.panelGap).toBeGreaterThanOrEqual(-1)
        expect(layout.mainGap).toBeGreaterThanOrEqual(-1)
        expect(layout.footerPosition).toBe('static')
        expect(layout.buttonContained).toBe(true)
        if (width > 900 && height > 600) expect(layout.position).toBe('sticky')
        else expect(['auto', '0px']).toContain(layout.top)
        if (width <= 760) expect(layout.padding).toBe('10px')
      }

      for (const expanded of [false, true]) {
        if (expanded) {
          await page.locator('.home-disclosure > summary').click()
          await page.locator('.run-params-toggle').click()
          await page.locator('.preflight-button').click()
          await expect(page.locator('.preflight-result')).toBeVisible()
        }
        for (const ratio of [0, 0.5, 1]) {
          await page.evaluate(
            (value) =>
              window.scrollTo(0, (document.documentElement.scrollHeight - innerHeight) * value),
            ratio,
          )
          await geometry()
        }
      }
      for (const selector of ['#research-project', '#workflow', '.preflight-button'])
        await assertHitTarget(page, selector)
      await page.locator('.home-actionbar .btn-lg').click()
      const error = page.locator('.home-actionbar-error')
      await expect(error).toBeVisible()
      await assertFullyInViewport(page, '.home-actionbar-error')
      await geometry()
      await expect(page.locator('#query')).toHaveValue(
        '快照式高光谱成像的深度展开方法有哪些适用条件？',
      )
      if ([1504, 390, 320].includes(width)) {
        await page.screenshot({ path: testInfo.outputPath('viewport.png') })
        await page.screenshot({ path: testInfo.outputPath('layout.png'), fullPage: true })
      }
    })
  }
}
