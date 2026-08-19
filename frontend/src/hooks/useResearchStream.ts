import { useEffect, useState } from 'react'
import { streamRun } from '../api/client'
import type { DagData, Report, ResearchEvent, RunStats } from '../types'

// disconnected：连接中断但运行未到终态——由 RunPage 的详情轮询接管兜底
export type StreamStatus = 'idle' | 'streaming' | 'disconnected' | 'done' | 'error' | 'cancelled'

export interface ResearchStreamState {
  events: ResearchEvent[] // 非 token 事件，喂给时间线
  reportMarkdown: string // token.delta 累加 / report 事件覆盖
  status: StreamStatus
  stats: RunStats | null
  dag: DagData | null
  elapsed: number // 直播耗时下界（已收到事件里的最大 elapsed）
  tokens: number // 直播累计 token（最新事件携带）
  tokensEstimated: boolean // Token 是否包含实时估算值
  findings: number // 直播累计发现数（finding 事件 data.count 之和）
}

const INITIAL: ResearchStreamState = {
  events: [],
  reportMarkdown: '',
  status: 'idle',
  stats: null,
  dag: null,
  elapsed: 0,
  tokens: 0,
  tokensEstimated: false,
  findings: 0,
}

// 只有 ORCHESTRATOR 的 done/error 才是运行终态；
// RESEARCHER 等阶段的 error 是被隔离的单点失败，运行仍在继续。
export function isTerminal(ev: ResearchEvent): boolean {
  return (
    ev.stage === 'ORCHESTRATOR' &&
    (ev.type === 'done' || ev.type === 'error' || ev.type === 'cancelled')
  )
}

// 纯归并函数：把一个 SSE 事件并入当前状态（便于单测）。
export function reduceStream(
  prev: ResearchStreamState,
  ev: ResearchEvent,
): ResearchStreamState {
  // A replay buffer can contain events after the orchestrator terminal event.
  // Once terminal, never let stale trailing events overwrite the final state.
  if (prev.status === 'done' || prev.status === 'error' || prev.status === 'cancelled') {
    return prev
  }
  // 直播统计：耗时取已见最大值（单调），token 取事件携带的累计值（缺省沿用旧值）。
  // 含 token 事件——合成阶段 token 增量持续到达，可让耗时平滑推进。
  const base: ResearchStreamState = {
    ...prev,
    elapsed: Math.max(prev.elapsed, ev.elapsed ?? 0),
    tokens: ev.tokens ?? prev.tokens,
    tokensEstimated: ev.tokens_estimated ?? prev.tokensEstimated,
  }
  switch (ev.type) {
    case 'token': {
      const delta = (ev.data as { delta?: string } | null)?.delta ?? ''
      return { ...base, reportMarkdown: base.reportMarkdown + delta }
    }
    case 'report': {
      const report = ev.data as unknown as Report | null
      return report?.markdown ? { ...base, reportMarkdown: report.markdown } : base
    }
    case 'finding': {
      const count = (ev.data as { count?: number } | null)?.count ?? 0
      return { ...base, findings: base.findings + count, events: [...base.events, ev] }
    }
    case 'done':
      if (!isTerminal(ev)) {
        return { ...base, events: [...base.events, ev] }
      }
      return {
        ...base,
        status: 'done',
        stats: (ev.data as unknown as RunStats | null) ?? base.stats,
        events: [...base.events, ev],
      }
    case 'error':
      if (!isTerminal(ev)) {
        // 非致命的单点失败：只进时间线，不终止整次运行的展示
        return { ...base, events: [...base.events, ev] }
      }
      return { ...base, status: 'error', events: [...base.events, ev] }
    case 'cancelled':
      if (!isTerminal(ev)) {
        return { ...base, events: [...base.events, ev] }
      }
      return { ...base, status: 'cancelled', events: [...base.events, ev] }
    case 'info': {
      const dag = (ev.data as { dag?: DagData } | null)?.dag
      return { ...base, dag: dag ?? base.dag, events: [...base.events, ev] }
    }
    default:
      // start / round → 时间线
      return { ...base, events: [...base.events, ev] }
  }
}

/**
 * 订阅某次研究的 SSE 事件流。后端 /api/runs/{id}/stream 自动二选一：
 * 进行中实时推送，已结束则从库回放（回放也包含 done/error 事件）。
 *
 * 连接中断时携带 Last-Event-ID 有界重连；多次失败后由详情轮询兜底。
 */
export function useResearchStream(runId: string | null): ResearchStreamState {
  const [state, setState] = useState<ResearchStreamState>(INITIAL)

  useEffect(() => {
    if (!runId) {
      setState(INITIAL)
      return
    }
    setState({ ...INITIAL, status: 'streaming' })
    const controller = new AbortController()
    let disposed = false
    let terminal = false
    let lastEventId: string | undefined

    const onMessage = (data: string, eventId?: string) => {
      // streamRun may dispatch several already-buffered SSE events
      // synchronously. Abort stops future reads, but not callbacks already
      // queued in the current buffer.
      if (disposed || terminal) return
      let ev: ResearchEvent
      try {
        ev = JSON.parse(data) as ResearchEvent
      } catch {
        return
      }
      if (eventId) lastEventId = eventId
      setState((prev) => reduceStream(prev, ev))
      if (isTerminal(ev)) {
        terminal = true
        controller.abort()
      }
    }

    void (async () => {
      const maxReconnects = 5
      for (let retry = 0; retry <= maxReconnects && !disposed && !terminal; retry += 1) {
        try {
          await streamRun(runId, onMessage, controller.signal, lastEventId)
        } catch (error: unknown) {
          if (
            disposed ||
            terminal ||
            (error instanceof DOMException && error.name === 'AbortError')
          ) {
            return
          }
        }
        if (disposed || terminal) return
        if (retry === maxReconnects) break
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(4000, 250 * 2 ** retry)))
      }
      if (!disposed && !terminal) {
        setState((prev) =>
          prev.status === 'streaming' ? { ...prev, status: 'disconnected' } : prev,
        )
      }
    })()

    return () => {
      disposed = true
      controller.abort()
    }
  }, [runId])

  return state
}
