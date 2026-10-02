import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import DagView from '../components/DagView'
import EventTimeline from '../components/EventTimeline'
import IntentPanel from '../components/IntentPanel'
import OrchestrationPipeline from '../components/OrchestrationPipeline'
import PrintableReport from '../components/PrintableReport'
import ReportActions from '../components/ReportActions'
import ReportView from '../components/ReportView'
import StatsBar from '../components/StatsBar'
import ModelReasoningPanel from '../components/ModelReasoningPanel'
import StatusBadge from '../components/StatusBadge'
import StructuredDocumentPreview from '../components/StructuredDocumentPreview'
import TagEditor from '../components/TagEditor'
import DeliverablesPanel from '../components/DeliverablesPanel'
import FileTree from '../components/FileTree'
import RunNarrative from '../components/RunNarrative'
import StepRail from '../components/StepRail'
import RunTaskSummary from '../components/RunTaskSummary'
import { useDeliverables, useNarrative, useRunTemplate, useWorkspace } from '../hooks/useWorkbench'
import { AppIcon } from '../components/AppIcon'
import { useResearchStream } from '../hooks/useResearchStream'
import { useCancelRun, useResumeRun, useRunDetail, useRunDocument } from '../hooks/useRuns'
import { appendTurn, turnFromRun } from '../lib/conversation'
import { countBlockedSources, flattenFindings, reportEvidenceToFindings } from '../lib/evidence'
import { displayReportTitle } from '../lib/reportTitle'
import { deriveResearchProgress } from '../lib/runProgress'
import { finalProseReview } from '../lib/workbench'
import type { ReportDocument, RunDetail, RunStatus } from '../types'

/**
 * 触发 HSI 领域表的意图名单。
 *
 * 这份名单是 `deep_research/intent/routing.py` 里 `_WORKFLOW_BY_INTENT` 的副本，
 * 属于已知的重复。之所以还留着：它只是**旧数据的回退路径**。新的 run 会在
 * `execution_policy.workflow` 或 `orchestration.workflow_name` 上直接带出
 * `hsi_review`，那两个信号由后端给出，优先级更高（见下方 includeHsiTables）。
 * 只有那两处都没有的历史 run 才会落到这份名单上，所以它漂移的代价有界——
 * 不是"新意图会漏判"，而是"某个历史 run 的表可能不出现"。
 */
const HSI_INTENTS = new Set([
  'literature_review',
  'method_comparison',
  'benchmark_survey',
  'reproducibility_check',
  'dataset_discovery',
])

function runIncludesHsiTables(
  intent: RunDetail['intent'] | undefined,
  workflowName?: string | null,
) {
  return Boolean(
    workflowName === 'hsi_review' ||
    intent?.execution_policy?.workflow === 'hsi_review' ||
    (intent && (HSI_INTENTS.has(intent.intent) || intent.intent.startsWith('hsi_'))),
  )
}

/**
 * 恢复是否仍未生效（即"这份快照还是恢复前那一份"）。
 *
 * 判据两条，任一成立即视为已生效：
 *   1. attempt 超过发起恢复时的基线——后端 prepare_resume 在返回 202 之前就 +1；
 *   2. status 已离开 error——worker 模式走 requeue_failed_run，它只把 status
 *      改成 running 而不动 attempt，所以这一条不能省。
 *
 * 只看 status 是不够的：恢复后的新尝试若立刻再次失败，status 会停回 error，
 * 与恢复前无从区分，判据就永远解不开，页面会卡在"运行中"并无限轮询。
 *
 * 定义在组件外，是为了让轮询回调与渲染共用同一份判据——写成组件内的派生值，
 * 轮询闭包引用它会落进 TDZ。
 */
function resumePending(
  baseline: number | null,
  status: string | undefined,
  attempt: number | null | undefined,
): boolean {
  if (baseline == null) return false
  if (attempt != null && attempt > baseline) return false
  if (status != null && status !== 'error') return false
  return true
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  // 打印预览：在屏幕上按 A4 版心呈现打印布局，替换掉交互视图。
  const [printPreview, setPrintPreview] = useState(false)
  const [streamRestartToken, setStreamRestartToken] = useState(0)
  // 恢复发起时的 attempt 基线；null＝当前没有在等恢复生效。
  // 存基线而不是存布尔，是因为"恢复是否已生效"只能靠 attempt 变化判定：
  // 一次立刻再失败的恢复会让 status 停在 error，与恢复前无从区分。
  const [resumeBaseline, setResumeBaseline] = useState<number | null>(null)
  const stream = useResearchStream(id ?? null, streamRestartToken)
  const cancel = useCancelRun(id)
  const resume = useResumeRun(id)
  // 详情（query / 已落库报告）：流式中轮询；流断开但未到终态时继续轮询兜底，
  // 直到 DB 状态本身到达终态才停止
  // Keep syncing until the persisted run reaches a terminal state. The stream can
  // report an error before the orchestrator has written that state to the database.
  const shouldSyncDetail = stream.status !== 'idle'
  const detail = useRunDetail(id, {
    refetchInterval: (q) => {
      const s = q.state.data?.status
      const finished = s === 'done' || s === 'error' || s === 'cancelled'
      // 等恢复生效期间要继续轮询，哪怕 DB 状态本身已是终态——那个终态正是
      // 我们在等着被新一次尝试覆盖的旧值。
      const waiting = resumePending(resumeBaseline, s, q.state.data?.orchestration?.attempt)
      return shouldSyncDetail && (!finished || waiting) ? 4000 : false
    },
  })

  const dbStatus = detail.data?.status
  const persistedTerminal = dbStatus === 'done' || dbStatus === 'error' || dbStatus === 'cancelled'
  const resuming = resumePending(resumeBaseline, dbStatus, detail.data?.orchestration?.attempt)
  // A resume starts a new attempt while the cached detail still contains the
  // previous error. Treat that snapshot as stale until the server reports the
  // new attempt, otherwise the page would keep showing a terminal error and
  // disable the live stream that was just restarted.
  const dbFinished = persistedTerminal && !resuming
  const refetchDetail = detail.refetch
  const persistedIntent = detail.data?.intent
  const executionPolicy = persistedIntent?.execution_policy
  // 后端信号优先：execution_policy / workflow_name 是本次运行真正走的编排。
  // intent 名单只兜住那两者都缺失的历史 run（见 HSI_INTENTS 的注释）。
  const includeHsiTables = runIncludesHsiTables(
    persistedIntent,
    executionPolicy?.workflow ?? detail.data?.orchestration?.workflow_name,
  )
  // The structured document is fetched only after persistence reaches a
  // terminal state. Streaming and the legacy detail payload remain the
  // immediate source while a run is active or when this optional endpoint
  // is unavailable.
  const structuredDocument = useRunDocument(id, {
    enabled: dbFinished,
    includeHsiTables,
  })
  const deliverables = useDeliverables(id, dbFinished && Boolean(detail.data?.report))
  const runTemplate = useRunTemplate(id)
  const narrative = useNarrative(id, !dbFinished)
  const workspace = useWorkspace(id, !dbFinished)

  useEffect(() => {
    if (stream.status !== 'done' && stream.status !== 'error' && stream.status !== 'cancelled')
      return
    void refetchDetail()
  }, [refetchDetail, stream.status])

  const query = displayReportTitle(detail.data?.query ?? '')
  const status: RunStatus = dbFinished
    ? (dbStatus as RunStatus)
    : stream.status === 'done'
      ? 'done'
      : stream.status === 'error'
        ? 'error'
        : stream.status === 'cancelled'
          ? 'cancelled'
          : (detail.data?.status ?? 'running')
  // Once the stream has ended, any persisted report is the authoritative complete copy.
  const documentMarkdown = useMemo(
    () =>
      structuredDocument.data?.blocks
        .filter(
          (block): block is Extract<ReportDocument['blocks'][number], { kind: 'prose' }> =>
            block.kind === 'prose',
        )
        .map((block) => block.markdown.trim())
        .filter(Boolean)
        .join('\n\n') || '',
    [structuredDocument.data],
  )
  const persistedMarkdown = detail.data?.report?.markdown || ''
  const preferPersistedReport =
    Boolean(documentMarkdown || persistedMarkdown) &&
    (dbFinished ||
      stream.status === 'disconnected' ||
      stream.status === 'done' ||
      stream.status === 'error' ||
      stream.status === 'cancelled')
  const markdown = preferPersistedReport
    ? documentMarkdown || persistedMarkdown
    : stream.reportMarkdown || persistedMarkdown
  const streaming = stream.status === 'streaming' && !dbFinished
  const liveActive =
    (stream.status === 'streaming' || stream.status === 'disconnected') && !dbFinished
  const connectionStatus =
    status === 'done'
      ? 'done'
      : status === 'error'
        ? 'error'
        : status === 'cancelled'
          ? 'cancelled'
          : stream.status
  const progress = deriveResearchProgress({
    execution: detail.data?.orchestration,
    events: stream.events,
    runStatus: status,
  })
  // 证据链数据：findings 来自详情（流式阶段可能为空，ReportView 会优雅降级）；
  // 来源拦截数解析事件流里的 source policy 审计事件（直播与历史回放都带）。
  const detailResults = detail.data?.results
  const evidenceFindings = useMemo(() => {
    const records = structuredDocument.data?.evidence
    // The structured endpoint is authoritative once it contains persisted
    // evidence. Older runs may legitimately return no records, so retain the
    // detail payload as a compatibility fallback in that case.
    return records && records.length > 0
      ? reportEvidenceToFindings(records)
      : flattenFindings(detailResults)
  }, [detailResults, structuredDocument.data?.evidence])
  const citations = useMemo(() => {
    const references = structuredDocument.data?.references
    if (references && references.length > 0) {
      return [...references].sort((a, b) => a.index - b.index).map((reference) => reference.url)
    }
    return detail.data?.report?.citations ?? []
  }, [detail.data?.report?.citations, structuredDocument.data?.references])
  const tableOptions = useMemo(
    () =>
      structuredDocument.data?.blocks
        .filter(
          (block): block is Extract<ReportDocument['blocks'][number], { kind: 'table' }> =>
            block.kind === 'table',
        )
        .map((table) => ({ id: table.id, label: table.title || table.id })) ?? [],
    [structuredDocument.data],
  )
  const blockedSources = useMemo(() => {
    const structuredBlocked = structuredDocument.data?.overview.blocked_sources
    return structuredBlocked != null ? structuredBlocked : countBlockedSources(stream.events)
  }, [stream.events, structuredDocument.data?.overview.blocked_sources])

  // 「继续追问」把本次运行折叠成一轮历史再跳去提问页。只在研究真正跑完后可用：
  // 半截的运行没有可供下一轮指代的结论，把它塞进历史只会误导消解器。
  const runDetail = detail.data
  const canFollowUp = status === 'done' && Boolean(runDetail?.query)
  const canCancel = Boolean(id) && (status === 'pending' || status === 'running')
  const canResume = Boolean(
    id &&
    status === 'error' &&
    !resuming &&
    detail.data?.orchestration?.checkpoint &&
    Object.keys(detail.data.orchestration.checkpoint).length > 0,
  )

  function askFollowUp() {
    if (!runDetail) return
    const turn = turnFromRun(runDetail)
    if (turn) appendTurn(turn)
    navigate('/?followup=1')
  }

  function resumeRun() {
    if (!id) return
    // 基线在发起前定格。onSuccess 时 detail 可能已经被 invalidate 后刷新过，
    // 那时读到的 attempt 已经是新值，拿它当基线就永远等不到"超过基线"。
    const baseline = detail.data?.orchestration?.attempt ?? 0
    resume.mutate(undefined, {
      onSuccess: () => {
        setResumeBaseline(baseline)
        setPrintPreview(false)
        setStreamRestartToken((value) => value + 1)
        void refetchDetail()
      },
    })
  }

  const quality = deliverables.data
    ? {
        status: deliverables.data.status,
        passed: deliverables.data.gates.filter((gate) => gate.status === 'pass').length,
        total: deliverables.data.gates.length,
      }
    : null
  const createdAt = detail.data?.created_at
    ? new Date(detail.data.created_at).toLocaleString('zh-CN', { hour12: false })
    : ''

  if (detail.isError) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <section className="panel run-missing" role="alert">
        <span className="empty-state-icon" aria-hidden="true">
          <AppIcon name="circle-x" size={24} />
        </span>
        <h1 className="empty-state-title">{notFound ? '任务不存在' : '加载任务详情失败'}</h1>
        <p className="hint">
          {notFound
            ? '运行不存在或已被删除，请回历史页确认。'
            : `加载运行详情失败：${detail.error instanceof Error ? detail.error.message : '未知错误'}`}
        </p>
        <button type="button" className="btn btn-secondary" onClick={() => navigate('/history')}>
          返回任务记录
        </button>
      </section>
    )
  }

  return (
    <div className={`run-workbench${liveActive ? ' is-live' : ''}`}>
      <header className={`run-head${canFollowUp ? ' has-followup' : ''}`}>
        <div className="run-head-main">
          <div className="run-head-meta">
            <StatusBadge status={status} />
            {runTemplate.data?.template && (
              <span className="run-head-type">
                <AppIcon name="file" size={13} aria-hidden="true" />
                {runTemplate.data.template.title}
              </span>
            )}
            {createdAt && (
              <span className="run-head-time">
                <AppIcon name="clock" size={13} aria-hidden="true" />
                {createdAt}
              </span>
            )}
          </div>
          <h1 className="run-q">{query || '加载中…'}</h1>
          {id && <TagEditor runId={id} tags={detail.data?.tags ?? []} />}
          {stream.status === 'disconnected' && !dbFinished && (
            <span className="run-head-note" role="status">
              <AppIcon name="refresh" size={13} aria-hidden="true" />
              实时连接已断开，正在轮询获取进度…
            </span>
          )}
          {resume.isError && !resuming && (
            <span className="error-text small" role="alert">
              恢复失败：{resume.error instanceof Error ? resume.error.message : '未知错误'}
            </span>
          )}
        </div>
        <div className="run-head-actions">
          {id && runTemplate.data?.template.input_kind === 'paper' && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => navigate(`/runs/${encodeURIComponent(id)}/read`)}
            >
              <AppIcon name="book" size={14} aria-hidden="true" />
              对照原文阅读
            </button>
          )}
          {canResume && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={resumeRun}
              disabled={resume.isPending}
            >
              <AppIcon name="refresh" size={14} aria-hidden="true" />
              {resume.isPending ? '恢复中' : '恢复运行'}
            </button>
          )}
          {(canCancel || status === 'cancelling') && (
            <button
              type="button"
              className="btn btn-sm danger"
              disabled={cancel.isPending || status === 'cancelling'}
              onClick={() => cancel.mutate()}
            >
              <AppIcon name="stop" size={14} aria-hidden="true" />
              {status === 'cancelling' || cancel.isPending ? '取消中' : '取消运行'}
            </button>
          )}
          {canFollowUp && (
            <button
              type="button"
              className="btn btn-primary btn-sm run-followup"
              onClick={askFollowUp}
            >
              <AppIcon name="sparkles" size={14} aria-hidden="true" />
              继续追问
            </button>
          )}
        </div>
      </header>

      <StatsBar
        stats={stream.stats}
        detail={detail.data ?? null}
        progress={progress}
        live={{ elapsed: stream.elapsed, tokens: stream.tokens, findings: stream.findings }}
        liveActive={liveActive}
        connectionStatus={connectionStatus}
        tokensEstimated={stream.tokensEstimated}
        quality={quality}
      />

      <RunTaskSummary info={runTemplate.data} />
      <ModelReasoningPanel events={stream.events} />

      {/* 打印预览独占阅读区域，屏幕中的运行活动由打印样式隐藏。 */}
      <div className={`run-columns run-workbench-grid${printPreview ? ' is-print-preview' : ''}`}>
        {/* 左栏：步骤轨道 + 人话进度 */}
        <aside className="run-left-pane" aria-label="运行步骤与进展">
          <StepRail workspace={workspace.data} />
          <RunNarrative narrative={narrative.data} />
        </aside>
        <div
          className={`report-panel run-report-column${liveActive ? ' is-streaming' : ''}${printPreview ? ' is-print-preview' : ''}`}
        >
          <section className="panel run-report-card" aria-labelledby="run-report-title">
            <div className="run-report-toolbar">
              <h2 className="panel-title" id="run-report-title">
                <AppIcon name="book" size={16} aria-hidden="true" />
                研究报告
                {liveActive && <span className="badge info">生成中</span>}
              </h2>
              <div className="report-panel-tools">
                <ReportActions
                  markdown={markdown}
                  query={query}
                  runId={id}
                  includeHsiTables={includeHsiTables}
                  tableOptions={tableOptions}
                  documentReady={Boolean(structuredDocument.data)}
                  supportFailed={
                    structuredDocument.data?.final_validation?.support_status === 'fail' ||
                    finalProseReview(detail.data)?.status === 'fail'
                  }
                  previewing={printPreview}
                  onTogglePreview={() => setPrintPreview((value) => !value)}
                />
              </div>
            </div>
            {printPreview ? (
              <PrintableReport
                markdown={markdown}
                query={query}
                runId={id}
                findings={evidenceFindings}
                citations={citations}
                blockedSources={blockedSources}
                createdAt={detail.data?.created_at}
                preview
                title={structuredDocument.data?.title}
                document={structuredDocument.data}
                supportFailed={finalProseReview(detail.data)?.status === 'fail'}
              />
            ) : (
              <>
                {!streaming &&
                  structuredDocument.data?.final_validation?.scope === 'source_processing' && (
                    <div className="run-validation-note" role="alert" aria-label="材料处理未完成">
                      <AppIcon name="shield" size={14} aria-hidden="true" />
                      <div>
                        <p>
                          部分检索或材料抽取未完成，本次没有生成正式交付。已有来源和结果已保留。
                        </p>
                        <ul>
                          {(structuredDocument.data.final_validation.issues ?? []).map(
                            (issue, index) => (
                              <li key={index}>{issue}</li>
                            ),
                          )}
                        </ul>
                      </div>
                    </div>
                  )}
                {!streaming &&
                  structuredDocument.data?.final_validation?.scope === 'citation_and_numbers' && (
                    <p className="run-validation-note" role="status">
                      <AppIcon name="shield" size={14} aria-hidden="true" />
                      <span>
                        {structuredDocument.data.final_validation.fallback
                          ? '正文未通过引用或数值检查，已替换为核验素材摘要。'
                          : '已检查正文的引用与数值一致性。'}
                        证据标签仅反映素材状态，不代表逐段语义审核。
                      </span>
                    </p>
                  )}
                <ReportView
                  markdown={markdown}
                  streaming={streaming}
                  isLive={liveActive}
                  findings={evidenceFindings}
                  citations={citations}
                  blockedSources={blockedSources}
                  finalReview={finalProseReview(detail.data)}
                  bibliography={structuredDocument.data?.bibliography}
                />
              </>
            )}
          </section>
          {!printPreview && (
            <>
              {id && dbFinished && detail.data?.report && (
                <DeliverablesPanel
                  key={id}
                  runId={id}
                  registry={deliverables.data}
                  loading={deliverables.isLoading}
                  error={deliverables.error}
                  onUpdated={deliverables.setRegistry}
                />
              )}
              {structuredDocument.data && (
                <StructuredDocumentPreview document={structuredDocument.data} />
              )}
              {/* 常驻打印 DOM：屏幕上由 print.css 隐藏，Ctrl+P 与「打印」按钮走同一路径，
                  不需要先切到预览再打印。 */}
              <PrintableReport
                markdown={markdown}
                query={query}
                runId={id}
                findings={evidenceFindings}
                citations={citations}
                blockedSources={blockedSources}
                createdAt={detail.data?.created_at}
                document={structuredDocument.data}
                title={structuredDocument.data?.title}
                supportFailed={finalProseReview(detail.data)?.status === 'fail'}
              />
            </>
          )}
        </div>
        {/* 右栏：产物文件树 + 机器事件时间线（排查细节用） */}
        <aside className="run-right-pane" aria-label="产物与活动">
          {id && <FileTree runId={id} workspace={workspace.data} />}
          <details className="panel run-timeline-panel" open={liveActive}>
            <summary className="run-collapsible-head">
              <span className="panel-title">
                <AppIcon name="activity" size={15} aria-hidden="true" />
                实时活动
              </span>
              <AppIcon name="chevron-down" size={15} aria-hidden="true" />
            </summary>
            <EventTimeline events={stream.events} streaming={streaming} />
          </details>
          {stream.dag && (
            <section className="panel run-dag-panel">
              <h3 className="panel-title">子问题依赖</h3>
              <DagView dag={stream.dag} />
            </section>
          )}
        </aside>
      </div>

      {/* 意图判定与编排流水线属于诊断信息：默认折叠，放在页面底部 */}
      <details className="panel run-diagnostics">
        <summary className="run-collapsible-head">
          <span className="panel-title">
            <AppIcon name="gauge" size={15} aria-hidden="true" />
            诊断详情
          </span>
          <span className="hint">诊断信息，用于排查路由与编排</span>
          <AppIcon name="chevron-down" size={15} aria-hidden="true" />
        </summary>
        <div className="run-diagnostics-body">
          <IntentPanel intent={detail.data?.intent ?? null} />
          <OrchestrationPipeline
            execution={detail.data?.orchestration}
            events={stream.events}
            runStatus={status}
          />
        </div>
      </details>
    </div>
  )
}
