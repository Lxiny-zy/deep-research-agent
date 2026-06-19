import { useState } from 'react'
import type { RoleInfo, WorkflowDef, WorkflowDefInput, WorkflowStep } from '../types'

interface Props {
  initial?: WorkflowDef | null // 传入＝编辑，否则＝新建
  roles: RoleInfo[]
  onSubmit: (body: WorkflowDefInput) => void
  onCancel: () => void
  pending?: boolean
  error?: string
}

// 新建时的起始模板：一条最简可产出报告的流程
const DEFAULT_STEPS: WorkflowStep[] = [
  { kind: 'agent', agent: 'planner' },
  { kind: 'agent', agent: 'researcher' },
  { kind: 'agent', agent: 'synthesizer' },
]

/** 自定义工作流构建器：把角色拼成有序流程（含反思循环），存为可复用的命名流程。 */
export default function WorkflowEditor({
  initial,
  roles,
  onSubmit,
  onCancel,
  pending,
  error,
}: Props) {
  const editing = !!initial
  const [name, setName] = useState(initial?.name ?? '')
  const [displayName, setDisplayName] = useState(initial?.display_name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [steps, setSteps] = useState<WorkflowStep[]>(
    initial?.steps?.length ? initial.steps : DEFAULT_STEPS,
  )

  const fallbackRole = roles[0]?.name ?? 'researcher'

  function patchStep(i: number, patch: Partial<WorkflowStep>) {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)))
  }
  function addStep() {
    setSteps((prev) => [...prev, { kind: 'agent', agent: fallbackRole }])
  }
  function removeStep(i: number) {
    setSteps((prev) => prev.filter((_, idx) => idx !== i))
  }
  function move(i: number, dir: -1 | 1) {
    setSteps((prev) => {
      const j = i + dir
      if (j < 0 || j >= prev.length) return prev
      const next = [...prev]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })
  }

  function submit() {
    const body: WorkflowDefInput = {
      display_name: displayName.trim(),
      description: description.trim(),
      steps,
      enabled: true,
    }
    if (!editing) body.name = name.trim()
    onSubmit(body)
  }

  const hasSynth = steps.some((s) => s.kind === 'agent' && s.agent === 'synthesizer')

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h3 className="panel-title">{editing ? '编辑工作流' : '新建工作流'}</h3>

        <div className="stack">
          {!editing && (
            <label className="field-label">
              工作流标识（英文，运行按此引用，创建后不可改）
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="如 my-deep"
              />
            </label>
          )}

          <label className="field-label">
            展示名
            <input
              className="input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="如 我的深度流程"
            />
          </label>

          <label className="field-label">
            描述
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="这个流程做什么"
            />
          </label>

          <div className="field-label">流程步骤（顺序执行；须含 synthesizer 收尾才能产出报告）</div>
          <div className="stack" style={{ gap: 8 }}>
            {steps.map((s, i) => (
              <div className="step-row" key={i}>
                <span className="step-index">{i + 1}</span>
                <select
                  className="input"
                  style={{ width: 110, flex: '0 0 auto' }}
                  value={s.kind}
                  onChange={(e) => patchStep(i, { kind: e.target.value as WorkflowStep['kind'] })}
                >
                  <option value="agent">角色</option>
                  <option value="reflect_loop">反思循环</option>
                </select>
                {s.kind === 'agent' ? (
                  <select
                    className="input"
                    style={{ flex: 1 }}
                    value={s.agent ?? fallbackRole}
                    onChange={(e) => patchStep(i, { agent: e.target.value })}
                  >
                    {roles.map((r) => (
                      <option key={r.name} value={r.name}>
                        {r.icon} {r.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <div className="row gap" style={{ flex: 1 }}>
                    <select
                      className="input"
                      value={s.reflector ?? 'reflector'}
                      onChange={(e) => patchStep(i, { reflector: e.target.value })}
                    >
                      {roles.map((r) => (
                        <option key={r.name} value={r.name}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                    <select
                      className="input"
                      value={s.researcher ?? 'researcher'}
                      onChange={(e) => patchStep(i, { researcher: e.target.value })}
                    >
                      {roles.map((r) => (
                        <option key={r.name} value={r.name}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                    <input
                      className="input"
                      type="number"
                      min={1}
                      max={5}
                      style={{ width: 84, flex: '0 0 auto' }}
                      placeholder="轮数"
                      value={s.max_rounds ?? ''}
                      onChange={(e) =>
                        patchStep(i, { max_rounds: e.target.value ? Number(e.target.value) : null })
                      }
                    />
                  </div>
                )}
                <div className="row" style={{ gap: 4, flex: '0 0 auto' }}>
                  <button
                    className="btn ghost sm"
                    onClick={() => move(i, -1)}
                    disabled={i === 0}
                    title="上移"
                  >
                    ↑
                  </button>
                  <button
                    className="btn ghost sm"
                    onClick={() => move(i, 1)}
                    disabled={i === steps.length - 1}
                    title="下移"
                  >
                    ↓
                  </button>
                  <button
                    className="btn ghost sm danger"
                    onClick={() => removeStep(i)}
                    title="删除"
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
          <button className="btn ghost sm" onClick={addStep} style={{ alignSelf: 'flex-start' }}>
            + 添加步骤
          </button>

          {!hasSynth && <p className="hint">提示：流程需包含 synthesizer 角色才能产出报告。</p>}
          {error && <p className="error-text">✗ {error}</p>}

          <div className="row between" style={{ marginTop: 8 }}>
            <button className="btn ghost" onClick={onCancel}>
              取消
            </button>
            <button
              className="btn"
              onClick={submit}
              disabled={pending || steps.length === 0 || (!editing && !name.trim())}
            >
              {pending ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
