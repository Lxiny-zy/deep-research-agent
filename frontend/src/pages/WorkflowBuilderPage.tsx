import ResearchMotif from '../components/ResearchMotif'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import BuiltinTemplateGallery from '../components/BuiltinTemplateGallery'
import Skeleton from '../components/Skeleton'
import EmptyState from '../components/EmptyState'
import WorkflowEditor from '../components/WorkflowEditor'
import { AppIcon, type AppIconName } from '../components/AppIcon'
import { ApiError, listWorkflows } from '../api/client'
import { useCustomWorkflows, useRoles, useWorkflowMutations } from '../hooks/useCatalog'
import type { TemplateClone } from '../lib/workflowTemplates'
import type { WorkflowDef, WorkflowDefInput, WorkflowInfo, WorkflowViewport } from '../types'

function errMsg(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) {
    return `保存冲突：${e.message}。当前草稿已保留。`
  }
  return e instanceof Error ? e.message : '出错了'
}

const STEP_LABELS: Record<string, string> = {
  planner: '规划',
  researcher: '检索',
  reflector: '反思',
  synthesizer: '综合',
  critic: '复核',
}

const STEP_ICONS: Record<string, AppIconName> = {
  planner: 'route',
  researcher: 'search-code',
  reflector: 'refresh',
  synthesizer: 'file',
  critic: 'shield',
}

function stepLabel(step: WorkflowDef['steps'][number]): string {
  return step.kind === 'reflect_loop'
    ? '反思循环'
    : (STEP_LABELS[step.agent ?? ''] ?? step.agent ?? '步骤')
}

function stepIcon(step: WorkflowDef['steps'][number]): AppIconName {
  return step.kind === 'reflect_loop' ? 'refresh' : (STEP_ICONS[step.agent ?? ''] ?? 'workflow')
}

type EditSession = {
  key: number
  workflow: WorkflowDef | null
  // true＝workflow 是内置模板克隆出的脚手架（预填步骤链）：保存走 create 而非 update。
  clone?: boolean
}

/** 工作流构建器：可视化自由组合角色为有序流程，存库后可在「新建研究」中选用。 */
export default function WorkflowBuilderPage() {
  const navigate = useNavigate()
  const workflows = useCustomWorkflows()
  const roles = useRoles()
  const m = useWorkflowMutations()
  const sessionSequence = useRef(0)
  const [editSession, setEditSession] = useState<EditSession | undefined>(undefined)
  const [savingSessionKey, setSavingSessionKey] = useState<number | null>(null)
  const [saveErrors, setSaveErrors] = useState<Record<number, string>>({})
  const [templates, setTemplates] = useState<WorkflowInfo[]>([])
  const templatesRef = useRef<HTMLDetailsElement>(null)
  const [templateAttempt, setTemplateAttempt] = useState(0)
  const [templateLoading, setTemplateLoading] = useState(true)
  const [templateError, setTemplateError] = useState('')
  const [conflict, setConflict] = useState<{ key: number; latest: WorkflowDef } | null>(null)

  useEffect(() => {
    let cancelled = false
    setTemplateLoading(true)
    setTemplateError('')
    listWorkflows()
      .then((rows) => {
        if (!cancelled) setTemplates(rows)
      })
      .catch((error: unknown) => {
        if (!cancelled) setTemplateError(errMsg(error))
      })
      .finally(() => {
        if (!cancelled) setTemplateLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [templateAttempt])

  function openEditor(workflow: WorkflowDef | null) {
    setConflict(null)
    const key = ++sessionSequence.current
    setEditSession({ key, workflow })
  }

  function cloneTemplate(template: TemplateClone) {
    // 含运行时控制原语（compose/team_fanout）的模板无法用自定义步骤表达：降级为空 Studio。
    if (!template.steps) {
      openEditor(null)
      return
    }
    const scaffold: WorkflowDef = {
      id: '',
      name: `${template.name}-copy-${Date.now().toString(36).slice(-4)}`,
      display_name: `${template.title}副本`,
      description: template.description,
      steps: template.steps,
      nodes: [],
      edges: [],
      // 不给 viewport：让编排 Studio 首次打开时自动 fitView 到预填链。
      viewport: undefined as unknown as WorkflowViewport,
      version: 1,
      enabled: true,
    }
    const key = ++sessionSequence.current
    setEditSession({ key, workflow: scaffold, clone: true })
  }

  function save(body: WorkflowDefInput) {
    const session = editSession
    if (!session) return
    const sessionKey = session.key
    setSavingSessionKey(sessionKey)
    setSaveErrors((current) => {
      const next = { ...current }
      delete next[sessionKey]
      return next
    })
    const options = {
      onSuccess: () => {
        try {
          sessionStorage.removeItem(`dr_workflow_draft_${session.workflow?.id || 'new'}`)
        } catch {
          /* Storage is optional. */
        }
        setConflict(null)
        setSavingSessionKey((current) => (current === sessionKey ? null : current))
        setEditSession((current) => (current?.key === sessionKey ? undefined : current))
      },
      onError: (error: unknown) => {
        setSaveErrors((current) => ({ ...current, [sessionKey]: errMsg(error) }))
        if (
          !(error instanceof ApiError) ||
          error.status !== 409 ||
          !session.workflow ||
          session.clone
        )
          return
        void workflows.refetch().then((result) => {
          const latest = result.data?.find((workflow) => workflow.id === session.workflow?.id)
          if (!latest) return
          setConflict({ key: sessionKey, latest })
        })
      },
      onSettled: () => {
        setSavingSessionKey((current) => (current === sessionKey ? null : current))
      },
    }
    if (session.clone && session.workflow) {
      // 克隆会话：Studio 处于编辑态不提交 name，这里补上脚手架生成的唯一标识后走新建。
      m.create.mutate({ ...body, name: session.workflow.name }, options)
    } else if (session.workflow) {
      m.update.mutate({ id: session.workflow.id, body }, options)
    } else {
      m.create.mutate(body, options)
    }
  }

  function remove(wf: WorkflowDef) {
    if (window.confirm(`删除工作流「${wf.display_name || wf.name}」？`)) m.remove.mutate(wf.id)
  }

  return (
    <div className="stack page-stack workflow-page">
      <section className="page-intro workflow-intro page-intro-compact">
        <div>
          <span className="eyebrow">
            <AppIcon name="workflow" size={14} aria-hidden="true" /> 工作流 / 编排
          </span>
          <h1>
            可视化<span className="accent">自由编排</span>研究团队
          </h1>
          <p className="sub">
            从可用角色里挑选、排成一条有序流程（可插入反思循环），保存后即可在「新建研究」中选用并运行。
          </p>
        </div>
        <ResearchMotif kind="weave" className="page-motif" />
      </section>

      <details className="workflow-templates" ref={templatesRef}>
        <summary>
          <span className="workflow-template-icon">
            <AppIcon name="stack" size={21} aria-hidden="true" />
          </span>
          <span className="workflow-template-copy">
            <strong>参考内置模板</strong>
            <span>从现成的研究流程开始，克隆后按需调整。</span>
          </span>
          <AppIcon
            name="chevron-down"
            size={19}
            className="workflow-template-chevron"
            aria-hidden="true"
          />
        </summary>
        <div className="workflow-template-content">
          {templateLoading && <Skeleton rows={3} />}
          {templateError && (
            <div className="workspace-load-error" role="alert">
              <p>模板加载失败：{templateError}</p>
              <button
                className="btn btn-secondary small"
                onClick={() => setTemplateAttempt((value) => value + 1)}
              >
                重试加载模板
              </button>
            </div>
          )}
          {!templateLoading && !templateError && (
            <BuiltinTemplateGallery templates={templates} onClone={cloneTemplate} />
          )}
        </div>
      </details>

      <section className="panel workflow-custom-rail" aria-label="自定义工作流">
        <div className="panel-header">
          <div>
            <span className="panel-kicker">
              <AppIcon name="waypoints" size={12} aria-hidden="true" /> 自定义 / 工作流
            </span>
            <h2 className="panel-title">自定义工作流</h2>
          </div>
          <button className="btn btn-primary" onClick={() => openEditor(null)} type="button">
            <AppIcon name="plus" size={15} aria-hidden="true" />
            新建工作流
          </button>
        </div>
        {workflows.isLoading && <Skeleton rows={3} />}
        {workflows.isError && (
          <div className="workspace-load-error" role="alert">
            <p>{errMsg(workflows.error)}</p>
            <button className="btn btn-secondary small" onClick={() => void workflows.refetch()}>
              重试加载工作流
            </button>
          </div>
        )}
        {m.remove.isError && (
          <p className="error-text" role="alert">
            {errMsg(m.remove.error)}
          </p>
        )}
        {!workflows.isLoading && !workflows.isError && workflows.data?.length === 0 && (
          <EmptyState
            icon="workflow"
            title="为你的研究，搭一条专属流程"
            description="从空白画布自由连接角色，或以模板为起点。保存后即可在新建研究中使用。"
          >
            <button
              className="btn btn-secondary"
              onClick={() => {
                if (!templatesRef.current) return
                templatesRef.current.open = true
                templatesRef.current.querySelector('summary')?.focus({ preventScroll: true })
                templatesRef.current.scrollIntoView({ block: 'start' })
              }}
            >
              <AppIcon name="stack" size={15} aria-hidden="true" /> 浏览内置模板
            </button>
          </EmptyState>
        )}

        <div className="card-grid workflow-custom-grid">
          {workflows.data?.map((wf, index) => (
            <div
              key={wf.id}
              className={`role-card catalog-card workflow-custom-card${wf.enabled ? '' : ' disabled'}`}
              data-card-size="fixed"
              data-card-index={`W-${String(index + 1).padStart(2, '0')}`}
              title={wf.display_name || wf.name}
            >
              <span className="catalog-card-accent" aria-hidden="true" />
              <div className="catalog-card-top">
                <span className="catalog-card-index">W-{String(index + 1).padStart(2, '0')}</span>
                <span className="badge">{wf.steps.length} 步</span>
              </div>
              <div className="catalog-card-title">
                <span className="catalog-card-glyph" aria-hidden="true">
                  <AppIcon name="workflow" size={18} />
                </span>
                <div className="catalog-card-name">
                  <strong>{wf.display_name || wf.name}</strong>
                  <code className="catalog-card-code">{wf.name}</code>
                </div>
              </div>
              <p className="catalog-card-desc">
                {wf.description?.trim() || '按步骤串联角色，形成可复用的研究流程。'}
              </p>
              <div className="catalog-card-chain" aria-label="工作流步骤">
                {wf.steps.map((step, stepIndex) => (
                  <span className="catalog-card-chain-piece" key={`${step.kind}-${stepIndex}`}>
                    {stepIndex > 0 && (
                      <AppIcon
                        name="chevron-right"
                        size={11}
                        aria-hidden="true"
                        className="catalog-card-chain-arrow"
                      />
                    )}
                    <span
                      className={`catalog-card-chain-node${step.kind === 'reflect_loop' ? ' loop' : ''}`}
                    >
                      <AppIcon name={stepIcon(step)} size={11} aria-hidden="true" />
                      {stepLabel(step)}
                    </span>
                  </span>
                ))}
              </div>
              <div className="role-card-foot catalog-card-foot">
                <button
                  className="btn ghost small"
                  onClick={() => navigate(`/?workflow=${encodeURIComponent(wf.name)}`)}
                >
                  <AppIcon name="play" size={13} aria-hidden="true" /> 去研究
                </button>
                <button className="btn ghost small" onClick={() => openEditor(wf)}>
                  <AppIcon name="edit" size={13} aria-hidden="true" /> 编辑
                </button>
                <button
                  className="btn ghost small danger"
                  aria-label={`删除 ${wf.display_name || wf.name}`}
                  onClick={() => remove(wf)}
                >
                  <AppIcon name="trash" size={13} aria-hidden="true" /> 删除
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      {editSession !== undefined && (
        <WorkflowEditor
          key={editSession.key}
          initial={editSession.workflow}
          roles={roles.data ?? []}
          onSubmit={save}
          onCancel={() => {
            setEditSession(undefined)
          }}
          pending={savingSessionKey === editSession.key}
          error={saveErrors[editSession.key]}
          conflict={conflict?.key === editSession.key ? conflict.latest : undefined}
          onReload={() => {
            if (!conflict || conflict.key !== editSession.key) return
            openEditor(conflict.latest)
          }}
        />
      )}
    </div>
  )
}
