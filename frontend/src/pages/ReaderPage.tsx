import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import {
  askQuestion,
  createConversation,
  getConversation,
  getReader,
  listConversations,
} from '../api/client'
import { RequestTimeoutError } from '../api/transport'
import { AppIcon } from '../components/AppIcon'
import QaMessageView from '../components/QaMessage'
import ReportView from '../components/ReportView'
import type { PdfHighlight } from '../components/PdfViewer'
import { useProjects } from '../hooks/useLibrary'
import { useRunDetail } from '../hooks/useRuns'
import { flattenFindings } from '../lib/evidence'
import { documentForEvidence } from '../lib/readerDocuments'
import { recoverTimedOutAnswer } from '../lib/qaRecovery'
import type { QaEvidence, QaSourceOption } from '../types'

// PDF.js 体积较大，只在打开原文时加载
const PdfViewer = lazy(() => import('../components/PdfViewer'))

function isActive(status: string | undefined): boolean {
  return status === 'pending' || status === 'running' || status === 'cancelling'
}

export default function ReaderPage() {
  const { id = '' } = useParams<{ id: string }>()
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
  })

  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [withLibrary, setWithLibrary] = useState(false)
  const [projectId, setProjectId] = useState('')
  const [withWeb, setWithWeb] = useState(false)
  const [pane, setPane] = useState<'pdf' | 'report'>('pdf')
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
  const canAsk = reader.data?.status === 'done' && reader.data.can_ask !== false

  const ask = useMutation({
    mutationFn: async (text: string) => {
      let target = conversationId
      let baselineCount = 0
      if (!target) {
        target = (await createConversation(text.slice(0, 60), id)).id
        setChosen(target)
      } else {
        // Cache data may still be loading or predate another tab's answer.
        // Freeze the durable history before sending a repeated question.
        baselineCount = (await getConversation(target)).messages.length
      }
      const sources: QaSourceOption[] = []
      if (withLibrary) sources.push('library')
      if (withWeb) sources.push('web')
      try {
        await askQuestion(target, text, undefined, {
          sources,
          projectId: withLibrary ? projectId : undefined,
        })
      } catch (error) {
        if (!(error instanceof RequestTimeoutError)) throw error
        const recovered = await recoverTimedOutAnswer(target, text, baselineCount)
        if (!recovered) throw error
      }
      return target
    },
    onMutate: (text) => setPending(text),
    onError: (_error, text) => setDraft(text),
    onSettled: async (target) => {
      await queryClient.invalidateQueries({ queryKey: ['qa-conversations', 'run', id] })
      if (target) await queryClient.invalidateQueries({ queryKey: ['qa-conversation', target] })
      setPending(null)
    },
  })

  const messages = conversation.data?.messages ?? []
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
    setHighlight({ quote: evidence.evidence_quote, token: Date.now() })
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
            <QaMessageView key={message.id} message={message} onLocate={locate} />
          ))}
          {pending && (
            <article className="qa-turn is-pending">
              <div className="qa-question">
                <p>{pending}</p>
              </div>
              <div className="qa-answer qa-answer-pending" role="status">
                <AppIcon name="loader" size={15} className="spin" aria-hidden="true" />
                正在翻阅原文并核验…
              </div>
            </article>
          )}
          {ask.isError && (
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
                  : reader.data?.status === 'done'
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

        <div className="reader-pane-body" role="tabpanel">
          {pane === 'pdf' ? (
            !current ? (
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
            )
          ) : report?.markdown ? (
            <div className="reader-report">
              <ReportView
                markdown={report.markdown}
                streaming={false}
                findings={flattenFindings(detail.data?.results)}
                citations={report.citations ?? []}
              />
            </div>
          ) : (
            <p className="reader-note hint">
              {running ? '精读报告还在撰写，写好后会出现在这里。' : '这次任务没有生成精读报告。'}
            </p>
          )}
        </div>
      </section>
    </div>
  )
}
