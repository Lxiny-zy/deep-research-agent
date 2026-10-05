// Browser verification for playwright-cli run-code: open this project's Vite
// page first. Requires the local, untracked reported-multispectral-original.pdf
// fixture under artifacts/; no production API or model is called.
async page => {
  const origin = new URL(page.url()).origin;
  await page.unrouteAll({ behavior: 'wait' });
  await page.route(`${origin}/api/runs/pdf-preview/reader/original/pdf`, route =>
    route.fulfill({ path: 'artifacts/reported-multispectral-original.pdf', contentType: 'application/pdf' }));
  await page.route(`${origin}/pdf-evidence-repair-preview`, route => route.fulfill({
    contentType: 'text/html',
    body: `<!doctype html><html><head><meta charset="utf-8"></head><body>
      <main id="root" class="main-content" style="padding:0;height:100vh"></main>
      <script type="module">
        import RefreshRuntime from '/@react-refresh';
        RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$ = () => {};
        window.$RefreshSig$ = () => type => type;
        window.__vite_plugin_react_preamble_installed__ = true;
        const { default: React } = await import('/node_modules/.vite/deps/react.js');
        const { useState } = React;
        const { default: ReactDOM } = await import('/node_modules/.vite/deps/react-dom_client.js');
        const { createRoot } = ReactDOM;
        const { default: PdfViewer } = await import('/src/components/PdfViewer.tsx');
        const { default: QaAnswerBody } = await import('/src/components/QaAnswerBody.tsx');
        import '/src/styles/index.css';
        const source = 'https://workspace.invalid/attachments/paper';
        const text = '论文创新点 [1]。';
        const id = 'a'.repeat(24);
        const evidence = [
          { source_url: source, origin: 'paper', support_id: 'first', statement: '深度引导网络', evidence_quote: 'we introduce a deep guided neural network. This deep guided neural network leverages the structure of the fully available center view by building local models of the available pixels of the peripheral view and the corresponding pixels of the center view.' },
          { source_url: source, origin: 'paper', support_id: 'second', statement: '重建网络数据增强', evidence_quote: 'Again, this network is trained using the proposed pseudo spectral data augmentation of Section IV.' },
        ];
        const binding = { source_body: text, body: '论文创新点 [[1]](#cite-o-' + id + ')。', binding_status: 'bound', documents: [{ index: 1, identity: 'p', title: '', reference: '', url: '', locations: [1] }], locations: [{ index: 1, document: 1, url: source, label: '', content_hashes: [] }], occurrences: [{ id, run: 0, document: 1, locations: [1], unit_id: 'unit', scope: 'reviewed_unit', evidence_ids: ['first', 'second'] }] };
        function Preview() {
          const [highlight, setHighlight] = useState(null);
          const locate = item => setHighlight(previous => ({quote: item.evidence_quote, token: (previous?.token ?? 0) + 1}));
          return React.createElement('div', { className: 'reader-layout', style: {height: '100%', minHeight: 0} },
            React.createElement('div', {className: 'reader-chat', style: {padding: '24px'}}, React.createElement(QaAnswerBody, { text, binding, citations: [source], evidence, onLocate: locate })),
            React.createElement('div', {className: 'reader-pane'}, React.createElement(PdfViewer, {runId: 'pdf-preview', documentId: 'original', highlight})));
        }
        createRoot(document.getElementById('root')).render(React.createElement(Preview));
      </script></body></html>`,
  }));
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(`${origin}/pdf-evidence-repair-preview`);
  await page.getByText('共 12 页', { exact: true }).waitFor();
  const measure = () => page.evaluate(() => {
    const mark = document.querySelector('.pdf-highlight')?.getBoundingClientRect();
    const scroller = document.querySelector('.pdf-scroller').getBoundingClientRect();
    return { status: document.querySelector('.pdf-toolbar-note')?.textContent,
      marks: document.querySelectorAll('.pdf-highlight').length,
      viewportWidth: scroller.width,
      error: mark ? Math.abs((mark.top + mark.bottom - scroller.top - scroller.bottom) / 2) : null,
      visible: !!mark && mark.bottom > scroller.top && mark.top < scroller.bottom };
  });
  const results = [];
  for (const index of [0, 1, 0]) {
    await page.getByRole('button', { name: '定位引用 1 的论文依据' }).click();
    await page.getByRole('dialog', { name: '选择论文依据' }).waitFor();
    await page.getByRole('button', { name: '定位这条依据', exact: true }).nth(index).click();
    await page.locator('.pdf-toolbar-note').filter({ hasText: '已定位完整引文' }).waitFor();
    await page.waitForTimeout(1000);
    results.push({ scenario: `quote-${index + 1}-closed`, ...await measure() });
    await page.getByRole('button', { name: '定位引用 1 的论文依据' }).click();
    await page.waitForTimeout(1000);
    results.push({ scenario: `quote-${index + 1}-drawer-open`, ...await measure() });
    await page.getByRole('button', { name: '关闭', exact: true }).click();
    await page.waitForTimeout(1000);
    results.push({ scenario: `quote-${index + 1}-drawer-closed`, ...await measure() });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(1000);
  results.push({ scenario: 'window-resize', ...await measure() });
  for (const result of results) {
    if (!result.visible || result.error === null || result.error > 3) {
      throw new Error(`Quotation left the center of the PDF viewport: ${JSON.stringify(result)}`);
    }
  }
  const first = results.find(result => result.scenario === 'quote-1-closed');
  const second = results.find(result => result.scenario === 'quote-2-closed');
  if (first.marks !== 5 || second.marks !== 3) throw new Error('The selected quotations were not highlighted separately');
  if (results[0].viewportWidth <= results[1].viewportWidth) throw new Error('Evidence drawer did not resize the PDF viewport');
  if (await page.locator('.pdf-text-measure').count()) throw new Error('Temporary text measurement layer leaked');
  await page.screenshot({ path: 'artifacts/pdf-evidence-locator-repair.png' });
  return results;
}
