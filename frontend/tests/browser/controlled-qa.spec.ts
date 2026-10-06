import { test, expect, answer, assertNoPageOverflow } from './fixtures'

for (const width of [1440, 390]) {
  for (const route of ['/qa/controlled-conversation', '/runs/controlled-run/read']) {
    test(`controlled answer avatars survive refresh ${route} ${width}`, async ({
      page,
      api,
    }, testInfo) => {
      api.messages = [{ ...answer }]
      await page.setViewportSize({ width, height: 900 })
      await page.goto(route)
      const message = page.getByRole('article', { name: '第 1 轮问答' })
      await expect(message).toContainText('CASSI 是编码孔径快照光谱成像')
      const avatars = message.locator('.qa-avatar')
      await expect(avatars).toHaveCount(1)
      for (const avatar of await avatars.all()) {
        await expect(avatar).toBeVisible()
        const size = await avatar.boundingBox()
        expect(size?.width).toBeGreaterThan(16)
        expect(size?.height).toBeGreaterThan(16)
        const ring = avatar.locator('.brand-ring')
        await expect(ring).toBeVisible()
        expect(
          await ring.evaluate((element) => getComputedStyle(element).backgroundImage),
        ).not.toBe('none')
      }
      await assertNoPageOverflow(page)
      await page.reload()
      await expect(message).toContainText('CASSI 是什么')
      await expect(avatars).toHaveCount(1)
      await page.screenshot({ path: testInfo.outputPath('conversation.png'), fullPage: true })
    })
  }
}

test('controlled durable cancellation survives refresh and exposes explicit continuation', async ({
  page,
  api,
}) => {
  api.messages = [
    {
      ...answer,
      answer: '',
      status: 'running',
      request_payload: { query: answer.query, sources: ['web'] },
    },
  ]
  await page.goto('/qa/controlled-conversation')
  await page.getByRole('button', { name: /停止本轮/ }).click()
  await expect(page.getByRole('article', { name: '第 1 轮问答' })).toContainText('已停止')
  await page.reload()
  await expect(page.getByRole('article', { name: '第 1 轮问答' })).toContainText('CASSI 是什么')
  await expect(page.getByRole('button', { name: '继续未完成的回答' })).toBeEnabled()
  expect(api.posts.filter((item) => item.path.endsWith('/cancel'))).toHaveLength(1)
  await page.getByRole('button', { name: '继续未完成的回答' }).click()
  await expect(page.getByText('受控流式片段，尚未完成。', { exact: true })).toBeVisible()
  const continued = api.posts.filter((item) => item.path.endsWith('/messages/stream')).at(-1)!
  expect(continued.body).toMatchObject({
    query: answer.query,
    resume_message_id: answer.id,
    sources: ['web'],
  })
  expect(continued.body.request_id).not.toBe(answer.request_id)
  api.finishStream(String(continued.body.request_id), '显式继续后完成的受控回答。')
  await expect(page.getByRole('article', { name: '第 2 轮问答' })).toContainText('显式继续后完成')
  await expect(page.getByRole('article', { name: '第 1 轮问答' })).toContainText('本轮已停止')
  // This checks durable UI reconciliation. Server fencing is covered by API tests,
  // not proven by a controlled response.
})

test('controlled real HTTP stream renders a partial answer before completion', async ({
  page,
  api,
}) => {
  const first = {
    call_id: 'synthetic-attempt-1',
    operation: 'structured',
    role: 'synthesizer',
    model: 'controlled-model',
    attempt: 1,
    usage_state: 'unknown',
    usage: null,
  }
  const retry = {
    ...first,
    call_id: 'synthetic-attempt-2',
    attempt: 2,
    retry_reason: 'transport_retry',
  }
  api.streamModelCalls = [
    { ...first, status: 'started' },
    { ...first, status: 'failed' },
    { ...retry, status: 'started' },
    { ...retry, status: 'succeeded' },
    // Replayed starts must not reset terminal state or increase request counts.
    { ...first, status: 'started' },
    { ...retry, status: 'succeeded' },
  ]
  await page.goto('/qa/controlled-conversation')
  await page.locator('.qa-composer textarea').fill('进一步说明适用条件')
  await page.locator('.qa-composer textarea').press('Enter')
  await expect(page.getByText('受控流式片段，尚未完成。', { exact: true })).toBeVisible()
  expect(api.streams.size).toBe(1)
  const liveCalls = page.locator('.model-call-summary')
  await expect(liveCalls).toContainText('2 次请求 · 1 次重试')
  await liveCalls.locator('summary').click()
  await expect(liveCalls.locator('li')).toHaveCount(2)
  await expect(liveCalls).toContainText('请求失败')
  await expect(liveCalls).toContainText('已结束')
  await expect(liveCalls).toContainText('用量未返回')
  await expect(liveCalls).not.toContainText('0 token')
  const requestId = [...api.streams.keys()][0]
  api.finishStream(requestId, '完成回答：适用条件需要结合具体实验设置。')
  await expect(page.getByRole('article', { name: '第 2 轮问答' })).toContainText(
    '完成回答：适用条件',
  )
  await page.reload()
  await expect(page.getByRole('article', { name: '第 2 轮问答' })).toContainText(
    '完成回答：适用条件',
  )
  const savedCalls = page
    .getByRole('article', { name: '第 2 轮问答' })
    .locator('.model-call-summary')
  await expect(savedCalls).toContainText('2 次请求 · 1 次重试')
  await savedCalls.locator('summary').click()
  await expect(savedCalls.locator('li')).toHaveCount(2)
  await expect(savedCalls).toContainText('用量未返回')
  await expect(savedCalls).not.toContainText('0 token')
})
