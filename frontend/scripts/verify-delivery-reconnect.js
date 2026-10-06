async (page) => {
  const root = 'D:/Cursor-edit/Project_test/deep-research-agent-upgrade/artifacts/reliability-ui/'
  const posts = []
  let loseResponse = true, conflict = false, statusReads = 0
  const fileText = '# Fixed snapshot\n\nVersion: ' + 'a'.repeat(64)
  const fileHash = await page.evaluate(async (text) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))), (n) => n.toString(16).padStart(2, '0')).join(''), fileText)
  const receipt = (requestId, status) => ({ id: 'durable-export', operation_id: 'durable-export', run_id: 'scientific-fixture', kind: 'export', request_id: requestId, status, content_version: 'a'.repeat(64), status_url: '/api/runs/scientific-fixture/render-operations/durable-export' })
  await page.route('**/api/runs/scientific-fixture/render-operations**', async (route) => {
    const request = route.request()
    if (request.method() === 'POST') {
      const body = request.postDataJSON()
      posts.push(body)
      if (conflict) return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'document_version_changed', message: '报告内容版本已变化，请刷新预览。' } }) })
      if (loseResponse) { loseResponse = false; return route.abort('connectionfailed') }
      return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(receipt(body.request_id, 'running')) })
    }
    if (request.url().includes('/result')) return route.fulfill({ status: 200, contentType: 'text/markdown', headers: { 'Content-Disposition': 'attachment; filename="fixed-version.md"', 'X-Content-Version': 'a'.repeat(64), 'X-Content-SHA256': fileHash }, body: fileText })
    statusReads++
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(receipt(posts[0].request_id, 'done')) })
  })
  await page.goto('http://127.0.0.1:5182/preview/scientific-document')
  await page.evaluate(() => { for (const key of Object.keys(localStorage)) if (key.startsWith('dr_render:')) localStorage.removeItem(key) })
  await page.getByText('导出', { exact: true }).click()
  await page.getByRole('button', { name: '下载 .md', exact: true }).click()
  await page.getByRole('alert').waitFor()
  await page.screenshot({ path: `${root}delivery-lost-response.png`, fullPage: true })
  const downloaded = page.waitForEvent('download')
  await page.reload()
  const result = await downloaded
  await result.saveAs(`${root}fixed-version.md`)
  if (posts.length !== 2 || posts[0].request_id !== posts[1].request_id) throw new Error('Replay created a different operation identity')
  if (posts.some((request) => request.version !== 'a'.repeat(64))) throw new Error('Preview version missing from export')
  if (statusReads < 1) throw new Error('Receipt status was not verified')
  conflict = true
  await page.getByText('导出', { exact: true }).click()
  await page.getByRole('button', { name: '下载 .md', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: '报告内容版本已变化' }).waitFor()
  await page.screenshot({ path: `${root}delivery-version-conflict.png`, fullPage: true })
  const offline = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载离线正文副本' }).click()
  await (await offline).saveAs(`${root}offline-copy.md`)
  await page.unroute('**/api/runs/scientific-fixture/render-operations**')
  return { originalRequest: posts[0].request_id, replayRequest: posts[1].request_id, statusReads, pinnedVersion: posts[0].version, conflictVisible: true, offlineFile: 'offline-copy.md' }
}
