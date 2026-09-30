import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AppIcon } from '../components/AppIcon'
import {
  askQuestion,
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
} from '../api/client'
import type { QaMessage } from '../types'

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

function MessageView({ message }: { message: QaMessage }) {
  return (
    <article className="qa-turn" aria-label={`第 ${message.position + 1} 轮问答`}>
      <div className="qa-question">
        <p>{message.query}</p>
      </div>
      <div className="qa-answer-row">
        <span className="qa-avatar" aria-hidden="true">
          <AppIcon name="network" size={14} strokeWidth={2} />
        </span>
        <div className={`qa-answer${message.status === 'fallback' ? ' is-fallback' : ''}`}>
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
              {message.answer}
            </ReactMarkdown>
          </div>
          {message.citations.length > 0 && (
            <div className="qa-sources">
              <span className="qa-sources-title">引用来源</span>
              <ol className="qa-citations" aria-label="引用来源">
                {message.citations.map((url, index) => {
                  const evidence = message.evidence.find((item) => item.source_url === url)
                  return (
                    <li key={url} value={index + 1}>
                      <a href={url} target="_blank" rel="noopener noreferrer">
                        {evidence?.source_reference || evidence?.source_title || url}
                      </a>
                    </li>
                  )
                })}
              </ol>
            </div>
          )}
          {message.thoughts.length > 0 && (
            <details className="qa-thoughts">
              <summary>
                <AppIcon name="chevron-right" size={13} aria-hidden="true" />
                检索与核验过程
              </summary>
              <ol>
                {message.thoughts.map((thought, index) => (
                  <li key={index}>
                    <strong>{THOUGHT_LABEL[thought.tool] ?? thought.tool}</strong>
                    <span>{thought.observation}</span>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </div>
      </div>
    </article>
  )
}

const THOUGHT_LABEL: Record<string, string> = {
  rewrite: '检索式',
  search_and_verify: '检索与逐字核验',
  citation_check: '引用复核',
}

export default function QaPage() {
  const { id } = useParams<{ id?: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const createdId = useRef<string | undefined>(undefined)
  const endRef = useRef<HTMLDivElement>(null)

  const conversations = useQuery({
    queryKey: ['qa-conversations'],
    queryFn: ({ signal }) => listConversations(signal),
  })
  const conversation = useQuery({
    queryKey: ['qa-conversation', id],
    queryFn: ({ signal }) => getConversation(id as string, signal),
    enabled: Boolean(id),
  })

  const ask = useMutation({
    mutationFn: async (text: string) => {
      let target = id || createdId.current
      if (!target) {
        const created = await createConversation(text.slice(0, 60))
        target = created.id
        createdId.current = created.id
      }
      await askQuestion(target, text)
      return target
    },
    onMutate: (text) => setPending(text),
    onError: (_error, text) => setDraft(text),
    onSettled: async (target) => {
      await queryClient.invalidateQueries({ queryKey: ['qa-conversations'] })
      if (target) await queryClient.invalidateQueries({ queryKey: ['qa-conversation', target] })
      setPending(null)
      // The app remounts page content when the pathname changes. Keep the
      // first request and its error state visible until the answer is saved.
      if (target && !id) navigate(`/qa/${target}`, { replace: true })
    },
  })

  const remove = useMutation({
    mutationFn: (target: string) => deleteConversation(target),
    onSuccess: async (_, target) => {
      await queryClient.invalidateQueries({ queryKey: ['qa-conversations'] })
      if (target === id) navigate('/qa')
    },
  })

  const messages = conversation.data?.messages ?? []
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' })
  }, [messages.length, pending])

  function submit(text = draft) {
    const value = text.trim()
    if (!value || ask.isPending) return
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
          onClick={() => navigate('/qa')}
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
          <h1>{conversation.data?.title || '学术问答'}</h1>
          <p className="hint">问概念、找文献、比较研究方法，也可以接着上一轮追问。</p>
        </header>

        <div className={'qa-thread' + (empty ? ' is-empty' : '')} aria-live="polite">
          {empty && (
            <div className="qa-welcome">
              <span className="qa-welcome-kicker" aria-hidden="true">
                Ask the literature
              </span>
              <h2>案头有所疑，且向卷中寻。</h2>
              <p className="hint">选一个示例，或直接写下你的问题。</p>
            </div>
          )}
          {messages.map((message) => (
            <MessageView key={message.id} message={message} />
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
                <div className="qa-answer qa-answer-pending" role="status">
                  <AppIcon name="loader" size={15} className="spin" aria-hidden="true" />
                  正在检索并核验证据…
                </div>
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
          <label className="visually-hidden" htmlFor="qa-input">
            输入问题
          </label>
          <textarea
            id="qa-input"
            className="qa-input"
            rows={2}
            value={draft}
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
            <span className="hint">Enter 发送，Shift + Enter 换行</span>
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={ask.isPending || !draft.trim()}
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

        {empty && (
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
