import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import BuiltinTemplateGallery from '../components/BuiltinTemplateGallery'
import Skeleton from '../components/Skeleton'
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

  useEffect(() => {
    let cancelled = false
    listWorkflows()
      .then((rows) => {
        if (!cancelled) setTemplates(rows)
      })
      .catch(() => {
        // 内置模板陈列加载失败不阻断页面主功能（自定义工作流增删改）。
      })
    return () => {
      cancelled = true
    }
  }, [])

  function openEditor(workflow: WorkflowDef | null) {
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
        // Refresh only the server version. The editor remains mounted, so its
        // local draft stays intact and the next submit carries the new version.
        void workflows.refetch().then((result) => {
          const latest = result.data?.find((workflow) => workflow.id === session.workflow?.id)
          if (!latest) return
          setEditSession((current) => {
            if (current?.key !== sessionKey || !current.workflow) return current
            return { ...current, workflow: { ...current.workflow, version: latest.version } }
          })
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
    <div className="stack">
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
        <div className="page-intro-mark" aria-hidden="true">
          <AppIcon name="waypoints" size={40} strokeWidth={1.2} />
        </div>
      </section>

      <BuiltinTemplateGallery templates={templates} onClone={cloneTemplate} />

      <section className="builtin-rail workflow-custom-rail" aria-label="自定义工作流">
        <div className="builtin-rail-head">
          <div>
            <span className="panel-kicker">
              <AppIcon name="waypoints" size={12} aria-hidden="true" /> 自定义 / 工作流
            </span>
            <h2 className="builtin-rail-title">自定义工作流</h2>
          </div>
          <button className="btn btn-primary" onClick={() => openEditor(null)} type="button">
            <AppIcon name="plus" size={15} aria-hidden="true" />
            新建工作流
          </button>
        </div>
        <span className="hint">
          把角色拼成你自己的多智能体流程，存库后可在「新建研究」中选用，也可从上方模板克隆起步。
        </span>

        {workflows.isLoading && <Skeleton rows={3} />}
        {workflows.isError && (
          <p className="error-text">
            <AppIcon name="circle-x" size={14} aria-hidden="true" />
            {errMsg(workflows.error)}
          </p>
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
        />
      )}
    </div>
  )
}
