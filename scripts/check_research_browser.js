async (page) => {
  const root = 'D:/Cursor-edit/Project_test/deep-research-agent/docs/validation/n2-long-layout';
  const observations = [];
  await page.goto('file:///' + root + '/review.html');
  for (const [name, width, height, color] of [
    ['desktop', 1366, 900, 'light'],
    ['mobile', 390, 844, 'light'],
    ['low-height-dark', 1280, 600, 'dark'],
  ]) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: color });
    const table = page.locator('table').first();
    const tableCount = await page.locator('table').count();
    const rows = await table.locator('tr').count();
    const math = await table.locator('.math-svg svg').count();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    const references = await page.locator('main').innerText();
    if (tableCount !== 2 || rows !== 37 || math !== 36 || overflow || !references.includes('Reference-036')) {
      throw new Error(JSON.stringify({ name, tableCount, rows, math, overflow }));
    }
    await table.scrollIntoViewIfNeeded();
    await table.evaluate(el => { el.scrollLeft = 0; });
    await page.screenshot({ path: root + '/' + name + '-table.png' });
    const scrolling = await table.evaluate(el => {
      el.scrollLeft = el.scrollWidth;
      return { width: el.clientWidth, contentWidth: el.scrollWidth, offset: el.scrollLeft };
    });
    if (scrolling.contentWidth > scrolling.width && scrolling.offset <= 0) {
      throw new Error('Long table is not horizontally scrollable');
    }
    if (name === 'mobile') {
      await page.screenshot({ path: root + '/mobile-formulas.png' });
    }
    observations.push({ name, width, height, color, tableCount, rows, math, overflow, scrolling });
  }
  return { fixture: 'synthetic_only', observations };
}
