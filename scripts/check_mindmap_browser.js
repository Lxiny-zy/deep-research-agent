async (page) => {
  const observations = [];
  await page.goto('http://127.0.0.1:18877/mindmap.html');
  for (const [name, width, height, color] of [
    ['desktop', 1366, 768, 'light'],
    ['mobile', 390, 844, 'light'],
    ['low-height-dark', 1280, 600, 'dark'],
  ]) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: color });
    await page.reload();
    await page.locator('[data-focus-branch="0"]').click();
    const leaf = page.locator('g.node[data-node-path="0.0"]');
    await leaf.focus();
    await page.keyboard.press('Enter');
    const inspector = await page.locator('#node-inspector').innerText();
    const math = await page.locator('#node-inspector .math-svg').count();
    const count = await page.locator('#complete-outline .node-content').count();
    const expected = await page.locator('#mindmap-index').evaluate(el => JSON.parse(el.textContent).node_count);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (!inspector.includes('合成说明') || math === 0 || count !== expected || overflow) {
      throw new Error(JSON.stringify({ name, inspector, math, count, expected, overflow }));
    }
    await page.locator('#node-inspector a[href="#source-1"]').click();
    const sourceOpened = await page.locator('#complete-outline').evaluate(el => el.open);
    if (!sourceOpened || !(await page.locator('#source-1').innerText()).includes('不作科研证据')) {
      throw new Error('The node citation did not open its original source');
    }
    await page.locator('#complete-outline').evaluate(el => { el.open = false; });
    const branch = page.locator('g.node[data-node-path="0"]');
    await branch.focus();
    await page.keyboard.press('Enter');
    const collapsed = (await branch.getAttribute('aria-expanded')) === 'false';
    if (!collapsed) throw new Error('Branch keyboard collapse failed');
    await page.keyboard.press('Enter');
    await leaf.focus();
    await page.keyboard.press('Enter');
    await page.screenshot({
      path: `D:/Cursor-edit/Project_test/deep-research-agent/docs/validation/n7-readability/${name}.png`,
      fullPage: true,
    });
    observations.push({ name, width, height, color, overflow, nodes: count, math, collapsed, sourceOpened });
  }
  return { fixture: 'synthetic_only', observations };
}
