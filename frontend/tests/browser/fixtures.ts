import { test as base, expect, type Page } from '@playwright/test'
import { createServer, type Server, type ServerResponse } from 'node:http'
import type { TaskTemplate } from '../../src/types'

export type Role = 'admin' | 'researcher' | 'reader'
export const templateNames = [
  '课题调研',
  '文献综述',
  '同行评审',
  '论文精读',
  '数据分析',
  '幻灯片',
  '思维导图',
]
export const templateKeys = [
  'autoResearch',
  'litReview',
  'peerReview',
  'paperRead',
  'dataAnalysis',
  'slides',
  'mindmap',
] as const
export const templates = templateKeys.map(
  (key, i): TaskTemplate => ({
    key,
    title: templateNames[i],
    tagline: '检索材料、核验证据并生成研究产物',
    description: '',
    icon: ['network', 'library', 'shield', 'book', 'chart', 'presentation', 'mindmap'][i],
    workflow: key === 'autoResearch' ? 'deep' : key,
    input_kind:
      key === 'dataAnalysis'
        ? 'dataset'
        : ['paperRead', 'peerReview'].includes(key)
          ? 'paper'
          : 'topic',
    input_label: key === 'dataAnalysis' ? '分析目标' : '研究问题',
    input_placeholder: '输入研究问题',
    sections: [],
    deliverables: ['md', 'docx', 'pdf', 'html'],
    examples: [],
    accepts_attachments: true,
    tier_default: 'deep',
    min_citations: 1,
    tags: [],
    supports_library: true,
    default_strategy: 'deep',
    strategies:
      key === 'autoResearch'
        ? [
            {
              key: 'deep',
              label: '深度检索',
              description: '多子问题并行检索、逐字核验证据、反思补洞',
              workflow: 'deep',
            },
            { key: 'quick', label: '快速检索', description: '检索一轮', workflow: 'quick' },
          ]
        : [],
  }),
)

export const answer = {
  id: 'message-1',
  request_id: 'request-1',
  position: 0,
  query: 'CASSI 是什么？',
  answer: 'CASSI 是编码孔径快照光谱成像 [1]。',
  citations: ['https://example.invalid/paper'],
  evidence: [
    {
      statement: 'CASSI 是编码孔径快照光谱成像',
      source_url: 'https://example.invalid/paper',
      evidence_quote: 'CASSI is coded aperture snapshot spectral imaging.',
      source_reference: '受控论文第 1 页',
    },
  ],
  thoughts: [{ tool: 'search_and_verify', input: 'CASSI', observation: '保留 1 条受控证据' }],
  status: 'done',
  created_at: null,
}

export class ControlledApi {
  taskTemplates = templates
  contractGate?: Promise<void>
  contractSections: string[] = []
  role: Role | null = 'admin'
  faults: string[] = []
  posts: { path: string; body: Record<string, unknown> }[] = []
  submissionError = '受控故障：服务暂时不可用，请稍后重试。'
  messages: Record<string, unknown>[] = [{ ...answer }]
  conversationTitle = '受控 CASSI 会话'
  privateProject = '受控研究资料库'
  server?: Server
  streams = new Map<string, ServerResponse>()
  streamModelCalls: Record<string, unknown>[] = []

  finishStream(requestId: string, text = '已完成受控的流式回答。') {
    const message = this.messages.find((item) => item.request_id === requestId)
    if (!message) throw new Error('No matching controlled stream request')
    Object.assign(message, {
      answer: text,
      status: 'done',
      thoughts: this.streamModelCalls.map((call) => ({ tool: 'model_call', call })),
    })
    this.streams.get(requestId)?.end(`event: complete\ndata: ${JSON.stringify(message)}\n\n`)
    this.streams.delete(requestId)
  }

  async close() {
    for (const response of this.streams.values()) response.destroy()
    this.server?.closeAllConnections()
    await new Promise<void>((resolve) =>
      this.server ? this.server.close(() => resolve()) : resolve(),
    )
  }

  async install(page: Page) {
    const origin = new URL(baseURL(page)).origin
    this.server = createServer(async (request, response) => {
      const chunks: Buffer[] = []
      for await (const chunk of request) chunks.push(Buffer.from(chunk))
      const body = JSON.parse(Buffer.concat(chunks).toString())
      let message = this.messages.find((item) => item.request_id === body.request_id)
      if (!message) {
        message = {
          ...answer,
          id: `message-${this.messages.length + 1}`,
          request_id: body.request_id,
          position: this.messages.length,
          query: body.query,
          answer: '',
          status: 'running',
          request_payload: body,
        }
        this.messages.push(message)
      }
      response.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Access-Control-Allow-Origin': origin,
        'Access-Control-Allow-Headers': 'content-type, authorization',
      })
      response.flushHeaders()
      for (const call of this.streamModelCalls)
        response.write(`event: model_call\ndata: ${JSON.stringify({ model_call: call })}\n\n`)
      response.write(
        `event: status\ndata: ${JSON.stringify({ phase: 'generating', message: '受控流式生成' })}\n\n`,
      )
      response.write(
        `event: delta\ndata: ${JSON.stringify({ delta: '受控流式片段，尚未完成。' })}\n\n`,
      )
      this.streams.set(body.request_id, response)
    })
    await new Promise<void>((resolve) => this.server!.listen(0, '127.0.0.1', resolve))
    const address = this.server.address()
    if (!address || typeof address === 'string')
      throw new Error('Controlled stream listener did not start')
    await page.addInitScript(() => {
      localStorage.setItem('dr_welcome_tour_seen', '1')
      sessionStorage.setItem('sr_intro_seen', '1')
      localStorage.setItem('sr_sidebar_collapsed', '1')
    })
    await page.route('**/*', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      if (!['http:', 'https:'].includes(url.protocol)) return route.continue()
      if (url.origin !== origin) {
        this.faults.push(`Unexpected external request: ${url.origin}${url.pathname}`)
        return route.abort('blockedbyclient')
      }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      const path = url.pathname
      const method = request.method()
      const body =
        method === 'GET'
          ? {}
          : request.headers()['content-type']?.includes('application/json')
            ? (request.postDataJSON() ?? {})
            : {}
      if (method !== 'GET') this.posts.push({ path, body })
      const reply = (json: unknown, status = 200) => route.fulfill({ status, json })
      if (path === '/api/config') {
        const token = request.headers().authorization?.replace('Bearer ', '')
        const loginRole = token?.replace('controlled-', '')
        const role = token
          ? ['admin', 'researcher', 'reader'].includes(loginRole || '')
            ? loginRole
            : null
          : this.role
        return role
          ? reply({ access: { role }, require_corroboration: false })
          : reply({ detail: '访问密钥无效' }, 401)
      }
      if (path === '/api/templates') return reply(this.taskTemplates)
      if (path === '/api/tiers')
        return reply(
          ['light', 'standard', 'deep'].map((key, i) => ({
            key,
            title: ['轻量', '标准', '深度'][i],
            description: '受控研究档位',
            max_sub_questions: [3, 5, 8][i],
            max_rounds: [0, 1, 2][i],
            results_per_search: 5,
            max_tokens: 200000,
          })),
        )
      if (path === '/api/usage')
        return reply({
          period: 'day',
          resets_at: '',
          runs: { used: 0, limit: null },
          tokens: { used: 0, limit: null },
          exhausted: false,
        })
      if (path === '/api/workflows')
        return reply([
          {
            name: 'deep',
            description: '深度检索流程',
            default: 'True',
            custom: 'False',
            nodes: [],
            edges: [],
          },
        ])
      if (path === '/api/projects')
        return reply([
          { id: 'controlled-project', name: this.privateProject, included_source_count: 3 },
        ])
      if (path === '/api/resource-preflight')
        return reply({
          ok: false,
          errors: ['受控预检：尚未配置检索服务。'],
          roles: [],
          warnings: [],
        })
      if (path === '/api/attachments/file') {
        const filename = url.searchParams.get('filename') || 'notes.txt'
        const content = request.postDataBuffer() || Buffer.alloc(0)
        this.posts[this.posts.length - 1].body = { filename, content: content.toString() }
        return reply({
          attachment: { id: 'a'.repeat(24), filename, text: content.toString(), truncated: false },
          summary: {
            id: 'a'.repeat(24),
            filename,
            kind: 'text',
            mime_type: 'text/plain',
            size: content.length,
            char_count: content.length,
            chunk_count: 1,
            truncated: false,
            preview: content.toString(),
            sections: [],
          },
        })
      }
      if (path === '/api/datasets')
        return reply({
          filename: body.filename,
          file_sha256: 'f'.repeat(64),
          skipped: [],
          sheets: [
            {
              name: '',
              rows: 2,
              columns: [
                { name: 'method', type: '文本' },
                { name: 'psnr', type: '数值' },
              ],
              csv: 'method,psnr\nA,30\nB,32\n',
              chars: 26,
              input_sha256: 'c'.repeat(64),
            },
          ],
        })
      if (path === '/api/runs' && method === 'GET') return reply([])
      if (path === '/api/tags' && method === 'GET') return reply([])
      if (path === '/api/intent/assess')
        return reply({
          ready: true,
          resolved_query: body.query,
          question: '',
          options: [],
          gap: 'none',
          blocked: false,
          intent: 'exploratory',
          reason: '',
        })
      if (path === '/api/runs' && method === 'POST')
        return reply({ detail: this.submissionError }, 503)
      if (path === '/api/templates/contract') {
        await this.contractGate
        return reply({
          template: body.template,
          title: '受控任务确认',
          original_request: body.query,
          focus: '',
          papers: [],
          dataset_csv: '',
          required_sections: this.contractSections,
          deliverables: ['md'],
          constraints: [],
          evidence_rules: [],
          tier: 'deep',
        })
      }
      if (path === '/api/qa/conversations' && method === 'GET')
        return reply([{ ...this.conversation(), messages: [] }])
      if (path === '/api/qa/conversations/controlled-conversation/messages/stream') {
        return route.continue({ url: `http://127.0.0.1:${address.port}${path}` })
      }
      if (path === '/api/qa/conversations/controlled-conversation' && method === 'GET')
        return reply(this.conversation())
      if (
        path === '/api/qa/conversations/controlled-conversation/requests/request-1' &&
        method === 'GET'
      )
        return reply(this.messages[0])
      if (path === '/api/qa/conversations/controlled-conversation/requests/request-1/cancel') {
        this.messages[0] = {
          ...this.messages[0],
          status: 'cancelled',
          recovery: { available: true, stage: 'draft' },
        }
        return reply(this.messages[0])
      }
      if (path === '/api/runs/controlled-run/reader')
        return reply({
          run_id: 'controlled-run',
          status: 'done',
          has_report: false,
          can_ask: true,
          documents: [
            {
              id: 'paper-0',
              kind: 'paper',
              title: '受控论文',
              url: 'https://example.invalid/paper',
              pdf: false,
              note: '受控论文未提供 PDF',
            },
          ],
        })
      if (path === '/api/runs/controlled-run')
        return reply({
          id: 'controlled-run',
          query: '精读受控论文',
          status: 'done',
          results: [],
          report: null,
        })
      this.faults.push(`Unhandled controlled API: ${method} ${path}`)
      return reply({ detail: 'Unhandled browser fixture endpoint' }, 501)
    })
  }

  conversation() {
    return {
      id: 'controlled-conversation',
      title: this.conversationTitle,
      created_at: null,
      updated_at: null,
      run_id: 'controlled-run',
      message_count: this.messages.length,
      messages: this.messages,
    }
  }
}

function baseURL(page: Page) {
  return page.url() === 'about:blank'
    ? process.env.DR_BROWSER_BASE_URL || 'http://127.0.0.1:5199'
    : page.url()
}

export const test = base.extend<{ api: ControlledApi }>({
  api: async ({ page }, provide) => {
    const api = new ControlledApi()
    const pageErrors: string[] = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await api.install(page)
    try {
      await provide(api)
      expect(api.faults, 'Every controlled request must have an explicit fixture').toEqual([])
      expect(pageErrors, 'Uncaught browser JavaScript exceptions').toEqual([])
    } finally {
      await api.close()
    }
  },
})
export { expect }

export async function assertNoPageOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    width: innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }))
  expect(Math.max(dimensions.document, dimensions.body)).toBeLessThanOrEqual(dimensions.width + 1)
}

export async function assertHitTarget(page: Page, selector: string) {
  const target = page.locator(selector)
  await target.scrollIntoViewIfNeeded()
  await target.focus()
  await expect(target).toBeFocused()
  const hit = await target.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
    return Boolean(top && (element === top || element.contains(top)))
  })
  expect(hit, `Target ${selector} must remain clickable`).toBe(true)
}

export async function assertFullyInViewport(page: Page, selector: string) {
  await expect
    .poll(() =>
      page.locator(selector).evaluate((element) => {
        const rect = element.getBoundingClientRect()
        // CSS pixel rounding at scroll boundaries may leave a subpixel border outside.
        return (
          rect.top >= -1 &&
          rect.bottom <= innerHeight + 1 &&
          rect.left >= -1 &&
          rect.right <= innerWidth + 1
        )
      }),
    )
    .toBe(true)
}
