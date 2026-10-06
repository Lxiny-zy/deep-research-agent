import { test, expect, templateKeys, templateNames, assertFullyInViewport } from './fixtures'

for (const [index, key] of templateKeys.entries()) {
  test(`controlled ${key} input/upload/configuration reaches its submission contract`, async ({
    page,
    api,
  }) => {
    await page.goto('/')
    await page.locator('.task-card').filter({ hasText: templateNames[index] }).click()
    await page.locator('#query').fill(`受控 ${templateNames[index]}：分析方法、数据与适用条件。`)
    if (key === 'dataAnalysis') {
      await page.locator('#dataset-file').setInputFiles({
        name: 'metrics.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from('method,psnr\nA,30\nB,32\n'),
      })
      await expect(page.locator('.home-dataset-file')).toContainText('metrics.csv')
      await expect(page.locator('.home-dataset-file')).toContainText('2 行')
    } else {
      await page.getByLabel('上传附件').setInputFiles({
        name: 'notes.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from('Controlled source: CASSI experiments use two conditions.'),
      })
      await expect(page.locator('.attach-item.is-ready')).toContainText('notes.txt')
    }
    await page.locator('#research-project').selectOption('controlled-project')
    await page.locator('.home-actionbar .btn-lg').click()
    await expect(page.locator('.home-actionbar-error')).toBeVisible()
    await assertFullyInViewport(page, '.home-actionbar-error')
    const submissions = api.posts.filter((item) => item.path === '/api/runs')
    expect(submissions).toHaveLength(1)
    expect(submissions[0].body).toMatchObject({
      template: key,
      project_id: 'controlled-project',
      demo_data: false,
    })
    if (key === 'dataAnalysis')
      expect(submissions[0].body.dataset).toBe('method,psnr\nA,30\nB,32\n')
    else
      expect(submissions[0].body.attachments).toEqual([
        expect.objectContaining({ filename: 'notes.txt' }),
      ])
    await expect(page.locator('#query')).toHaveValue(
      `受控 ${templateNames[index]}：分析方法、数据与适用条件。`,
    )
  })
}

test('a late task preview keeps the submission error visible without stealing later input focus', async ({
  page,
  api,
}) => {
  let release!: () => void
  api.contractGate = new Promise<void>((resolve) => {
    release = resolve
  })
  api.contractSections = Array.from({ length: 12 }, (_, i) => `需要回答的详细分析条件 ${i + 1}`)
  try {
    await page.goto('/')
    await page.locator('.task-card[data-template="dataAnalysis"]').click()
    await page.locator('#query').fill('比较两组数据的差异')
    await page.locator('.home-actionbar .btn-lg').click()
    await expect(page.locator('.home-actionbar-error')).toBeFocused()
    await expect
      .poll(() => api.posts.filter((item) => item.path === '/api/templates/contract').length)
      .toBeGreaterThan(0)
    release()
    await expect(page.locator('.contract-sections li')).toHaveCount(12)
    await assertFullyInViewport(page, '.home-actionbar-error')
    await page.locator('#query').fill('重新编辑分析目标')
    const contractsBefore = api.posts.filter(
      (item) => item.path === '/api/templates/contract',
    ).length
    await expect
      .poll(() => api.posts.filter((item) => item.path === '/api/templates/contract').length)
      .toBeGreaterThan(contractsBefore)
    await expect(page.locator('#query')).toBeFocused()
  } finally {
    release()
  }
})
