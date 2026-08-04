// 澄清循环的客户端状态：问到第几轮、已经答了什么。
//
// 与 lib/conversation.ts 同一个原则——**状态由客户端携带，服务端不存**。
// 但这里刻意**不落 sessionStorage**：澄清是一次提问内部的临时过程，
// 刷新页面就该重来（问题本身都没了，半截的答案没有意义）；
// 而多轮追问的上下文要跨页面存活，那是两种不同的生命周期。
import type { IntentSlots } from '../types'

/** 与后端 readiness.MAX_CLARIFY_ROUNDS 对齐。到顶服务端会强制放行。 */
export const MAX_CLARIFY_ROUNDS = 3

/** 与后端 readiness.SKIP_OPTION 对齐：用户随时可以跳过追问直接研究。 */
export const SKIP_OPTION = '直接研究'

export interface ClarifyState {
  /** 用户最初问的那句话，整个循环里不变 */
  query: string
  /** 已经问过几轮 */
  round: number
  /** 累积的答案。本质就是槽位值，因此直接用 IntentSlots */
  answers: IntentSlots
  /** 当前这一轮要问什么 */
  question: string
  options: string[]
  /** 缺哪一样，决定用户的回答落到哪个槽位 */
  gap: string
}

export function emptySlots(): IntentSlots {
  return { entities: [], time_range: '', domain: '', language: '', aspects: [] }
}

/** 把用户这一轮的回答落到对应槽位。
 *
 * 按 gap 分派而不是让用户选字段：问题本身就是按 gap 提的——问「对比哪几个」
 * 得到的当然是实体，问「什么方向」得到的是关注侧面。用户不需要理解槽位语义。
 *
 * 这段逻辑与后端 readiness.merge_answer 是一对**必须同步**的定义。分别实现是
 * 因为前端要立刻更新 UI（不能等一次往返才知道自己答了什么），而后端不能信任
 * 客户端算出来的槽位。两边算错的后果是槽位落错字段，会被 assess 的下一轮
 * 判定发现（gap 没消掉）。
 */
export function applyAnswer(answers: IntentSlots, gap: string, raw: string): IntentSlots {
  const answer = raw.trim()
  if (!answer || answer === SKIP_OPTION) return answers
  if (gap === 'entities' || gap === 'topic') {
    // 用户可能一次填多个：「Kafka 和 RabbitMQ」「Milvus、Qdrant」
    const parts = answer
      .split(/[、,，和与/]| vs /)
      .map((part) => part.trim())
      .filter(Boolean)
    return { ...answers, entities: [...answers.entities, ...parts].slice(0, 8) }
  }
  if (gap === 'direction') {
    return { ...answers, aspects: [...answers.aspects, answer].slice(0, 6) }
  }
  return answers
}

/** 用户答完一轮之后的新状态。轮次自增，答案累积。 */
export function advance(state: ClarifyState, answer: string): ClarifyState {
  return {
    ...state,
    round: state.round + 1,
    answers: applyAnswer(state.answers, state.gap, answer),
  }
}

/** 用户是否选择了跳过澄清。 */
export function isSkip(answer: string): boolean {
  return answer.trim() === SKIP_OPTION
}
