async (page) => {
  const root = 'D:/Cursor-edit/Project_test/deep-research-agent-upgrade/artifacts/reliability-ui/'
  await page.goto('http://127.0.0.1:5182/preview/scientific-document')
  await page.getByTestId('structured-chart-multi-series').waitFor()
  const results = []
  for (const [width, height] of [[1440, 1100], [820, 1100], [390, 844]]) {
    await page.setViewportSize({ width, height })
    await page.screenshot({ path: `${root}scientific-${width}.png`, fullPage: true })
    results.push(await page.evaluate(() => ({ width: innerWidth, pageWidth: document.documentElement.scrollWidth, math: document.querySelectorAll('.katex').length, points: document.querySelectorAll('svg [data-value]').length, tables: document.querySelectorAll('.structured-document-preview table').length })))
  }
  const chartScroll = page.locator('.structured-chart-scroll')
  const tableScroll = page.locator('.structured-document-table-scroll').first()
  const scrollEvidence = []
  for (const region of [chartScroll, tableScroll]) {
    await region.evaluate((node) => { node.scrollLeft = 0 })
    await region.focus()
    await page.keyboard.press('ArrowRight')
    await page.waitForFunction((node) => node.scrollLeft > 0, await region.elementHandle())
    const keyboardScroll = await region.evaluate((node) => node.scrollLeft)
    await region.evaluate((node) => { node.scrollLeft = node.scrollWidth })
    scrollEvidence.push({ keyboardScroll, ...await region.evaluate((node) => ({ width: node.clientWidth, scrollWidth: node.scrollWidth, scrollLeft: node.scrollLeft, endVisible: Math.abs(node.scrollWidth - node.clientWidth - node.scrollLeft) < 2 })) })
  }
  await page.screenshot({ path: `${root}scientific-390-scrolled.png`, fullPage: true })
  if (scrollEvidence.some((item) => item.scrollWidth <= item.width || item.scrollLeft <= 0 || !item.endVisible)) throw new Error('Mobile scrolling did not reveal final data')
  for (const form of ['bar', 'dot', 'grouped_bar', 'scatter', 'line']) {
    await page.getByRole('combobox').first().selectOption(form)
    const marks = await page.locator('.structured-chart-svg [data-value]').count()
    if (marks !== (form === 'scatter' ? 2 : 5)) throw new Error(`${form}: wrong marks ${marks}`)
  }
  await page.setViewportSize({ width: 1440, height: 1100 })
  await page.getByRole('combobox').first().selectOption('grouped_bar')
  const cite = page.getByTestId('structured-table-observations').getByRole('button', { name: '查看引用 1 的证据' }).first()
  await cite.focus()
  await page.keyboard.press('Enter')
  await page.getByRole('dialog').waitFor()
  if (!await page.getByRole('dialog').getByText('Alpha: -2.50 ± 0.10 meV; B: 3.20 meV.', { exact: true }).isVisible()) throw new Error('Original evidence excerpt missing')
  await page.screenshot({ path: `${root}scientific-evidence.png`, fullPage: true })
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => document.activeElement?.closest('[data-testid="structured-table-observations"]'))
  if (!await cite.evaluate((node) => node === document.activeElement)) throw new Error('Evidence focus return failed')
  if (await page.getByRole('button', { name: /查看引用 9/ }).count()) throw new Error('Unmapped citation became clickable')
  if (results.some((result) => result.pageWidth > result.width)) throw new Error(`Horizontal page overflow: ${JSON.stringify(results)}`)
  return { results, scrollEvidence, forms: 5, keyboardEvidence: true, focusReturn: true, unmappedCitationInert: true }
}
