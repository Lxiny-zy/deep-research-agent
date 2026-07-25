import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Skeleton from '../components/Skeleton'
import WorkflowEditor from '../components/WorkflowEditor'
import { AppIcon } from '../components/AppIcon'
import { ApiError } from '../api/client'
import { useCustomWorkflows, useRoles, useWorkflowMutations } from '../hooks/useCatalog'
import type { WorkflowDef, WorkflowDefInput } from '../types'

function errMsg(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) {
    return `保存冲突：${e.message}。当前草稿已保留。`
  }
  return e instanceof Error ? e.message : '出错了'
}

function stepSummary(wf: WorkflowDef): string {
  return wf.steps.map((s) => (s.kind === 'reflect_loop' ? '反思循环' : (s.agent ?? '?'))).join(' · ')
}

type EditSession = {
  key: number
  workflow: WorkflowDef | null
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

  function openEditor(workflow: WorkflowDef | null) {
    const key = ++sessionSequence.current
    setEditSession({ key, workflow })
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
        if (!(error instanceof ApiError) || error.status !== 409 || !session.workflow) return
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
    if (session.workflow) m.update.mutate({ id: session.workflow.id, body }, options)
    else m.create.mutate(body, options)
  }

  function remove(wf: WorkflowDef) {
    if (window.confirm(`删除工作流「${wf.display_name || wf.name}」？`)) m.remove.mutate(wf.id)
  }

  return (
    <div className="stack">
      <section className="page-intro workflow-intro">
        <div>
          <span className="eyebrow"><AppIcon name="workflow" size={14} aria-hidden="true" /> WORKFLOW / BUILDER</span>
          <h1>可视化<span className="accent">自由编排</span>研究团队</h1>
          <p className="sub">
          从可用角色里挑选、排成一条有序流程（可插入反思循环），保存后即可在「新建研究」中选用并运行。
          </p>
        </div>
        <div className="page-intro-mark" aria-hidden="true"><AppIcon name="waypoints" size={58} strokeWidth={1.2} /></div>
      </section>

      <div className="row between">
        <span className="hint">自定义工作流：把角色拼成你自己的多智能体流程，存库复用。</span>
        <button className="btn btn-primary" onClick={() => openEditor(null)} type="button">
          <AppIcon name="plus" size={15} aria-hidden="true" />
          新建工作流
        </button>
      </div>

      {workflows.isLoading && <Skeleton rows={3} />}
      {workflows.isError && <p className="error-text"><AppIcon name="circle-x" size={14} aria-hidden="true" />{errMsg(workflows.error)}</p>}
      {workflows.data?.length === 0 && (
        <p className="muted">还没有自定义工作流。点「新建工作流」开始拼装你的研究团队。</p>
      )}

      <div className="card-grid">
        {workflows.data?.map((wf) => (
          <div key={wf.id} className={`role-card${wf.enabled ? '' : ' disabled'}`}>
            <div className="role-card-head">
              <span className="badge">{wf.steps.length} 步</span>
              <strong>{wf.display_name || wf.name}</strong>
            </div>
            <code className="muted small">{wf.name}</code>
            {wf.description && <p className="muted small">{wf.description}</p>}
            <p className="step-summary">{stepSummary(wf)}</p>
            <div className="role-card-foot row gap">
              <button
                className="btn ghost small"
                onClick={() => navigate(`/?workflow=${encodeURIComponent(wf.name)}`)}
              >
                <AppIcon name="play" size={13} aria-hidden="true" /> 去研究
              </button>
              <button className="btn ghost small" onClick={() => openEditor(wf)}>
                <AppIcon name="edit" size={13} aria-hidden="true" /> 编辑
              </button>
              <button className="btn ghost small danger" onClick={() => remove(wf)}>
                <AppIcon name="trash" size={13} aria-hidden="true" /> 删除
              </button>
            </div>
          </div>
        ))}
      </div>

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
