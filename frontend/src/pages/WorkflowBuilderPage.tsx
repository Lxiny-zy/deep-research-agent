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

/** 研究策略中心：展示可复用策略；底层流程编辑保留为管理员高级能力。 */
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
      <header className="page-header">
        <div>
            <h1>研究策略</h1>
            <p className="page-verse">一线接一线亮起，思路才有次第。</p>
            <p>
            先按研究目标选择策略；日常任务不需要理解 Agent、Step 或分支。底层流程编排保留在本页的高级区域，供管理员调试和扩展。
          </p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={() => openEditor(null)} type="button">
            <AppIcon name="plus" size={15} aria-hidden="true" />
            新建高级策略
          </button>
        </div>
      </header>

      <details className="panel workflow-templates" ref={templatesRef} open>
        <summary>
          <span className="workflow-template-icon">
            <AppIcon name="stack" size={18} aria-hidden="true" />
          </span>
          <span className="workflow-template-copy">
            <strong>可复用研究策略</strong>
            <span>从深度研究、快速查询或领域审查开始，策略会自动选择合适的证据链。</span>
          </span>
          <AppIcon
            name="chevron-down"
            size={18}
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
                className="btn btn-secondary btn-sm"
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

        <section className="panel workflow-custom-rail" aria-label="自定义研究策略">
        <div className="panel-header">
          <div>
            <h2 className="panel-title">我的研究策略</h2>
            <p className="hint">保存后的策略可以在新建研究时复用；只有需要改变底层 Agent 链路时才进入编辑。</p>
          </div>
        </div>
        {workflows.isLoading && <Skeleton rows={3} />}
        {workflows.isError && (
          <div className="workspace-load-error" role="alert">
            <p>{errMsg(workflows.error)}</p>
            <button className="btn btn-secondary btn-sm" onClick={() => void workflows.refetch()}>
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
            title="为你的研究，保存一套策略"
            description="从内置策略开始，或进入高级编排器创建专属链路。"
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
              <AppIcon name="stack" size={15} aria-hidden="true" /> 浏览研究策略
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
                  className="btn btn-ghost btn-sm"
                  onClick={() => navigate(`/?workflow=${encodeURIComponent(wf.name)}`)}
                >
                  <AppIcon name="play" size={13} aria-hidden="true" /> 去研究
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => openEditor(wf)}>
                  <AppIcon name="edit" size={13} aria-hidden="true" /> 编辑
                </button>
                <button
                  className="btn btn-ghost btn-sm danger"
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
