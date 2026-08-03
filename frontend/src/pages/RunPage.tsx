import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import DagView from '../components/DagView'
import EventTimeline from '../components/EventTimeline'
import IntentPanel from '../components/IntentPanel'
import OrchestrationPipeline from '../components/OrchestrationPipeline'
import ReportActions from '../components/ReportActions'
import ReportView from '../components/ReportView'
import StatsBar from '../components/StatsBar'
import StatusBadge from '../components/StatusBadge'
import TagEditor from '../components/TagEditor'
import { AppIcon } from '../components/AppIcon'
import { useResearchStream } from '../hooks/useResearchStream'
import { useRunDetail } from '../hooks/useRuns'
import { countBlockedSources, flattenFindings } from '../lib/evidence'
import { deriveResearchProgress } from '../lib/runProgress'
import type { RunStatus } from '../types'

export default function RunPage() {
  const { id } = useParams<{ id: string }>()
  // 研究报告是核心产出：默认给报告栏更大权重，也允许一键展开全宽阅读。
  const [reportExpanded, setReportExpanded] = useState(false)
  const stream = useResearchStream(id ?? null)
  // 详情（query / 已落库报告）：流式中轮询；流断开但未到终态时继续轮询兜底，
  // 直到 DB 状态本身到达终态才停止
  // Keep syncing until the persisted run reaches a terminal state. The stream can
  // report an error before the orchestrator has written that state to the database.
  const shouldSyncDetail = stream.status !== 'idle'
  const detail = useRunDetail(id, {
    refetchInterval: (q) => {
      const s = q.state.data?.status
      const finished = s === 'done' || s === 'error'
      return shouldSyncDetail && !finished ? 4000 : false
    },
  })

  const dbStatus = detail.data?.status
  const dbFinished = dbStatus === 'done' || dbStatus === 'error'
  const refetchDetail = detail.refetch

  useEffect(() => {
    if (stream.status !== 'done' && stream.status !== 'error') return
    void refetchDetail()
  }, [refetchDetail, stream.status])

  const query = detail.data?.query ?? ''
  const status: RunStatus = dbFinished
    ? (dbStatus as RunStatus)
    : stream.status === 'done'
      ? 'done'
      : stream.status === 'error'
        ? 'error'
        : (detail.data?.status ?? 'running')
  // Once the stream has ended, any persisted report is the authoritative complete copy.
  const persistedMarkdown = detail.data?.report?.markdown || ''
  const preferPersistedReport =
    Boolean(persistedMarkdown) &&
    (dbFinished || stream.status === 'disconnected' || stream.status === 'done' || stream.status === 'error')
  const markdown = preferPersistedReport
    ? persistedMarkdown
    : stream.reportMarkdown || persistedMarkdown
  const streaming = stream.status === 'streaming' && !dbFinished
  const liveActive =
    (stream.status === 'streaming' || stream.status === 'disconnected') && !dbFinished
  const connectionStatus =
    status === 'done' ? 'done' : status === 'error' ? 'error' : stream.status
  const progress = deriveResearchProgress({
    execution: detail.data?.orchestration,
    events: stream.events,
    runStatus: status,
  })
  // 证据链数据：findings 来自详情（流式阶段可能为空，ReportView 会优雅降级）；
  // 来源拦截数解析事件流里的 source policy 审计事件（直播与历史回放都带）。
  const detailResults = detail.data?.results
  const evidenceFindings = useMemo(() => flattenFindings(detailResults), [detailResults])
  const blockedSources = useMemo(() => countBlockedSources(stream.events), [stream.events])

  if (detail.isError) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <div className="stack">
        <div className="panel">
          <div className="empty">
            <div className="ico"><AppIcon name="circle-x" size={24} aria-hidden="true" /></div>
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
        {id && <TagEditor runId={id} tags={detail.data?.tags ?? []} />}
      </div>

      <StatsBar
        stats={stream.stats}
        detail={detail.data ?? null}
        progress={progress}
        live={{ elapsed: stream.elapsed, tokens: stream.tokens, findings: stream.findings }}
        liveActive={liveActive}
        connectionStatus={connectionStatus}
        tokensEstimated={stream.tokensEstimated}
      />

      <IntentPanel intent={detail.data?.intent ?? null} />

      <OrchestrationPipeline
        execution={detail.data?.orchestration}
        events={stream.events}
        runStatus={status}
      />

      <div className={`grid-2 run-columns${reportExpanded ? ' report-expanded' : ''}`}>
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
        <div className={`panel report-panel${streaming ? ' is-streaming' : ''}`}>
          <div className="row between panel-head">
            <h3 className="panel-title">研究报告</h3>
            <div className="row report-panel-tools">
              <button
                type="button"
                className="btn btn-ghost btn-sm report-expand-toggle"
                aria-pressed={reportExpanded}
                onClick={() => setReportExpanded((value) => !value)}
              >
                <AppIcon name={reportExpanded ? 'panel-open' : 'panel-close'} size={14} aria-hidden="true" />
                {reportExpanded ? '双栏视图' : '全宽阅读'}
              </button>
              <ReportActions markdown={markdown} query={query} runId={id} />
            </div>
          </div>
          <ReportView
            markdown={markdown}
            streaming={streaming}
            findings={evidenceFindings}
            citations={detail.data?.report?.citations ?? []}
            blockedSources={blockedSources}
          />
        </div>
      </div>
    </div>
  )
}
