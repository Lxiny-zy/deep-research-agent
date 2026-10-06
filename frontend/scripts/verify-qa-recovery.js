async (page) => {
  const root = 'D:/Cursor-edit/Project_test/deep-research-agent-upgrade/artifacts/reliability-ui/'
  const results = []
  await page.unroute('**/api/**')
  await page.setViewportSize({ width: 1440, height: 1100 })
  for (const surface of ['qa', 'reader']) {
    const posts = [], cancellations = []
    let release = () => {}
    let messages = [{ id: 'm1', position: 0, query: '这轮原始材料中的证据是什么？', answer: '', citations: [], evidence: [], thoughts: [], status: 'running', created_at: null, request_id: `original-${surface}`, request_payload: { query: '这轮原始材料中的证据是什么？', sources: ['web'], project_id: 'original-project' } }]
    const conversation = () => ({ id: 'c1', title: '单轮停止与继续验收', created_at: null, updated_at: null, run_id: surface === 'reader' ? 'r1' : null, message_count: messages.length, messages })
    await page.route('http://127.0.0.1:5182/api/**', async (route) => {
      const request = route.request(), path = request.url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
      const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      if (path.endsWith('/cancel')) {
        cancellations.push(path)
        messages[0] = { ...messages[0], status: 'cancelled', recovery: { available: true, stage: 'evidence' } }
        await json(messages[0]); release(); return
      }
      if (path.endsWith('/messages/stream')) {
        const body = request.postDataJSON(); posts.push(body)
        if (!body.resume_message_id) {
          await new Promise((resolve) => { release = resolve })
          try { await route.fulfill({ status: 200, contentType: 'text/event-stream', body: `event: complete\ndata: ${JSON.stringify(messages[0])}\n\n` }) } catch { /* The client disconnects only after durable cancellation. */ }
          return
        }
        const result = { ...messages[0], id: 'm2', position: 1, status: 'done', request_id: body.request_id, answer: '已从保存的证据阶段完成回答。', recovery: undefined, request_payload: body }
        messages = [...messages, result]
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: `event: complete\ndata: ${JSON.stringify(result)}\n\n` })
      }
      if (path === '/api/qa/conversations/c1') return json(conversation())
      if (path === '/api/qa/conversations') return json([conversation()])
      if (path.includes('/requests/')) return json(messages[0])
      if (path.endsWith('/reader')) return json({ run_id: 'r1', status: 'done', documents: [], has_report: false, can_ask: true, query: '本地材料验收' })
      if (path === '/api/runs/r1') return json({ id: 'r1', query: '本地材料验收', status: 'done', results: [], report: null })
      return json([])
    })
    await page.goto(`http://127.0.0.1:5182/preview/${surface}/${surface === 'qa' ? 'c1' : 'r1'}`)
    await page.locator('.qa-turn.is-pending').waitFor()
    await page.getByRole('button', { name: '停止本轮', exact: true }).click()
    await page.getByText('本轮已停止，历史与已保存阶段仍保留。', { exact: true }).waitFor()
    if (posts.length !== 1 || cancellations.length !== 1) throw new Error(`${surface}: cancellation replayed work`)
    await page.screenshot({ path: `${root}${surface}-stopped.png`, fullPage: true })
    await page.getByRole('button', { name: '继续未完成的回答' }).click()
    await page.getByText('已从保存的证据阶段完成回答。', { exact: true }).waitFor()
    await page.getByText('已继续，请查看后续回答。', { exact: true }).waitFor()
    if (await page.getByRole('button', { name: '继续未完成的回答' }).count()) throw new Error(`${surface}: completed continuation left a duplicate recovery action`)
    if (posts[1].request_id === posts[0].request_id || posts[1].resume_message_id !== 'm1' || posts[1].sources.join(',') !== 'web' || posts[1].project_id !== 'original-project') throw new Error(`${surface}: recovery changed the original scope or reused identity`)
    await page.screenshot({ path: `${root}${surface}-resumed.png`, fullPage: true })
    await page.reload()
    await page.getByText('已从保存的证据阶段完成回答。', { exact: true }).waitFor()
    if (posts.length !== 2) throw new Error(`${surface}: terminal refresh repeated work`)
    results.push({ surface, requests: posts.length, cancelledRequest: posts[0].request_id, resumedRequest: posts[1].request_id, originalScope: true, historyPreserved: messages.length === 2, terminalRefreshPassive: true })
    await page.unroute('http://127.0.0.1:5182/api/**')
  }
  return results
}
