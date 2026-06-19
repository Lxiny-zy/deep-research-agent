import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Skeleton from '../components/Skeleton'
import WorkflowEditor from '../components/WorkflowEditor'
import { useCustomWorkflows, useRoles, useWorkflowMutations } from '../hooks/useCatalog'
import type { WorkflowDef, WorkflowDefInput } from '../types'

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : '出错了'
}

function stepSummary(wf: WorkflowDef): string {
  return wf.steps.map((s) => (s.kind === 'reflect_loop' ? '反思循环' : (s.agent ?? '?'))).join(' → ')
}

/** 工作流构建器：可视化自由组合角色为有序流程，存库后可在「新建研究」中选用。 */
export default function WorkflowBuilderPage() {
  const navigate = useNavigate()
  const workflows = useCustomWorkflows()
  const roles = useRoles()
  const m = useWorkflowMutations()
  // undefined＝弹窗关闭；null＝新建；对象＝编辑
  const [edit, setEdit] = useState<WorkflowDef | null | undefined>(undefined)

  function save(body: WorkflowDefInput) {
    const onDone = { onSuccess: () => setEdit(undefined) }
    if (edit) m.update.mutate({ id: edit.id, body }, onDone)
    else m.create.mutate(body, onDone)
  }

  function remove(wf: WorkflowDef) {
    if (window.confirm(`删除工作流「${wf.display_name || wf.name}」？`)) m.remove.mutate(wf.id)
  }

  const saveError = m.create.isError
    ? errMsg(m.create.error)
    : m.update.isError
      ? errMsg(m.update.error)
      : undefined

  return (
    <div className="stack">
      <section className="hero">
        <span className="eyebrow">✦ Workflow Builder</span>
        <h2>
          可视化<span className="accent">自由编排</span>研究团队
        </h2>
        <p className="sub">
          从可用角色里挑选、排成一条有序流程（可插入反思循环），保存后即可在「新建研究」中选用并运行。
        </p>
      </section>

      <div className="row between">
        <span className="hint">自定义工作流：把角色拼成你自己的多智能体流程，存库复用。</span>
        <button className="btn" onClick={() => setEdit(null)}>
          + 新建工作流
        </button>
      </div>

      {workflows.isLoading && <Skeleton rows={3} />}
      {workflows.isError && <p className="error-text">✗ {errMsg(workflows.error)}</p>}
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
                去研究
              </button>
              <button className="btn ghost small" onClick={() => setEdit(wf)}>
                编辑
              </button>
              <button className="btn ghost small danger" onClick={() => remove(wf)}>
                删除
              </button>
            </div>
          </div>
        ))}
      </div>

      {edit !== undefined && (
        <WorkflowEditor
          initial={edit}
          roles={roles.data ?? []}
          onSubmit={save}
          onCancel={() => setEdit(undefined)}
          pending={m.create.isPending || m.update.isPending}
          error={saveError}
        />
      )}
    </div>
  )
}
