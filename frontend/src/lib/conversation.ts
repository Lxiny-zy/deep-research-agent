// 追问上下文：多轮意图消解所依赖的历史轮次，由**客户端**保管。
//
// 为什么不建服务端会话表：会话状态是整个系统里最容易变成隐式耦合的东西——
// 一旦服务端持有「当前对话」，每条请求的行为就取决于一份客户端看不见的状态，
// 出了问题（消解错对象、串号、并发覆盖）几乎无从排查。让客户端把历史随请求
// 显式传上来，请求就是自解释的：同样的 body 永远得到同样的判定，可复现、可回放。
//
// 为什么用 sessionStorage 而不是内存或 localStorage：
//   - 内存：刷新页面（或从运行详情深链回来）就丢，而追问恰恰发生在「看完报告
//     再问一句」的跨页流程里；
//   - localStorage：对话不该跨标签页与跨天存活，那会让用户下次打开时被一段
//     早已忘掉的上下文影响判定——这正是隐式状态最难排查的形态。
// sessionStorage 的生命周期（一个标签页会话）与「一段对话」天然对齐。
import type { ConversationTurn, RunDetail } from '../types'

const STORAGE_KEY = 'dr_conversation'

// 与后端 CreateRunRequest.history 的 max_length 对齐。超出必须在客户端截断：
// 让服务端返回 422 来告诉用户「你问得太多轮了」是荒唐的产品行为。
// 取最近 N 轮而非最早 N 轮——指代消解依赖的是最近的话题焦点。
export const MAX_THREAD_TURNS = 6

function readRaw(): unknown {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    // sessionStorage 不可用（隐私模式）或内容损坏：当作没有上下文。
    // 追问是增强功能，读不出来就退化成一次普通提问，不该报错阻断用户。
    return null
  }
}

/** 把任意来源的数据收敛成合法的 ConversationTurn，无法收敛则丢弃。 */
function coerceTurn(value: unknown): ConversationTurn | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const query = typeof raw.query === 'string' ? raw.query.trim() : ''
  if (!query) return null
  const slots = (raw.slots ?? {}) as Record<string, unknown>
  return {
    query: query.slice(0, 2000),
    intent: typeof raw.intent === 'string' ? raw.intent : 'unknown',
    slots: {
      entities: Array.isArray(slots.entities) ? (slots.entities as string[]).slice(0, 8) : [],
      time_range: typeof slots.time_range === 'string' ? slots.time_range : '',
      domain: typeof slots.domain === 'string' ? slots.domain : '',
      language: typeof slots.language === 'string' ? slots.language : '',
      aspects: Array.isArray(slots.aspects) ? (slots.aspects as string[]).slice(0, 6) : [],
    },
  }
}

/** 读取当前对话的历史轮次；损坏或不可用时返回空数组。 */
export function loadThread(): ConversationTurn[] {
  const raw = readRaw()
  if (!Array.isArray(raw)) return []
  return raw.map(coerceTurn).filter((turn): turn is ConversationTurn => turn !== null)
}

function writeThread(turns: ConversationTurn[]): ConversationTurn[] {
  const bounded = turns.slice(-MAX_THREAD_TURNS)
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(bounded))
  } catch {
    // 写不进去就只影响下一轮追问的质量，不影响本次提交——不抛。
  }
  return bounded
}

/** 追加一轮，返回截断后的完整线程。 */
export function appendTurn(turn: ConversationTurn): ConversationTurn[] {
  return writeThread([...loadThread(), turn])
}

export function clearThread(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // 同上：清不掉也不该抛。
  }
}

/** 把一次已完成的研究折叠成一轮对话历史。
 *
 * 带上意图与槽位而不只是原文：后端 `render_history` 会把它们一并交给消解器，
 * 让它知道上一轮的话题焦点是什么（「对比 Milvus 和 Qdrant｜研究对象：Milvus、Qdrant」）。
 * 只给原文的话，消解器得先自己把上一轮重读一遍才能判断「第二个」指谁。
 *
 * 用**消解后**的问题：上一轮若本身就是追问（「那第二个呢」），把它原样存进历史
 * 只会让下一轮的消解器面对一串互相指代的残句。存已经补全的那句，历史才是自足的。
 */
export function turnFromRun(detail: RunDetail): ConversationTurn | null {
  const intent = detail.intent
  const query = (intent?.resolved_query || detail.query || '').trim()
  if (!query) return null
  return {
    query: query.slice(0, 2000),
    intent: intent?.intent ?? 'unknown',
    slots: intent?.slots ?? {
      entities: [],
      time_range: '',
      domain: '',
      language: '',
      aspects: [],
    },
  }
}
