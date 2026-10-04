import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'
import QaMessageView from '../components/QaMessage'
import QaStreamingAnswer from '../components/QaStreamingAnswer'
import {
  createConversation,
  deleteConversation,
  getConversation,
  getReader,
  listConversations,
} from '../api/client'
import { useProjects } from '../hooks/useLibrary'
import {
  pendingConversationId,
  pendingQaId,
  reconcileQaRequests,
  runQaRequest,
} from '../lib/qaRequest'
import { appendQaActivity } from '../lib/qaActivity'
import type { QaActivity, QaMessage } from '../types'

const STARTERS = [
  { tag: '文献检索', text: '查找 DOE 光谱成像系统误差补偿的最新文献' },
  { tag: '评价指标', text: 'CASSI 重建中常用的评价指标有哪些？' },
  { tag: '方法梳理', text: 'Zernike 系数盲估计有哪些代表性方法？' },
  { tag: '前沿进展', text: '深度展开网络在快照光谱成像中的最新进展' },
  { tag: '对比分析', text: '比较 SD-CASSI 与 DD-CASSI 的系统结构差异' },
  { tag: '方法梳理', text: '高光谱图像去噪有哪些基于深度先验的方法？' },
]

/** 阅读提示与回答内的核验记录分工：这里说明如何使用和判断回答。 */
const STEPS = [
  { title: '查看出处', text: '点击引用，回到原文核对结论与适用条件。' },
  { title: '继续追问', text: '补充论文、方法或实验条件，让问题更具体。' },
  { title: '留意证据不足', text: '未找到支持材料时，回答会说明局限。' },
]

export default function QaPage() {
  const { id } = useParams<{ id?: string }>()
  const [params] = useSearchParams()
  const requestedRunId = id ? undefined : params.get('run') || undefined
  return (
    <QaWorkspace
      key={`${id ?? ''}/${requestedRunId ?? ''}`}
      id={id}
      requestedRunId={requestedRunId}
    />
  )
}

function QaWorkspace({ id, requestedRunId }: { id?: string; requestedRunId?: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [streamingAnswer, setStreamingAnswer] = useState('')
  const [activity, setActivity] = useState<QaActivity[]>([])
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null)
  const resumeRef = useRef<QaMessage | null>(null)
  const revisionRef = useRef<QaMessage | null>(null)
  const targetRef = useRef<string>()
  const attemptedResume = useRef(new Set<string>())
  const connection = useRef<AbortController | null>(null)
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
  const createdId = useRef<string | undefined>(undefined)
  const endRef = useRef<HTMLDivElement>(null)
  const projects = useProjects()

  const conversation = useQuery({
    queryKey: ['qa-conversation', id],
    queryFn: ({ signal }) => getConversation(id as string, signal),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      query.state.data?.messages.some((m) => ['pending', 'running'].includes(m.status))
        ? 5000
        : false,
  })
  const runId = id ? conversation.data?.run_id || undefined : requestedRunId
  const conversations = useQuery({
    queryKey: ['qa-conversations', runId ?? null],
    queryFn: ({ signal }) => listConversations(signal, runId),
    enabled: !id || Boolean(conversation.data),
  })
  const taskScope = useQuery({
    queryKey: ['reader', runId],
    queryFn: ({ signal }) => getReader(runId as string, signal),
    enabled: Boolean(runId),
  })
  const canAsk = (!id || Boolean(conversation.data)) && (!runId || taskScope.data?.can_ask === true)
  const newConversationPath = runId
    ? `/qa?${new URLSearchParams({ run: runId }).toString()}`
    : '/qa'
  const scopeNotice = !runId
    ? ''
    : taskScope.isError
      ? '无法读取任务材料，请刷新后重试。'
      : !taskScope.data
        ? '正在读取任务材料…'
        : taskScope.data.can_ask
          ? ''
          : ['pending', 'running', 'cancelling'].includes(taskScope.data.status)
            ? '任务尚未完成，完成后可基于材料提问。'
            : '这次任务暂无可回查的原文材料，暂时不能提问。'

  const ask = useMutation({
    mutationFn: async (text: string) => {
      const controller = new AbortController()
      connection.current = controller
      let target = id || createdId.current
      const resume = resumeRef.current
      resumeRef.current = null
      const revision = revisionRef.current
      revisionRef.current = null
      if (!target) {
        const created = runId
          ? await createConversation(text.slice(0, 60), runId)
          : await createConversation(text.slice(0, 60))
        controller.signal.throwIfAborted()
        target = created.id
        createdId.current = created.id
      }
      targetRef.current = target
      const sources: ('library' | 'web')[] = []
      if (withLibrary) sources.push('library')
      if (withWeb) sources.push('web')
      const revisionMessageId = revision?.id ?? resume?.request_payload?.revision_message_id
      const scope = revisionMessageId
        ? { sources: [], projectId: undefined, revisionMessageId }
        : resume
          ? {
              sources: resume.request_payload?.sources ?? [],
              projectId: resume.request_payload?.project_id ?? undefined,
            }
          : { sources, projectId: withLibrary ? projectId : undefined }
      const requestId = pendingQaId(target, text, scope, resume?.request_id ?? undefined)
      attemptedResume.current.add(requestId)
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
      setPending(text)
      setStreamingAnswer('')
      setActivity([])
    },
    onError: (_error, text) => {
      if (mounted.current) setDraft(text)
    },
    onSettled: async (target) => {
      await queryClient.invalidateQueries({ queryKey: ['qa-conversations'] })
      if (target ?? targetRef.current)
        await queryClient.invalidateQueries({
          queryKey: ['qa-conversation', target ?? targetRef.current],
        })
      if (!mounted.current) return
      setPending(null)
      setActiveRequestId(null)
      setStreamingAnswer('')
      // The app remounts page content when the pathname changes. Keep the
      // first request and its error state visible until the answer is saved.
      if (target && !id) navigate(`/qa/${target}`, { replace: true })
    },
  })

  const remove = useMutation({
    mutationFn: (target: string) => deleteConversation(target),
    onSuccess: async (_, target) => {
      await queryClient.invalidateQueries({ queryKey: ['qa-conversations'] })
      if (target === id) navigate(newConversationPath)
    },
  })

  useEffect(() => {
    if (id || createdId.current || ask.isPending) return
    const pendingId = pendingConversationId((conversations.data ?? []).map((item) => item.id))
    if (pendingId) navigate(`/qa/${pendingId}`, { replace: true })
  }, [id, conversations.data, ask.isPending, navigate])

  const messages = (conversation.data?.messages ?? []).filter(
    (m) => !activeRequestId || m.request_id !== activeRequestId,
  )
  const asking = ask.isPending
  const mutateQuestion = ask.mutate
  useEffect(() => {
    if (id && conversation.data) reconcileQaRequests(id, conversation.data.messages)
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
  }, [id, conversation.data, asking, mutateQuestion])
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' })
  }, [messages.length, pending])

  function submit(text = draft) {
    const value = text.trim()
    if (!value || ask.isPending || !canAsk || (withLibrary && !projectId)) return
    setDraft('')
    ask.mutate(value)
  }

  const empty = !id && messages.length === 0 && !pending
  // 本会话所有回答引用过的来源，按首次出现去重
  const sources = [...new Set(messages.flatMap((message) => message.citations))].map((url) => {
    const evidence = messages
      .flatMap((message) => message.evidence)
      .find((item) => item.source_url === url)
    return { url, label: evidence?.source_reference || evidence?.source_title || url }
  })

  return (
    <div className="qa-layout">
      <aside className="qa-sidebar" aria-label="问答会话">
        <button
          type="button"
          className="btn btn-secondary btn-block"
          onClick={() => navigate(newConversationPath)}
        >
          <AppIcon name="plus" size={15} aria-hidden="true" /> 新会话
        </button>
        <span className="qa-sidebar-title">历史会话</span>
        <ul className="qa-conversation-list">
          {(conversations.data ?? []).map((item) => (
            <li key={item.id} className={item.id === id ? 'is-active' : ''}>
              <button
                type="button"
                className="qa-conversation-open"
                onClick={() => navigate(`/qa/${item.id}`)}
              >
                <span className="qa-conversation-title">{item.title || '未命名会话'}</span>
                <span className="qa-conversation-meta">{item.message_count} 轮</span>
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm icon-button qa-conversation-delete"
                aria-label={`删除会话 ${item.title}`}
                title="删除会话"
                onClick={() => remove.mutate(item.id)}
              >
                <AppIcon name="trash" size={13} aria-hidden="true" />
              </button>
            </li>
          ))}
          {conversations.data?.length === 0 && <li className="qa-empty-list">还没有问答会话</li>}
        </ul>
      </aside>

      <section className={'qa-main' + (empty ? ' is-empty' : '')} aria-label="学术问答">
        <header className="qa-header">
          <h1>{conversation.data?.title || (runId ? '任务追问' : '学术问答')}</h1>
          {runId ? (
            <p className="hint">
              基于本次任务的原文与已核验发现继续提问。{' '}
              <Link to={`/runs/${encodeURIComponent(runId)}`}>返回任务</Link>
            </p>
          ) : (
            <p className="hint">问概念、找文献、比较研究方法，也可以接着上一轮追问。</p>
          )}
        </header>

        <div className={'qa-thread' + (empty ? ' is-empty' : '')} aria-live="polite">
          {empty && !runId && (
            <div className="qa-welcome">
              <span className="qa-welcome-kicker" aria-hidden="true">
                Ask the literature
              </span>
              <h2>案头有所疑，且向卷中寻。</h2>
              <p className="hint">选一个示例，或直接写下你的问题。</p>
            </div>
          )}
          {scopeNotice && (
            <p className="hint" role="status">
              {scopeNotice}
            </p>
          )}
          {messages.map((message) => (
            <QaMessageView
              key={message.id}
              message={message}
              onReconnect={() => {
                if (!ask.isPending) {
                  resumeRef.current = message
                  ask.mutate(message.query)
                }
              }}
              revisionPending={ask.isPending}
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
              <div className="qa-question">
                <p>{pending}</p>
              </div>
              <div className="qa-answer-row">
                <span className="qa-avatar" aria-hidden="true">
                  <AppIcon name="network" size={14} strokeWidth={2} />
                </span>
                <QaStreamingAnswer
                  text={streamingAnswer}
                  activity={activity}
                  waiting={withLibrary || withWeb ? '正在检索并核验证据…' : '正在生成回答…'}
                />
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
          className="qa-composer"
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          <fieldset className="qa-scope" aria-describedby="qa-scope-hint">
            <legend className="visually-hidden">本轮参考</legend>
            <div className="qa-scope-options">
              <span className="qa-scope-title" aria-hidden="true">
                本轮参考
              </span>
              {runId && <span className="qa-scope-option">任务材料</span>}
              <label className="qa-scope-toggle">
                <input
                  className="visually-hidden"
                  type="checkbox"
                  checked={withLibrary}
                  onChange={(event) => setWithLibrary(event.target.checked)}
                />
                <span className="qa-scope-option">
                  <AppIcon name="library" size={15} aria-hidden="true" />
                  私有知识库
                  <AppIcon
                    name={withLibrary ? 'check' : 'plus'}
                    size={13}
                    className="qa-scope-indicator"
                    aria-hidden="true"
                  />
                </span>
              </label>
              <label className="qa-scope-toggle">
                <input
                  className="visually-hidden"
                  type="checkbox"
                  checked={withWeb}
                  onChange={(event) => setWithWeb(event.target.checked)}
                />
                <span className="qa-scope-option">
                  <AppIcon name="search" size={15} aria-hidden="true" />
                  联网检索
                  <AppIcon
                    name={withWeb ? 'check' : 'plus'}
                    size={13}
                    className="qa-scope-indicator"
                    aria-hidden="true"
                  />
                </span>
              </label>
            </div>
            {withLibrary && (
              <div className="qa-scope-project">
                <label htmlFor="qa-scope-project">知识库项目</label>
                <select
                  id="qa-scope-project"
                  aria-label="选择知识库项目"
                  value={projectId}
                  onChange={(event) => setProjectId(event.target.value)}
                >
                  <option value="">
                    {projects.data?.length ? '选择项目' : '还没有知识库项目'}
                  </option>
                  {(projects.data ?? []).map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <p id="qa-scope-hint" className="qa-scope-hint">
              {runId
                ? '默认使用本次任务材料；可按需添加知识库或联网来源。'
                : '按需添加参考来源；未选择时，使用模型自身知识回答。'}
            </p>
          </fieldset>
          <label className="visually-hidden" htmlFor="qa-input">
            输入问题
          </label>
          <textarea
            id="qa-input"
            className="qa-input"
            rows={2}
            value={draft}
            disabled={!canAsk}
            placeholder={
              messages.length ? '继续追问，例如「第二篇的实验设置是什么？」' : '输入学术问题…'
            }
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                submit()
              }
            }}
            maxLength={2000}
          />
          <div className="qa-composer-foot">
            <span className="hint">
              {withLibrary && !projectId
                ? '勾选知识库后请选择项目'
                : 'Enter 发送，Shift + Enter 换行'}
            </span>
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={ask.isPending || !canAsk || !draft.trim() || (withLibrary && !projectId)}
            >
              <AppIcon
                name={ask.isPending ? 'loader' : 'arrow-right'}
                size={15}
                className={ask.isPending ? 'spin' : ''}
                aria-hidden="true"
              />
              提问
            </button>
          </div>
        </form>

        {empty && !runId && (
          <div className="qa-starters" aria-label="示例问题">
            {STARTERS.map((starter) => (
              <button
                type="button"
                key={starter.text}
                className="qa-starter"
                onClick={() => submit(starter.text)}
              >
                <span className="qa-starter-tag">{starter.tag}</span>
                <span className="qa-starter-text">{starter.text}</span>
                <AppIcon name="arrow-up-right" size={14} aria-hidden="true" />
              </button>
            ))}
          </div>
        )}
      </section>

      <aside className="qa-context" aria-label="问答说明与引用">
        <section className="qa-context-block">
          <h2 className="qa-context-title">
            本会话引用
            {sources.length > 0 && <span className="qa-context-count">{sources.length}</span>}
          </h2>
          {sources.length ? (
            <ol className="qa-context-sources">
              {sources.map((source) => (
                <li key={source.url}>
                  <a href={source.url} target="_blank" rel="noopener noreferrer">
                    {source.label}
                  </a>
                </li>
              ))}
            </ol>
          ) : (
            <p className="hint">回答引用过的文献会汇总在这里，方便整体回看。</p>
          )}
        </section>
        <section className="qa-context-block">
          <h2 className="qa-context-title">阅读提示</h2>
          <ol className="qa-context-steps">
            {STEPS.map((step) => (
              <li key={step.title}>
                <strong>{step.title}</strong>
                <span>{step.text}</span>
              </li>
            ))}
          </ol>
        </section>
        <p className="qa-context-motto" aria-hidden="true">
          Every claim,
          <br />
          traced to its source.
        </p>
      </aside>
    </div>
  )
}
