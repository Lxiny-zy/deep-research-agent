import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'
import ClarifyDialog from '../components/ClarifyDialog'
import SettingsPanel from '../components/SettingsPanel'
import ResourcePreflightPanel from '../components/ResourcePreflightPanel'
import TaskTemplatePicker from '../components/TaskTemplatePicker'
import ContractPreview from '../components/ContractPreview'
import StrategySelector from '../components/StrategySelector'
import AttachmentDropzone from '../components/AttachmentDropzone'
import { useAttachments } from '../hooks/useAttachments'
import TierSelector from '../components/TierSelector'
import { useContractPreview, useTemplates, useTiers, useUsage } from '../hooks/useWorkbench'
import type { StrategyKey, TierKey } from '../types'
import { useConfig } from '../hooks/useConfig'
import { useResearchDraft } from '../hooks/useResearchDraft'
import { useProjects } from '../hooks/useLibrary'
import { assessIntent, createRun, listWorkflows } from '../api/client'
import { advance, emptySlots, isSkip, type ClarifyState } from '../lib/clarification'
import { clearThread, loadThread } from '../lib/conversation'
import {
  BUILTIN_TEMPLATE_META,
  isDefaultWorkflow,
  isUserFacingWorkflow,
} from '../lib/workflowTemplates'
import type { ConversationTurn, WorkflowInfo } from '../types'

const DEFAULT_QUERY = '快照式高光谱成像中，深度展开网络相比端到端网络的优势与局限是什么？'
const SAMPLES = [
  'CASSI 系统中编码孔径失配对重建质量的影响有哪些量化研究？',
  '2024 年以来扩散模型用于光谱重建的代表工作与主要瓶颈',
  '衍射光学元件（DOE）端到端联合设计的主流优化方法对比',
]

function readText(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file, 'utf-8')
  })
}

export default function NewResearchPage() {
  const [searchParams] = useSearchParams()
  return <ResearchComposer key={searchParams.get('followup') === '1' ? 'followup' : 'new'} />
}

function ResearchComposer() {
  const navigate = useNavigate()
  const { data: config } = useConfig()
  const [searchParams] = useSearchParams()
  const isFollowUp = searchParams.get('followup') === '1'
  const [thread, setThread] = useState<ConversationTurn[]>(() => loadThread())
  const [draftContext] = useState(() => (isFollowUp ? JSON.stringify(thread) : ''))
  const draft = useResearchDraft(isFollowUp ? '' : DEFAULT_QUERY, draftContext)
  const { query, params, workflow, project_id: projectId } = draft
  const projects = useProjects()
  const templates = useTemplates()
  const [templateKey, setTemplateKey] = useState(
    () => searchParams.get('template') || 'autoResearch',
  )
  const activeTemplate = templates.data?.find((item) => item.key === templateKey)
  // 课题调研允许在「高级」里改用自定义流程；其它任务由「任务 + 检索策略」决定流程。
  const templateOwnsWorkflow = Boolean(activeTemplate && activeTemplate.key !== 'autoResearch')
  const strategies = activeTemplate?.strategies ?? []
  // 检索策略按任务记忆：切换任务时回到该任务的默认策略。
  const [strategyChoice, setStrategyChoice] = useState<{
    template: string
    key: StrategyKey
  } | null>(null)
  const strategy: StrategyKey | null =
    strategyChoice?.template === templateKey
      ? strategyChoice.key
      : (activeTemplate?.default_strategy ?? null)
  const contract = useContractPreview(templateKey, query, templateOwnsWorkflow, strategy)
  // 课题调研选了「快速检索 / 深度检索」时由策略决定流程；只有用户在高级里
  // 选了自定义流程（非内置 deep/quick）才以它为准。
  const customWorkflow =
    !templateOwnsWorkflow && workflow && workflow !== 'deep' && workflow !== 'quick'
      ? workflow
      : null
  const strategyWorkflow = strategies.find((item) => item.key === strategy)?.workflow
  const tiers = useTiers()
  const usage = useUsage()
  // 档位默认跟随任务模板（精读/评审偏标准，综述偏深度）；用户手动选过后不再被模板覆盖。
  const [tierChoice, setTierChoice] = useState<TierKey | null>(null)
  const tier: TierKey = tierChoice ?? activeTemplate?.tier_default ?? 'standard'
  // 数据分析可以上传 CSV / TSV 文件：文件内容作为独立的 dataset 字段提交，
  // 输入框里只写分析问题。只在浏览器内读取，不经过其它服务。
  const [dataset, setDataset] = useState<{ name: string; text: string } | null>(null)
  // 任务附件：选择即上传解析，模型会在检索前先阅读这些文件
  const attachments = useAttachments()
  const [datasetError, setDatasetError] = useState<string | null>(null)
  async function pickDataset(file: File | undefined) {
    setDatasetError(null)
    if (!file) return setDataset(null)
    if (file.size > 2_000_000) return setDatasetError('文件超过 2 MB，请先抽样或聚合后再上传')
    try {
      const text = await readText(file)
      if (!text.trim()) return setDatasetError('文件为空')
      setDataset({ name: file.name, text })
    } catch {
      setDatasetError('无法读取文件，请确认是 UTF-8 编码的文本表格')
    }
  }
  const draftRef = useRef(draft)
  draftRef.current = draft
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [phase, setPhase] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [clarify, setClarify] = useState<ClarifyState | null>(null)
  const busy = submitting || clarify !== null
  const requestRef = useRef<AbortController | null>(null)
  useEffect(() => () => requestRef.current?.abort(), [])
  function beginRequest() {
    requestRef.current?.abort()
    requestRef.current = new AbortController()
  }

  useEffect(() => {
    let stale = false
    const preferred = searchParams.get('workflow')
    listWorkflows()
      .then((items) => {
        if (stale) return
        const visible = items.filter(isUserFacingWorkflow)
        setWorkflows(visible)
        const selected =
          visible.find((item) => item.name === preferred) ??
          visible.find((item) => item.name === draftRef.current.workflow) ??
          visible.find(isDefaultWorkflow) ??
          visible[0]
        if (selected) draftRef.current.update({ workflow: selected.name }, false)
      })
      .catch(() => {})
    return () => {
      stale = true
    }
  }, [searchParams])

  function dropThread() {
    clearThread()
    setThread([])
    if (isFollowUp) {
      draft.discard()
      navigate('/', { replace: true })
    }
  }

  async function launch(finalQuery: string, clarified = false) {
    setPhase('正在创建研究任务')
    const hasParams = Object.values(params).some((item) => item != null)
    const { run_id } = await createRun(
      {
        query: finalQuery,
        params: hasParams ? params : null,
        workflow: customWorkflow,
        template: activeTemplate ? activeTemplate.key : null,
        strategy: customWorkflow ? null : strategy,
        dataset: activeTemplate?.input_kind === 'dataset' && dataset ? dataset.text : null,
        tier,
        project_id: projectId || null,
        history: thread,
        clarified,
        attachments: attachments.payloads,
      },
      requestRef.current?.signal,
      JSON.stringify({
        query,
        params,
        workflow,
        projectId,
        thread,
        templateKey,
        tier,
        dataset: dataset?.name ?? null,
        attachments: attachments.payloads.map((item) => item.id),
      }),
    )
    if (requestRef.current?.signal.aborted) return
    draft.discard()
    navigate('/runs/' + run_id)
  }

  async function step(state: ClarifyState, allowFallback = false) {
    setPhase('正在确认研究范围')
    // Only assessment failures fall back; failed creation must not trigger another request.
    const verdict = await assessIntent(
      {
        query: state.query,
        answers: state.answers,
        round: state.round,
        history: thread,
      },
      requestRef.current?.signal,
    ).catch((cause: unknown) => {
      if (requestRef.current?.signal.aborted) throw cause
      if (!allowFallback) throw cause
      return null
    })
    if (requestRef.current?.signal.aborted) return
    if (!verdict || verdict.ready) {
      await launch(verdict?.resolved_query || state.query, Boolean(verdict))
      return
    }
    setClarify({ ...state, question: verdict.question, options: verdict.options, gap: verdict.gap })
    setSubmitting(false)
  }

  function failed(cause: unknown) {
    if (requestRef.current?.signal.aborted) return
    setError(cause instanceof Error ? cause.message : '提交失败，请重试')
    setSubmitting(false)
  }

  async function start() {
    const value = query.trim()
    if (!value || busy || attachments.uploading) return
    beginRequest()
    setSubmitting(true)
    setError(null)
    try {
      if (templateOwnsWorkflow) {
        // 专项任务（评审 / 精读 / 数据分析 / PPT / 导图）的输入是论文链接或数据表，
        // 意图澄清的问句（「你关心哪些方面？」）对它们没有意义，直接按模板契约创建。
        await launch(value, true)
        return
      }
      await step(
        { query: value, round: 0, answers: emptySlots(), question: '', options: [], gap: 'none' },
        true,
      )
    } catch (cause) {
      failed(cause)
    }
  }

  async function answerClarification(answer: string) {
    if (!clarify || submitting) return
    beginRequest()
    if (isSkip(answer)) {
      await skipClarification()
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await step(advance(clarify, answer))
    } catch (cause) {
      failed(cause)
    }
  }

  async function skipClarification() {
    if (!clarify || submitting) return
    beginRequest()
    setSubmitting(true)
    setError(null)
    setPhase('正在确认研究范围')
    const verdict = await assessIntent(
      {
        query: clarify.query,
        answers: clarify.answers,
        round: clarify.round,
        history: thread,
        skip: true,
      },
      requestRef.current?.signal,
    ).catch(() => null)
    if (requestRef.current?.signal.aborted) return
    try {
      await launch(verdict?.resolved_query || clarify.query, true)
    } catch (cause) {
      failed(cause)
    }
  }

  const activeWorkflow = workflows.find((item) => item.name === workflow)
  const draftLabel = {
    idle: '',
    restored: '已恢复上次草稿',
    saving: '正在保存草稿…',
    saved: '草稿已保存到此浏览器',
    unavailable: '草稿未能保存，请保持当前页面',
    cleared: '草稿已清除',
  }[draft.status]

  const isResearch = !templateOwnsWorkflow
  const submitLabel = submitting
    ? '提交中…'
    : templateOwnsWorkflow && activeTemplate
      ? `开始${activeTemplate.title}`
      : '开始研究'
  const statusText = submitting
    ? phase
    : clarify
      ? '等待补充研究范围'
      : attachments.uploading
        ? '正在解析附件…'
        : query.trim()
          ? `将以「${activeTemplate?.title ?? '课题调研'}」方式执行` +
            (attachments.payloads.length ? `，附 ${attachments.payloads.length} 个文件` : '')
          : '输入内容后即可开始'

  return (
    <div className="home-page">
      <header className="page-header home-hero">
        <div className="home-hero-copy">
          <span className="home-hero-kicker" aria-hidden="true">
            From questions
            <br />
            to a brighter
            <br />
            tomorrow.
          </span>
          <h1 className="home-hero-title">
            <span className="visually-hidden">Science Research 科研工作台：</span>
            <span className="home-hero-word" aria-hidden="true">
              Science
            </span>
            <span className="home-hero-word is-spectral" aria-hidden="true">
              Research
            </span>
            <span className="home-hero-question">今天想研究什么？</span>
          </h1>
          <p className="home-hero-sub">探索未知，让思想穿越时空，与伟大的智慧相遇。</p>
        </div>
        <div className="home-hero-aside" aria-hidden="true">
          <span>Knowledge drives human progress</span>
          <span>Spectrum of knowledge</span>
        </div>
        {draft.status !== 'idle' && (
          <div className={'home-draft is-' + draft.status}>
            <span role="status">
              <AppIcon
                name={draft.status === 'unavailable' ? 'alert' : 'save'}
                size={14}
                aria-hidden="true"
              />
              {draftLabel}
            </span>
            {draft.canUndo && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={busy}
                onClick={draft.restore}
              >
                <AppIcon name="undo" size={14} aria-hidden="true" />
                撤销清除
              </button>
            )}
            {query.trim() && (
              <button
                type="button"
                className="btn btn-ghost btn-sm icon-button"
                disabled={busy}
                title="清除草稿"
                aria-label="清除草稿"
                onClick={draft.clear}
              >
                <AppIcon name="trash" size={14} aria-hidden="true" />
              </button>
            )}
            {draft.status === 'unavailable' && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={draft.retry}>
                重试保存
              </button>
            )}
          </div>
        )}
      </header>

      <fieldset className="home-layout" disabled={busy}>
        <div className="research-composer-body home-main">
          {thread.length > 0 && (
            <section className="home-card home-thread" data-testid="thread-context">
              <div className="home-card-head">
                <span className="home-card-title">
                  <AppIcon name="history" size={15} aria-hidden="true" />
                  追问上下文（{thread.length} 轮）
                </span>
                <button type="button" className="btn btn-ghost btn-sm" onClick={dropThread}>
                  开始新话题
                </button>
              </div>
              <ol className="home-thread-list">
                {thread.map((turn, index) => (
                  <li key={index}>
                    <span className="home-thread-index">{index + 1}</span>
                    <span>{turn.query}</span>
                  </li>
                ))}
              </ol>
              <p className="hint">本次提问会带上以上轮次。</p>
            </section>
          )}

          {templates.data && templates.data.length > 1 && thread.length === 0 && (
            <TaskTemplatePicker
              templates={templates.data}
              value={templateKey}
              disabled={busy}
              onChange={(key) => {
                setTemplateKey(key)
                // 默认示例问题只适合课题调研；切到专项任务时换成空输入，显示该任务的输入提示
                if (query === DEFAULT_QUERY && key !== 'autoResearch')
                  draft.update({ query: '' }, false)
              }}
            />
          )}

          <section className="home-card home-input">
            <div className="home-section-head">
              <label className="home-input-label" htmlFor="query">
                {activeTemplate?.input_label ?? '研究问题'}
              </label>
              <span className="home-section-motto" aria-hidden="true">
                Good research begins with a better question.
              </span>
            </div>
            <textarea
              id="query"
              className="input textarea home-query"
              rows={activeTemplate?.input_kind === 'dataset' ? 9 : 5}
              value={query}
              onChange={(event) => draft.update({ query: event.target.value })}
              spellCheck={activeTemplate?.input_kind !== 'dataset'}
              placeholder={
                thread.length
                  ? '接着上文追问，例如「那第二个呢」…'
                  : (activeTemplate?.input_placeholder ?? '输入一个值得深挖的问题…')
              }
            />
            {activeTemplate?.input_kind === 'dataset' && (
              <div className="home-dataset">
                <label className="btn btn-secondary btn-sm" htmlFor="dataset-file">
                  <AppIcon name="database" size={14} aria-hidden="true" />
                  {dataset ? '更换数据文件' : '上传 CSV / TSV'}
                </label>
                <input
                  id="dataset-file"
                  type="file"
                  accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values"
                  className="visually-hidden"
                  onChange={(event) => void pickDataset(event.target.files?.[0])}
                />
                {dataset ? (
                  <span className="home-dataset-file">
                    <AppIcon name="file" size={14} aria-hidden="true" />
                    已选择 {dataset.name}（约 {Math.max(0, dataset.text.split('\n').length - 1)}{' '}
                    行）
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm icon-button"
                      aria-label="移除数据文件"
                      onClick={() => setDataset(null)}
                    >
                      <AppIcon name="x" size={13} aria-hidden="true" />
                    </button>
                  </span>
                ) : (
                  <span className="hint">也可以直接把表格粘贴到上方输入框，第一行写分析问题。</span>
                )}
                {datasetError && (
                  <span className="error-text" role="alert">
                    {datasetError}
                  </span>
                )}
              </div>
            )}
            <div className="home-attach">
              <span className="home-attach-title">
                添加附件
                <span className="hint">模型会先阅读这些文件，引用时标注文件与页码 / 章节</span>
              </span>
              <AttachmentDropzone
                items={attachments.items}
                onAdd={attachments.add}
                onRemove={attachments.remove}
                disabled={busy}
                full={attachments.full}
              />
            </div>
            {(() => {
              const examples =
                templateOwnsWorkflow && activeTemplate ? activeTemplate.examples : SAMPLES
              if (!examples.length || thread.length > 0) return null
              return (
                <div className="home-examples" aria-label="示例输入">
                  <span className="home-examples-title">示例问题</span>
                  {examples.map((example) => (
                    <button
                      type="button"
                      key={example}
                      className="home-example"
                      onClick={() => draft.update({ query: example })}
                    >
                      {example.length > 56 ? example.slice(0, 56) + '…' : example}
                      <AppIcon name="chevron-right" size={13} aria-hidden="true" />
                    </button>
                  ))}
                </div>
              )
            })()}
          </section>

          {templateOwnsWorkflow && activeTemplate && (
            <ContractPreview
              template={activeTemplate}
              contract={contract.data}
              loading={contract.isFetching}
              error={contract.error}
              uploadedDataset={dataset?.name}
            />
          )}

          {clarify && (
            <div className="home-clarify">
              <ClarifyDialog
                question={clarify.question}
                options={clarify.options}
                round={clarify.round}
                busy={submitting}
                onAnswer={answerClarification}
                onSkip={skipClarification}
              />
              <button
                className="btn btn-ghost btn-sm"
                type="button"
                disabled={submitting}
                onClick={() => {
                  setClarify(null)
                  setError(null)
                  requestAnimationFrame(() => document.getElementById('query')?.focus())
                }}
              >
                <AppIcon name="edit" size={14} aria-hidden="true" />
                修改问题
              </button>
            </div>
          )}
        </div>

        <aside className="home-config" aria-label="研究配置">
          <div className="home-config-head">任务配置</div>
          {strategies.length > 0 && !customWorkflow && (
            <StrategySelector
              strategies={strategies}
              value={strategy ?? strategies[0].key}
              onChange={(key) => setStrategyChoice({ template: templateKey, key })}
              disabled={busy}
            />
          )}
          {tiers.data && tiers.data.length > 0 && (
            <TierSelector
              tiers={tiers.data}
              value={tier}
              onChange={setTierChoice}
              usage={usage.data}
              disabled={busy}
            />
          )}
          {projects.data && projects.data.length > 0 && (
            <label className="field-label" htmlFor="research-project">
              资料库项目
              <span className="select-with-icon">
                <AppIcon name="library" size={15} aria-hidden="true" />
                <select
                  id="research-project"
                  className="input"
                  value={projectId}
                  onChange={(event) => draft.update({ project_id: event.target.value })}
                >
                  <option value="">不使用项目资料库</option>
                  {projects.data.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}（{project.included_source_count} 个来源）
                    </option>
                  ))}
                </select>
              </span>
              <span className="hint">已纳入的资料片段会与公网检索结果一起参与证据核验。</span>
            </label>
          )}
          {isResearch && workflows.length > 0 && (
            <details className="home-disclosure" open={Boolean(customWorkflow)}>
              <summary>
                <AppIcon name="chevron-right" size={14} aria-hidden="true" />
                自定义研究流程
              </summary>
              <label className="field-label" htmlFor="workflow">
                研究流程
                <span className="select-with-icon">
                  <AppIcon name="workflow" size={15} aria-hidden="true" />
                  <select
                    id="workflow"
                    className="input"
                    value={workflow}
                    onChange={(event) => draft.update({ workflow: event.target.value })}
                  >
                    {workflows.map((item) => (
                      <option key={item.name} value={item.name}>
                        {BUILTIN_TEMPLATE_META[item.name]?.title || item.name}
                        {isDefaultWorkflow(item) ? '（默认）' : ''}
                      </option>
                    ))}
                  </select>
                </span>
                {activeWorkflow && (
                  <span className="hint">
                    {BUILTIN_TEMPLATE_META[activeWorkflow.name]?.description ??
                      activeWorkflow.description}
                  </span>
                )}
              </label>
            </details>
          )}
          <SettingsPanel
            value={params}
            onChange={(next) => draft.update({ params: next })}
            globalRequireCorroboration={config?.require_corroboration ?? false}
          />
          <ResourcePreflightPanel
            key={customWorkflow ?? strategyWorkflow ?? workflow}
            workflow={customWorkflow ?? strategyWorkflow ?? workflow}
          />
          <p className="home-config-motto" aria-hidden="true">
            Not just answers,
            <br />
            but new perspectives.
          </p>
        </aside>
      </fieldset>

      <div className="home-actionbar">
        <p className="home-actionbar-motto" aria-hidden="true">
          Exploration
          <br />
          has no final frontier.
        </p>
        <div className="home-actionbar-status" role="status">
          <AppIcon
            name={submitting ? 'loader' : clarify ? 'help' : 'circle-dot-dashed'}
            size={16}
            className={submitting ? 'spin' : ''}
            aria-hidden="true"
          />
          <span>{statusText}</span>
        </div>
        {error && (
          <div className="home-actionbar-error" role="alert">
            <AppIcon name="circle-x" size={15} aria-hidden="true" />
            {error}。输入已保留，可重新提交。
          </div>
        )}
        <div className="home-actionbar-go">
          <button
            className="btn btn-primary btn-lg"
            onClick={start}
            disabled={busy || !query.trim()}
            type="button"
          >
            {submitLabel}
            {!submitting && <AppIcon name="arrow-right" size={16} aria-hidden="true" />}
          </button>
          <span aria-hidden="true">Begin research</span>
        </div>
      </div>
    </div>
  )
}
