import { useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import DagView from '../components/DagView'
import EventTimeline from '../components/EventTimeline'
import ReportView from '../components/ReportView'
import StatsBar from '../components/StatsBar'
import StatusBadge from '../components/StatusBadge'
import { useResearchStream } from '../hooks/useResearchStream'
import { useRunDetail } from '../hooks/useRuns'
import type { RunStatus } from '../types'

export default function RunPage() {
  const { id } = useParams<{ id: string }>()
  const stream = useResearchStream(id ?? null)
  // 详情（query / 已落库报告）：流式中轮询；流断开但未到终态时继续轮询兜底，
  // 直到 DB 状态本身到达终态才停止
  const streamActive = stream.status === 'streaming' || stream.status === 'disconnected'
  const detail = useRunDetail(id, {
    refetchInterval: (q) => {
      const s = q.state.data?.status
      const finished = s === 'done' || s === 'error'
      return streamActive && !finished ? 4000 : false
    },
  })

  const dbStatus = detail.data?.status
  const dbFinished = dbStatus === 'done' || dbStatus === 'error'

  const query = detail.data?.query ?? ''
  const status: RunStatus =
    stream.status === 'done'
      ? 'done'
      : stream.status === 'error'
        ? 'error'
        : (detail.data?.status ?? 'running')
  // 报告：优先流式累积；为空时回退到已落库报告（回放 / 断连兜底场景）
  const markdown = stream.reportMarkdown || detail.data?.report?.markdown || ''
  const streaming = stream.status === 'streaming'

  if (detail.isError) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <div className="stack">
        <div className="panel">
          <div className="empty">
            <div className="ico">✗</div>
            {notFound
              ? '运行不存在或已被删除，请回历史页确认。'
              : `加载运行详情失败：${detail.error instanceof Error ? detail.error.message : '未知错误'}`}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="stack">
      <div className="panel run-head">
        <div className="run-q">{query || '加载中…'}</div>
        <StatusBadge status={status} />
        {stream.status === 'disconnected' && !dbFinished && (
          <span className="muted small">实时连接已断开，正在轮询获取进度…</span>
        )}
      </div>

      <StatsBar stats={stream.stats} detail={detail.data ?? null} />

      <div className="grid-2">
        <div className="stack">
          <div className="panel">
            <h3 className="panel-title">Agent 实时活动</h3>
            <EventTimeline events={stream.events} streaming={streaming} />
          </div>
          {stream.dag && (
            <div className="panel">
              <h3 className="panel-title">子问题依赖（DAG 分层调度）</h3>
              <DagView dag={stream.dag} />
            </div>
          )}
        </div>
        <div className="panel">
          <h3 className="panel-title">研究报告</h3>
          <ReportView markdown={markdown} streaming={streaming} />
        </div>
      </div>
    </div>
  )
}
