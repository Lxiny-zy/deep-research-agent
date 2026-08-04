import type { RunDetail } from '../types'
import {
  MAX_THREAD_TURNS,
  appendTurn,
  clearThread,
  loadThread,
  turnFromRun,
} from './conversation'

// 追问上下文由客户端保管，因此它的**健壮性**就是多轮功能的下限：
// 存储不可用、内容被手工改坏、历史无限增长，任何一项处理不好都会让
// 一次普通提问直接失败——而追问只是增强功能，不该有这种权力。

const EMPTY_SLOTS = { entities: [], time_range: '', domain: '', language: '', aspects: [] }

function makeDetail(over: Partial<RunDetail> = {}): RunDetail {
  return {
    id: 'run-1',
    query: '对比 Milvus 和 Qdrant',
    status: 'done',
    created_at: null,
    total_tokens: 0,
    elapsed: 0,
    tags: [],
    interpretation: '',
    sub_questions: [],
    results: [],
    report: null,
    orchestration: null,
    sources: [],
    events: [],
    manifest: null,
    metrics: null,
    intent: null,
    ...over,
  } as RunDetail
}

beforeEach(() => {
  sessionStorage.clear()
})

describe('conversation thread storage', () => {
  it('starts empty and round-trips an appended turn', () => {
    expect(loadThread()).toEqual([])

    appendTurn({ query: '对比 Milvus 和 Qdrant', intent: 'comparative', slots: EMPTY_SLOTS })

    expect(loadThread()).toEqual([
      { query: '对比 Milvus 和 Qdrant', intent: 'comparative', slots: EMPTY_SLOTS },
    ])
  })

  it('keeps only the most recent turns', () => {
    // 与后端 CreateRunRequest.history 的 max_length 对齐。若不截断，
    // 一条长对话会让创建请求直接 422——用报错告诉用户「你问得太多轮了」是荒唐的。
    for (let i = 0; i < MAX_THREAD_TURNS + 3; i += 1) {
      appendTurn({ query: `第${i}轮`, intent: 'unknown', slots: EMPTY_SLOTS })
    }

    const thread = loadThread()
    expect(thread).toHaveLength(MAX_THREAD_TURNS)
    // 保留最近的而非最早的：指代消解依赖的是最近的话题焦点。
    expect(thread[0].query).toBe('第3轮')
    expect(thread[thread.length - 1].query).toBe(`第${MAX_THREAD_TURNS + 2}轮`)
  })

  it('discards corrupt entries instead of throwing', () => {
    sessionStorage.setItem(
      'dr_conversation',
      JSON.stringify([{ query: '正常一轮' }, { intent: 'comparative' }, null, 42]),
    )

    // 缺 query 的、非对象的都该被丢掉，剩下的仍要能用——手工改坏 storage
    // 不该让整个提问页崩掉。
    expect(loadThread()).toEqual([{ query: '正常一轮', intent: 'unknown', slots: EMPTY_SLOTS }])
  })

  it('treats unparsable storage as no context', () => {
    sessionStorage.setItem('dr_conversation', '{ not json')
    expect(loadThread()).toEqual([])
  })

  it('clears the thread', () => {
    appendTurn({ query: 'a', intent: 'unknown', slots: EMPTY_SLOTS })
    clearThread()
    expect(loadThread()).toEqual([])
  })

  it('survives unavailable storage', () => {
    // 隐私模式下 sessionStorage 会抛。追问是增强功能，存不下只影响下一轮的
    // 消解质量，绝不能让本次提交失败。
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })

    expect(() => appendTurn({ query: 'a', intent: 'unknown', slots: EMPTY_SLOTS })).not.toThrow()
    expect(loadThread()).toEqual([])

    setItem.mockRestore()
    getItem.mockRestore()
  })
})

describe('turnFromRun', () => {
  it('carries the intent and slots so the resolver knows the topic focus', () => {
    const turn = turnFromRun(
      makeDetail({
        intent: {
          intent: 'comparative',
          confidence: 0.9,
          tier: 'rule',
          risk: 'none',
          risk_confidence: 0,
          signals: [],
          escalated: false,
          scores: {},
          reason: '',
          slots: { ...EMPTY_SLOTS, entities: ['Milvus', 'Qdrant'] },
          context_resolved: false,
          resolved_query: '',
          clarification: null,
        },
      }),
    )

    expect(turn).toEqual({
      query: '对比 Milvus 和 Qdrant',
      intent: 'comparative',
      slots: { ...EMPTY_SLOTS, entities: ['Milvus', 'Qdrant'] },
    })
  })

  it('stores the resolved question rather than the raw follow-up', () => {
    // 上一轮本身若是追问（「那第二个呢」），把原文存进历史只会让下一轮的
    // 消解器面对一串互相指代的残句。存补全后的那句，历史才是自足的。
    const turn = turnFromRun(
      makeDetail({
        query: '那第二个呢',
        intent: {
          intent: 'comparative',
          confidence: 0.9,
          tier: 'rule',
          risk: 'none',
          risk_confidence: 0,
          signals: [],
          escalated: false,
          scores: {},
          reason: '',
          slots: EMPTY_SLOTS,
          context_resolved: true,
          resolved_query: 'Qdrant 在 RAG 场景的表现如何',
          clarification: null,
        },
      }),
    )

    expect(turn?.query).toBe('Qdrant 在 RAG 场景的表现如何')
  })

  it('falls back to the raw query when there is no intent decision', () => {
    // 意图识别关闭时仍要能追问：历史里少了意图与槽位，消解器只是少一点提示，
    // 而不是完全没有上下文。
    expect(turnFromRun(makeDetail({ intent: null }))).toEqual({
      query: '对比 Milvus 和 Qdrant',
      intent: 'unknown',
      slots: EMPTY_SLOTS,
    })
  })

  it('returns null for a run with no usable question', () => {
    expect(turnFromRun(makeDetail({ query: '   ', intent: null }))).toBeNull()
  })
})
