import type { ResearchEvent } from '../types'
import { reduceStream, type ResearchStreamState } from './useResearchStream'

const base: ResearchStreamState = {
  events: [],
  reportMarkdown: '',
  status: 'streaming',
  stats: null,
  dag: null,
  elapsed: 0,
  tokens: 0,
  tokensEstimated: false,
  findings: 0,
}

function ev(partial: Partial<ResearchEvent>): ResearchEvent {
  return { stage: 'ORCHESTRATOR', type: 'info', message: '', elapsed: 0, data: null, ...partial }
}

describe('reduceStream', () => {
  it('累加 token.delta 到报告，且 token 不进时间线', () => {
    let s = base
    s = reduceStream(s, ev({ stage: 'SYNTHESIZER', type: 'token', data: { delta: 'Hello ' } }))
    s = reduceStream(s, ev({ stage: 'SYNTHESIZER', type: 'token', data: { delta: 'world' } }))
    expect(s.reportMarkdown).toBe('Hello world')
    expect(s.events).toHaveLength(0)
  })

  it('report 事件用完整 markdown 覆盖流式残留', () => {
    const s = reduceStream(
      { ...base, reportMarkdown: '流式残留' },
      ev({ type: 'report', data: { query: 'q', markdown: '# 最终报告', citations: [] } }),
    )
    expect(s.reportMarkdown).toBe('# 最终报告')
  })

  it('done 事件收尾并记录统计', () => {
    const s = reduceStream(
      base,
      ev({ type: 'done', data: { elapsed: 1.2, total_tokens: 99, sources: 3 } }),
    )
    expect(s.status).toBe('done')
    expect(s.stats).toEqual({ elapsed: 1.2, total_tokens: 99, sources: 3 })
  })

  it('ignores trailing events after a terminal event', () => {
    const done = reduceStream(
      base,
      ev({ type: 'done', data: { elapsed: 1.2, total_tokens: 99, sources: 3 } }),
    )
    const stale = reduceStream(
      done,
      ev({ stage: 'SYNTHESIZER', type: 'token', data: { delta: 'stale' } }),
    )
    expect(stale).toBe(done)
    expect(stale.reportMarkdown).toBe('')
  })

  it('ORCHESTRATOR error 事件标记终态错误', () => {
    const s = reduceStream(base, ev({ stage: 'ORCHESTRATOR', type: 'error', message: '失败' }))
    expect(s.status).toBe('error')
    expect(s.events).toHaveLength(1)
  })

  it('RESEARCHER error 是非致命单点失败：进时间线但不终止流', () => {
    const s = reduceStream(base, ev({ stage: 'RESEARCHER', type: 'error', message: '检索失败' }))
    expect(s.status).toBe('streaming')
    expect(s.events).toHaveLength(1)
  })

  it('非 ORCHESTRATOR 的 done 不置终态', () => {
    const s = reduceStream(base, ev({ stage: 'SYNTHESIZER', type: 'done' }))
    expect(s.status).toBe('streaming')
  })

  it('从 ORCHESTRATOR info 提取 dag', () => {
    const s = reduceStream(
      base,
      ev({ type: 'info', data: { dag: { layers: [[0], [1]], deps: { '1': [0] } } } }),
    )
    expect(s.dag).toEqual({ layers: [[0], [1]], deps: { '1': [0] } })
  })

  it('start / finding 等普通事件进入时间线', () => {
    let s = reduceStream(base, ev({ stage: 'PLANNER', type: 'start', message: '开始拆解' }))
    s = reduceStream(s, ev({ stage: 'RESEARCHER', type: 'finding', data: { count: 3 } }))
    expect(s.events).toHaveLength(2)
  })

  it('直播统计：耗时取最大值、token 取携带值、发现数累加', () => {
    let s = reduceStream(
      base,
      ev({ stage: 'PLANNER', type: 'start', elapsed: 0.5, tokens: 10, tokens_estimated: true }),
    )
    expect(s.elapsed).toBe(0.5)
    expect(s.tokens).toBe(10)
    expect(s.tokensEstimated).toBe(true)
    s = reduceStream(
      s,
      ev({ stage: 'RESEARCHER', type: 'finding', elapsed: 1.2, tokens: 30, data: { count: 4 } }),
    )
    expect(s.elapsed).toBe(1.2)
    expect(s.tokens).toBe(30)
    expect(s.findings).toBe(4)
    // 乱序到达的旧 elapsed 不应回退；缺省 tokens 沿用旧值
    s = reduceStream(
      s,
      ev({ stage: 'RESEARCHER', type: 'finding', elapsed: 0.8, data: { count: 1 } }),
    )
    expect(s.elapsed).toBe(1.2)
    expect(s.tokens).toBe(30)
    expect(s.findings).toBe(5)
  })
})
