import { useEffect, useState } from 'react'
import { streamUrl } from '../api/client'
import type { DagData, Report, ResearchEvent, RunStats } from '../types'

// disconnected：连接中断但运行未到终态——由 RunPage 的详情轮询接管兜底
export type StreamStatus = 'idle' | 'streaming' | 'disconnected' | 'done' | 'error'

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
  return ev.stage === 'ORCHESTRATOR' && (ev.type === 'done' || ev.type === 'error')
}

// 纯归并函数：把一个 SSE 事件并入当前状态（便于单测）。
export function reduceStream(
  prev: ResearchStreamState,
  ev: ResearchEvent,
): ResearchStreamState {
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
 * 连接中断（网络抖动、后端重启）时不自动重连——后端重连会全量重放 buffer，
 * 事件会重复；改为置 'disconnected'，由 RunPage 的详情轮询兜底拿到最终报告。
 */
export function useResearchStream(runId: string | null): ResearchStreamState {
  const [state, setState] = useState<ResearchStreamState>(INITIAL)

  useEffect(() => {
    if (!runId) {
      setState(INITIAL)
      return
    }
    setState({ ...INITIAL, status: 'streaming' })
    const es = new EventSource(streamUrl(runId))

    es.onmessage = (e: MessageEvent<string>) => {
      let ev: ResearchEvent
      try {
        ev = JSON.parse(e.data) as ResearchEvent
      } catch {
        return
      }
      setState((prev) => reduceStream(prev, ev))
      if (isTerminal(ev)) {
        es.close()
      }
    }

    es.onerror = () => {
      es.close()
      // 已到终态后的连接关闭是正常收尾；否则标记断连，交给轮询兜底
      setState((prev) =>
        prev.status === 'streaming' ? { ...prev, status: 'disconnected' } : prev,
      )
    }

    return () => es.close()
  }, [runId])

  return state
}
