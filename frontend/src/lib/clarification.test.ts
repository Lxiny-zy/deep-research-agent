import { MAX_CLARIFY_ROUNDS, advance, applyAnswer, emptySlots, isSkip } from './clarification'

// 这一层是纯函数：轮次怎么走、答案落到哪个槽位。它与后端 readiness.merge_answer
// 是一对**必须同步**的定义，两边算错会让 gap 消不掉、循环空转。

describe('applyAnswer', () => {
  it('把对比对象拆成多个实体', () => {
    // 用户不会规规矩矩一次填一个，「Kafka 和 RabbitMQ」必须拆开——
    // 下游要逐个实体去检索，粘成一个字符串就拆不出计划。
    const slots = applyAnswer(emptySlots(), 'entities', 'Kafka 和 RabbitMQ')
    expect(slots.entities).toEqual(['Kafka', 'RabbitMQ'])
  })

  it.each(['Milvus、Qdrant', 'Milvus, Qdrant', 'Milvus vs Qdrant', 'Milvus/Qdrant'])(
    '识别常见的并列写法：%s',
    (input) => {
      expect(applyAnswer(emptySlots(), 'entities', input).entities).toEqual(['Milvus', 'Qdrant'])
    },
  )

  it('方向类回答落到关注侧面', () => {
    const slots = applyAnswer(emptySlots(), 'direction', '某个领域的现状调研')
    expect(slots.aspects).toEqual(['某个领域的现状调研'])
    expect(slots.entities).toEqual([])
  })

  it('累积而不是覆盖', () => {
    // 多轮追问的答案要叠加：第二轮答的不能把第一轮的冲掉。
    const first = applyAnswer(emptySlots(), 'entities', 'Kafka')
    const second = applyAnswer(first, 'entities', 'RabbitMQ')
    expect(second.entities).toEqual(['Kafka', 'RabbitMQ'])
  })

  it('跳过与空回答不改变任何槽位', () => {
    const base = applyAnswer(emptySlots(), 'entities', 'Kafka')
    expect(applyAnswer(base, 'entities', '直接研究')).toEqual(base)
    expect(applyAnswer(base, 'entities', '   ')).toEqual(base)
  })

  it('不修改传入的对象', () => {
    // React state 必须是不可变的，就地改会让组件不重渲染。
    const base = emptySlots()
    applyAnswer(base, 'entities', 'Kafka')
    expect(base.entities).toEqual([])
  })

  it('实体数量有上限，防止无限累积', () => {
    let slots = emptySlots()
    for (let i = 0; i < 12; i += 1) slots = applyAnswer(slots, 'entities', `E${i}`)
    expect(slots.entities).toHaveLength(8)
  })
})

describe('advance', () => {
  const state = {
    query: '对比一下',
    round: 0,
    answers: emptySlots(),
    question: '想对比哪几个对象？',
    options: [],
    gap: 'entities',
  }

  it('轮次自增且答案累积', () => {
    const next = advance(state, 'Kafka 和 RabbitMQ')
    expect(next.round).toBe(1)
    expect(next.answers.entities).toEqual(['Kafka', 'RabbitMQ'])
    // 原始提问在整个循环里不变——服务端每轮都基于它重新合成。
    expect(next.query).toBe('对比一下')
  })

  it('不修改原状态', () => {
    advance(state, 'Kafka')
    expect(state.round).toBe(0)
    expect(state.answers.entities).toEqual([])
  })
})

describe('isSkip', () => {
  it('识别跳过选项', () => {
    expect(isSkip('直接研究')).toBe(true)
    expect(isSkip('  直接研究  ')).toBe(true)
    expect(isSkip('某项技术的原理与机制')).toBe(false)
  })
})

it('轮次上限与后端保持一致', () => {
  // 后端 readiness.MAX_CLARIFY_ROUNDS 到顶会强制放行。两边不一致的话，
  // UI 上显示的「第 N / M 轮」会与实际行为对不上。
  expect(MAX_CLARIFY_ROUNDS).toBe(3)
})
