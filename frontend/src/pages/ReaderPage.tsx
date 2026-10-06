import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { createConversation, getConversation, getReader, listConversations } from '../api/client'
import { AppIcon } from '../components/AppIcon'
import QaMessageView from '../components/QaMessage'
import QaStreamingAnswer from '../components/QaStreamingAnswer'
import ReportView from '../components/ReportView'
import ReadingMapPanel from '../components/ReadingMapPanel'
import {
  canLocateReadingAnchor,
  readingNavigation,
  type ReadingNavigation,
} from '../api/readingMap'
import type { PdfHighlight } from '../components/PdfViewer'
import { useProjects } from '../hooks/useLibrary'
import { useRunDetail, useRunDocument } from '../hooks/useRuns'
import { flattenFindings, reportEvidenceToFindings } from '../lib/evidence'
import { documentForEvidence } from '../lib/readerDocuments'
import { pendingQaId, reconcileQaRequests, runQaRequest } from '../lib/qaRequest'
import type { QaActivity, QaEvidence, QaSourceOption, QaMessage } from '../types'
import { appendQaActivity } from '../lib/qaActivity'
import { useCancelQaRequest } from '../hooks/useCancelQaRequest'
import { latestQaContinuation, savedQaScope } from '../lib/qaRequest'
import { finalProseReview } from '../lib/workbench'

// PDF.js 体积较大，只在打开原文时加载
const PdfViewer = lazy(() => import('../components/PdfViewer'))

function isActive(status: string | undefined): boolean {
  return status === 'pending' || status === 'running' || status === 'cancelling'
}

export default function ReaderPage() {
  const { id = '' } = useParams<{ id: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const navigation = readingNavigation(
    (location.state as { readingAnchor?: unknown } | null)?.readingAnchor,
  )
  const [includeHsiTables] = useState(navigation?.includeHsiTables ?? false)
  const [mapInitialUnit] = useState(navigation?.unitId || '')
  const [mapOpened, setMapOpened] = useState(false)
  const [mapNotice, setMapNotice] = useState('')
  const queryClient = useQueryClient()
  const reader = useQuery({
    queryKey: ['reader', id],
    queryFn: ({ signal }) => getReader(id, signal),
    enabled: Boolean(id),
    retry: false,
    refetchInterval: (query) => (isActive(query.state.data?.status) ? 5000 : false),
  })
  const running = isActive(reader.data?.status)
  const detail = useRunDetail(id, { refetchInterval: running ? 5000 : false })
  const reportDocument = useRunDocument(id, {
    enabled: !running && Boolean(detail.data?.report),
    includeHsiTables,
  })
  const projects = useProjects()
  const conversations = useQuery({
    queryKey: ['qa-conversations', 'run', id],
    queryFn: ({ signal }) => listConversations(signal, id),
    enabled: Boolean(id),
  })
  // undefined：沿用最近一次会话；null：用户点了「新对话」，下一问再建
  const [chosen, setChosen] = useState<string | null | undefined>(undefined)
  const conversationId = chosen === undefined ? conversations.data?.[0]?.id : (chosen ?? undefined)
  const conversation = useQuery({
    queryKey: ['qa-conversation', conversationId],
    queryFn: ({ signal }) => getConversation(conversationId as string, signal),
    enabled: Boolean(conversationId),
    refetchInterval: (query) =>
      query.state.data?.messages.some((m) => m.status === 'pending' || m.status === 'running')
        ? 5000
        : false,
  })

  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [streamingAnswer, setStreamingAnswer] = useState('')
  const [activity, setActivity] = useState<QaActivity[]>([])
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null)
  const activeRequestRef = useRef<string | null>(null)
  const resumeRef = useRef<QaMessage | null>(null)
  const revisionRef = useRef<QaMessage | null>(null)
  const recoveryRef = useRef<QaMessage | null>(null)
  const cancelledByUser = useRef(false)
  const targetRef = useRef<string>()
  const attemptedResume = useRef(new Set<string>())
  const connection = useRef<AbortController | null>(null)
  const stop = useCancelQaRequest((message, requestId) => {
    if (message.status === 'cancelled' && requestId === activeRequestRef.current) {
      cancelledByUser.current = true
      connection.current?.abort()
    }
  })
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      connection.current?.abort()
    }
  }, [])
  const [withLibrary, setWithLibrary] = useState(false)
  const [projectId, setProjectId] = useState('')
  const [withWeb, setWithWeb] = useState(false)
  const [pane, setPane] = useState<'pdf' | 'report' | 'map'>('pdf')
  const [documentId, setDocumentId] = useState<string>()
  const [highlight, setHighlight] = useState<PdfHighlight | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const documents = reader.data?.documents ?? []
  const current =
    documents.find((item) => item.id === documentId) ??
    documents.find((item) => item.pdf) ??
    documents[0]
  const projectList = projects.data ?? []
  const libraryReady = !withLibrary || Boolean(projectId)
  const paperReady = reader.data?.status === 'done' || reader.data?.status === 'needs_review'
  const canAsk = paperReady && reader.data?.can_ask !== false
  const consumedNavigation = useRef('')
  useEffect(() => {
    if (!navigation || consumedNavigation.current === location.key || !reader.data || !detail.data)
      return
    if (!reportDocument.data && !reportDocument.isError && detail.data.report) return
    consumedNavigation.current = location.key
    const target = reader.data.documents.find(
      (item) => item.id === navigation.anchor.document_id && item.pdf,
    )
    if (
      navigation.runId !== id ||
      navigation.documentVersion !== reportDocument.data?.content_version
    ) {
      setHighlight(null)
      setMapNotice('报告版本已变化，请重新选择当前版本的原文依据。')
    } else if (!target) {
      setHighlight(null)
      setMapNotice('这条依据没有可确认的原版 PDF，请查看来源记录。')
    } else {
      setDocumentId(target.id)
      setPane('pdf')
      setMapNotice('')
      setHighlight((previous) => ({
        quote: navigation.anchor.quote,
        token: (previous?.token ?? 0) + 1,
      }))
    }
    // Quotes stay out of URLs and are removed from navigation state after use.
    navigate(location.pathname + location.search, { replace: true, state: null })
  }, [
    navigation,
    location.key,
    location.pathname,
    location.search,
    reader.data,
    detail.data,
    reportDocument.data,
    reportDocument.isError,
    id,
    navigate,
  ])

  const ask = useMutation({
    mutationFn: async (text: string) => {
      const controller = new AbortController()
      connection.current = controller
      let target = conversationId
      const resume = resumeRef.current
      resumeRef.current = null
      const revision = revisionRef.current
      revisionRef.current = null
      const recovery = recoveryRef.current
      recoveryRef.current = null
      if (!target) {
        target = (await createConversation(text.slice(0, 60), id)).id
        controller.signal.throwIfAborted()
        setChosen(target)
      }
      targetRef.current = target
      const sources: QaSourceOption[] = []
      if (withLibrary) sources.push('library')
      if (withWeb) sources.push('web')
      const revisionMessageId = revision?.id ?? resume?.request_payload?.revision_message_id
      const scope = recovery
        ? { ...savedQaScope(recovery), resumeMessageId: recovery.id }
        : resume
          ? savedQaScope(resume)
          : revisionMessageId
            ? { sources: [], projectId: undefined, revisionMessageId }
            : { sources, projectId: withLibrary ? projectId : undefined }
      const requestId = pendingQaId(target, text, scope, resume?.request_id ?? undefined)
      attemptedResume.current.add(requestId)
      activeRequestRef.current = requestId
      setActiveRequestId(requestId)
      await runQaRequest(
        target,
        text,
        scope,
        requestId,
        (delta) => setStreamingAnswer((current) => current + delta),
        (event) => {
          if (event.type === 'reset') setStreamingAnswer('')
          if (event.type === 'reset' && event.replay) setActivity([])
          setActivity((current) => appendQaActivity(current, event))
        },
        controller.signal,
      )
      return target
    },
    onMutate: (text) => {
      cancelledByUser.current = false
      setPending(text)
      setStreamingAnswer('')
      setActivity([])
    },
    onError: (_error, text) => {
      if (mounted.current && !cancelledByUser.current) setDraft(text)
    },
    onSettled: async (target) => {
      await queryClient.invalidateQueries({ queryKey: ['qa-conversations', 'run', id] })
      if (target ?? targetRef.current)
        await queryClient.invalidateQueries({
          queryKey: ['qa-conversation', target ?? targetRef.current],
        })
      if (!mounted.current) return
      setPending(null)
      setActiveRequestId(null)
      activeRequestRef.current = null
      setStreamingAnswer('')
    },
  })

  const messages = (conversation.data?.messages ?? []).filter(
    (m) => !activeRequestId || m.request_id !== activeRequestId,
  )
  const asking = ask.isPending
  const mutateQuestion = ask.mutate
  useEffect(() => {
    if (conversationId && conversation.data)
      reconcileQaRequests(conversationId, conversation.data.messages)
    if (asking) return
    const unfinished = conversation.data?.messages.find(
      (m) =>
        m.request_id &&
        ['pending', 'running'].includes(m.status) &&
        !attemptedResume.current.has(m.request_id),
    )
    if (unfinished?.request_id) {
      attemptedResume.current.add(unfinished.request_id)
      resumeRef.current = unfinished
      mutateQuestion(unfinished.query)
    }
  }, [conversationId, conversation.data, asking, mutateQuestion])
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' })
  }, [messages.length, pending])

  function submit() {
    const value = draft.trim()
    if (!value || ask.isPending || !libraryReady || !canAsk) return
    setDraft('')
    ask.mutate(value)
  }

  function locate(evidence: QaEvidence) {
    const target = documentForEvidence(evidence.source_url, documents) ?? current
    if (target) setDocumentId(target.id)
    setPane('pdf')
    setHighlight((previous) => ({
      quote: evidence.evidence_quote,
      token: (previous?.token ?? 0) + 1,
    }))
  }
  function locateReading(anchor: ReadingNavigation['anchor']) {
    const target = documents.find((item) => item.id === anchor.document_id && item.pdf)
    if (!target || !canLocateReadingAnchor(anchor)) {
      setHighlight(null)
      setMapNotice('这条依据不能可靠定位到原版 PDF，请查看来源记录。')
      return
    }
    setDocumentId(target.id)
    setPane('pdf')
    setMapNotice('')
    setHighlight((previous) => ({ quote: anchor.quote, token: (previous?.token ?? 0) + 1 }))
  }

  if (reader.isError) {
    return (
      <section className="empty-state" role="alert">
        <h1 className="empty-state-title">打不开这次精读</h1>
        <p className="hint">任务不存在，或你没有查看它的权限。</p>
        <Link className="btn btn-secondary" to="/history">
          返回任务记录
        </Link>
      </section>
    )
  }

  const report = detail.data?.report
  return (
    <div className="reader-layout">
      <section className="reader-chat" aria-label="论文对话">
        <header className="reader-head">
          <Link className="reader-back" to={`/runs/${encodeURIComponent(id)}`}>
            <AppIcon name="arrow-left" size={14} aria-hidden="true" />
            返回任务
          </Link>
          <h1 className="reader-title">{current?.title || detail.data?.query || '论文精读'}</h1>
          <p className="reader-verse">一灯一卷，与君细论。</p>
          <p className="hint">
            右侧对照原文，左侧随时提问。回答默认只引用这篇论文，需要时再勾选资料库或联网检索。
          </p>
        </header>

        <div className="reader-thread" aria-live="polite">
          {messages.length === 0 && !pending && (
            <p className="reader-empty hint">
              可以问方法细节、实验设置、结论依据，比如「表 2 的对比基线是怎么选的？」
            </p>
          )}
          {messages.map((message) => (
            <QaMessageView
              key={message.id}
              message={message}
              continuationStatus={
                latestQaContinuation(message.id, conversation.data?.messages ?? [])?.status
              }
              onLocate={locate}
              onReconnect={() => {
                if (!ask.isPending) {
                  resumeRef.current = message
                  ask.mutate(message.query)
                }
              }}
              revisionPending={ask.isPending}
              onStop={
                message.request_id
                  ? () => {
                      const target = conversationId || targetRef.current
                      if (target)
                        stop.mutate({ conversationId: target, requestId: message.request_id! })
                    }
                  : undefined
              }
              stopping={stop.isPending}
              onResume={() => {
                if (
                  !ask.isPending &&
                  message.recovery?.available &&
                  !latestQaContinuation(message.id, conversation.data?.messages ?? [])
                ) {
                  recoveryRef.current = message
                  ask.mutate(message.request_payload?.query ?? message.query)
                }
              }}
              onRevise={() => {
                if (!ask.isPending) {
                  revisionRef.current = message
                  ask.mutate(message.query)
                }
              }}
            />
          ))}
          {pending && (
            <article className="qa-turn is-pending">
              {activeRequestId && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={stop.isPending}
                  onClick={() => {
                    const target = targetRef.current || conversationId
                    if (target) stop.mutate({ conversationId: target, requestId: activeRequestId })
                  }}
                >
                  {stop.isPending ? '正在停止…' : '停止本轮'}
                </button>
              )}
              <div className="qa-question">
                <p>{pending}</p>
              </div>
              <QaStreamingAnswer
                text={streamingAnswer}
                waiting="正在翻阅原文并核验…"
                activity={activity}
                requiresVerification
              />
            </article>
          )}
          {stop.isError && (
            <p role="alert" className="error-text">
              {stop.error instanceof Error ? stop.error.message : '停止请求失败，请重试。'}
            </p>
          )}
          {ask.isError && !cancelledByUser.current && (
            <div className="alert error" role="alert">
              <AppIcon name="circle-x" size={14} aria-hidden="true" />
              {ask.error instanceof Error ? ask.error.message : '提问失败'}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form
          className="reader-composer"
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          <fieldset className="reader-scope">
            <legend>本轮参考</legend>
            <label className="reader-scope-item is-fixed">
              <input type="checkbox" checked disabled />
              本论文
            </label>
            <label className="reader-scope-item">
              <input
                type="checkbox"
                checked={withLibrary}
                disabled={!canAsk || ask.isPending}
                onChange={(event) => setWithLibrary(event.target.checked)}
              />
              资料库
            </label>
            {withLibrary && (
              <select
                className="reader-scope-project"
                aria-label="选择资料库项目"
                value={projectId}
                disabled={!canAsk || ask.isPending}
                onChange={(event) => setProjectId(event.target.value)}
              >
                <option value="">{projectList.length ? '选择项目' : '还没有资料库项目'}</option>
                {projectList.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            )}
            <label className="reader-scope-item">
              <input
                type="checkbox"
                checked={withWeb}
                disabled={!canAsk || ask.isPending}
                onChange={(event) => setWithWeb(event.target.checked)}
              />
              联网检索
            </label>
          </fieldset>
          <label className="visually-hidden" htmlFor="reader-input">
            向这篇论文提问
          </label>
          <textarea
            id="reader-input"
            className="qa-input"
            rows={2}
            maxLength={2000}
            value={draft}
            disabled={!canAsk || ask.isPending}
            placeholder="向这篇论文提问…"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                submit()
              }
            }}
          />
          <div className="qa-composer-foot">
            {!canAsk ? (
              <span className="hint">
                {running
                  ? '论文正在导入，完成后可提问'
                  : paperReady
                    ? '没有可用的论文原文材料，暂时不可提问'
                    : '论文导入未完成，暂时不可提问'}
              </span>
            ) : !libraryReady ? (
              <span className="hint">勾选了资料库，请先选一个项目</span>
            ) : (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setChosen(null)}
                disabled={!conversationId || ask.isPending || !canAsk}
              >
                <AppIcon name="plus" size={14} aria-hidden="true" />
                新对话
              </button>
            )}
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={ask.isPending || !draft.trim() || !libraryReady || !canAsk}
            >
              <AppIcon name="arrow-right" size={15} aria-hidden="true" />
              提问
            </button>
          </div>
        </form>
      </section>

      <section className="reader-pane" aria-label="原文与精读报告">
        <div className="reader-tabs" role="tablist" aria-label="右侧内容">
          <button
            type="button"
            role="tab"
            aria-selected={pane === 'pdf'}
            className={`reader-tab${pane === 'pdf' ? ' is-active' : ''}`}
            onClick={() => setPane('pdf')}
          >
            <AppIcon name="file" size={14} aria-hidden="true" />
            原文
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={pane === 'report'}
            className={`reader-tab${pane === 'report' ? ' is-active' : ''}`}
            onClick={() => setPane('report')}
          >
            <AppIcon name="book" size={14} aria-hidden="true" />
            精读报告
          </button>
          {reportDocument.data?.content_version && (
            <button
              type="button"
              role="tab"
              aria-selected={pane === 'map'}
              className={`reader-tab reader-tab-map${pane === 'map' ? ' is-active' : ''}`}
              onClick={() => {
                setPane('map')
                setMapOpened(true)
                setHighlight(null)
                setMapNotice('')
              }}
            >
              阅读导览
            </button>
          )}
          {pane === 'pdf' && documents.length > 1 && (
            <select
              className="reader-doc-select"
              aria-label="选择论文"
              value={current?.id ?? ''}
              onChange={(event) => {
                setDocumentId(event.target.value)
                setHighlight(null)
              }}
            >
              {documents.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          )}
        </div>

        {mapNotice && (
          <p className="reader-note hint" role="status">
            {mapNotice}
          </p>
        )}

        <div className="reader-pane-body" role="tabpanel" hidden={pane !== 'pdf'} aria-label="原文">
          {!current ? (
            <p className="reader-note hint">
              {reader.isLoading ? '正在读取任务…' : '这次任务没有可查看的论文。'}
            </p>
          ) : current.pdf ? (
            <Suspense
              fallback={
                <p className="pdf-loading" role="status">
                  <AppIcon name="loader" size={15} className="spin" aria-hidden="true" />
                  正在准备阅读器…
                </p>
              }
            >
              <PdfViewer
                key={current.id}
                runId={id}
                documentId={current.id}
                highlight={highlight}
              />
            </Suspense>
          ) : (
            <div className="reader-note">
              <p className="hint">{current.note || '这份论文没有可显示的原版 PDF。'}</p>
              {current.url && (
                <a
                  className="btn btn-secondary btn-sm"
                  href={current.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <AppIcon name="external" size={14} aria-hidden="true" />
                  在原网站打开
                </a>
              )}
            </div>
          )}
        </div>
        <div
          className="reader-pane-body"
          role="tabpanel"
          hidden={pane !== 'report'}
          aria-label="精读报告"
        >
          {pane === 'report' &&
            (report?.markdown ? (
              <div className="reader-report">
                <ReportView
                  bibliography={reportDocument.data?.bibliography}
                  markdown={report.markdown}
                  streaming={false}
                  findings={
                    reportDocument.data?.evidence?.length
                      ? reportEvidenceToFindings(reportDocument.data.evidence)
                      : flattenFindings(detail.data?.results)
                  }
                  citations={report.citations ?? []}
                  finalReview={finalProseReview(detail.data)}
                />
              </div>
            ) : (
              <p className="reader-note hint">
                {running ? '精读报告还在撰写，写好后会出现在这里。' : '这次任务没有生成精读报告。'}
              </p>
            ))}
        </div>
        <div
          className="reader-pane-body"
          role="tabpanel"
          hidden={pane !== 'map'}
          aria-label="阅读导览"
        >
          {mapOpened && reportDocument.data?.content_version && (
            <ReadingMapPanel
              runId={id}
              documentVersion={reportDocument.data.content_version}
              includeHsiTables={includeHsiTables}
              embedded
              active={pane === 'map'}
              initialUnitId={mapInitialUnit}
              onLocate={(anchor) => locateReading(anchor)}
              onClearLocate={() => {
                setHighlight(null)
                setMapNotice('')
              }}
              onRefreshVersion={() => {
                void reportDocument.refetch()
              }}
            />
          )}
        </div>
      </section>
    </div>
  )
}
