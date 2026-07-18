import { useMemo, useState } from 'react'
import WorkflowFlowCanvas from './WorkflowFlowCanvas'
import type { RoleInfo, WorkflowDef, WorkflowDefInput, WorkflowStep } from '../types'

interface Props {
  initial?: WorkflowDef | null
  roles: RoleInfo[]
  onSubmit: (body: WorkflowDefInput) => void
  onCancel: () => void
  pending?: boolean
  error?: string
}

/*
 * Design assumptions:
 * - This release edits an ordered pipeline; arbitrary graph edges come with the graph schema migration.
 * - The canvas is the primary workspace. Metadata and node configuration remain secondary.
 * - Roles are dragged from a reusable library, while reflection is a control-flow node.
 */
const DEFAULT_STEPS: WorkflowStep[] = [
  { kind: 'agent', agent: 'planner' },
  { kind: 'agent', agent: 'researcher' },
  { kind: 'agent', agent: 'synthesizer' },
]

function nodeTitle(step: WorkflowStep, roles: RoleInfo[]): string {
  if (step.kind === 'reflect_loop') return '反思循环'
  return roles.find((role) => role.name === step.agent)?.label ?? step.agent ?? '未配置角色'
}

export default function WorkflowEditor({
  initial,
  roles,
  onSubmit,
  onCancel,
  pending,
  error,
}: Props) {
  const editing = !!initial
  const initialSteps = initial?.nodes?.length
    ? initial.nodes.map((node) => node.step)
    : initial?.steps?.length
      ? initial.steps
      : DEFAULT_STEPS
  const [name, setName] = useState(initial?.name ?? '')
  const [displayName, setDisplayName] = useState(initial?.display_name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [steps, setSteps] = useState<WorkflowStep[]>(initialSteps)
  const [nodeKeys, setNodeKeys] = useState<string[]>(() =>
    initial?.nodes?.length
      ? initial.nodes.map((node) => node.id)
      : initialSteps.map((_, index) => `node-${index + 1}`),
  )
  const [dependencies, setDependencies] = useState<Record<string, string[]>>(() => {
    if (initial?.nodes?.length) {
      const incoming: Record<string, string[]> = Object.fromEntries(
        initial.nodes.map((node) => [node.id, []]),
      )
      for (const edge of initial.edges ?? []) incoming[edge.target]?.push(edge.source)
      return incoming
    }
    const keys = initialSteps.map((_, index) => `node-${index + 1}`)
    return Object.fromEntries(keys.map((key, index) => [key, index ? [keys[index - 1]] : []]))
  })
  const [edgeConditions, setEdgeConditions] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      (initial?.edges ?? [])
        .filter((edge) => edge.condition)
        .map((edge) => [`${edge.source}->${edge.target}`, edge.condition ?? '']),
    ),
  )
  const [joinModes, setJoinModes] = useState<Record<string, 'any' | 'all' | 'success_all'>>(
    () => Object.fromEntries((initial?.nodes ?? []).map((node) => [node.id, node.join_mode ?? 'any'])),
  )
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>(() =>
    Object.fromEntries(
      initial?.nodes?.length
        ? initial.nodes.map((node) => [node.id, node.position])
        : initialSteps.map((_, index) => [`node-${index + 1}`, { x: 220, y: 70 + index * 150 }]),
    ),
  )
  const [selected, setSelected] = useState(0)
  const [mobilePane, setMobilePane] = useState<'library' | 'canvas' | 'inspector'>('canvas')
  const [graphError, setGraphError] = useState('')

  const current = steps[selected]
  const hasSynth = steps.some((step) => step.kind === 'agent' && step.agent === 'synthesizer')
  const validation = useMemo(() => {
    if (!steps.length) return '画布至少需要一个节点'
    if (!hasSynth) return '缺少可产出报告的 Synthesizer 节点'
    return null
  }, [hasSynth, steps.length])

  function patchStep(index: number, patch: Partial<WorkflowStep>) {
    setSteps((prev) => prev.map((step, i) => (i === index ? { ...step, ...patch } : step)))
  }

  function appendAgent(agent: string) {
    setSteps((prev) => {
      const key = `node-${Date.now()}-${prev.length}`
      setNodeKeys((keys) => [...keys, key])
      setPositions((value) => ({ ...value, [key]: { x: 220 + (prev.length % 3) * 280, y: 100 + Math.floor(prev.length / 3) * 170 } }))
      setDependencies((deps) => ({ ...deps, [key]: [] }))
      setSelected(prev.length)
      return [...prev, { kind: 'agent', agent }]
    })
  }

  function appendReflection() {
    setSteps((prev) => {
      const key = `node-${Date.now()}-${prev.length}`
      setNodeKeys((keys) => [...keys, key])
      setPositions((value) => ({ ...value, [key]: { x: 220 + (prev.length % 3) * 280, y: 100 + Math.floor(prev.length / 3) * 170 } }))
      setDependencies((deps) => ({ ...deps, [key]: [] }))
      setSelected(prev.length)
      return [
        ...prev,
        { kind: 'reflect_loop', reflector: 'reflector', researcher: 'researcher' },
      ]
    })
  }

  function cyclePath(source: string, target: string): string[] | null {
    const outgoing = new Map<string, string[]>()
    for (const [child, parents] of Object.entries(dependencies)) {
      for (const parent of parents) outgoing.set(parent, [...(outgoing.get(parent) ?? []), child])
    }
    const queue: Array<{ id: string; path: string[] }> = [{ id: target, path: [target] }]
    const visited = new Set<string>()
    while (queue.length) {
      const current = queue.shift()!
      if (current.id === source) return [...current.path, target]
      if (visited.has(current.id)) continue
      visited.add(current.id)
      for (const next of outgoing.get(current.id) ?? []) queue.push({ id: next, path: [...current.path, next] })
    }
    return null
  }

  function connectNodes(source: string, target: string) {
    const cycle = cyclePath(source, target)
    if (cycle) {
      const labels = cycle.map((key) => {
        const index = nodeKeys.indexOf(key)
        return index >= 0 ? nodeTitle(steps[index], roles) : key
      })
      setGraphError(`无法连接：该依赖会形成循环 ${labels.join(' → ')}`)
      return
    }
    setGraphError('')
    setDependencies((prev) => ({
      ...prev,
      [target]: [...new Set([...(prev[target] ?? []), source])],
    }))
  }

  function removeSelected() {
    const removedKey = nodeKeys[selected]
    setSteps((prev) => prev.filter((_, i) => i !== selected))
    setNodeKeys((prev) => prev.filter((_, i) => i !== selected))
    setDependencies((prev) => {
      const next: Record<string, string[]> = {}
      for (const [key, parents] of Object.entries(prev)) {
        if (key !== removedKey) next[key] = parents.filter((parent) => parent !== removedKey)
      }
      return next
    })
    setSelected((prev) => Math.max(0, Math.min(prev, steps.length - 2)))
  }

  function submit() {
    const nodes = steps.map((step, index) => ({
      id: nodeKeys[index],
      type: 'step',
      position: positions[nodeKeys[index]] ?? { x: 220, y: 80 + index * 150 },
      step,
      join_mode: joinModes[nodeKeys[index]] ?? 'any',
    }))
    const edges = nodes.flatMap((node) =>
      (dependencies[node.id] ?? []).map((source, index) => ({
        id: `edge-${source}-${node.id}-${index}`,
        source,
        target: node.id,
        condition: edgeConditions[`${source}->${node.id}`] || null,
      })),
    )
    const body: WorkflowDefInput = {
      display_name: displayName.trim(),
      description: description.trim(),
      steps,
      nodes,
      edges,
      viewport: { x: 0, y: 0, zoom: 1 },
      version: initial?.version ?? 1,
      enabled: true,
    }
    if (!editing) body.name = name.trim()
    onSubmit(body)
  }

  return (
    <div className="modal-backdrop workflow-studio-backdrop" onClick={onCancel}>
      <div className="workflow-studio" onClick={(event) => event.stopPropagation()}>
        <header className="workflow-studio-head">
          <div>
            <span className="workflow-kicker">ORCHESTRATION STUDIO</span>
            <strong>{editing ? '编辑编排' : '创建编排'}</strong>
            <span className="muted small">顺序管线模式 · 图分支能力即将接入</span>
          </div>
          <div className="row gap">
            <span className={`pipeline-health ${validation ? 'warning' : 'ready'}`}>
              {validation ?? '管线可运行'}
            </span>
            <button className="btn ghost" onClick={onCancel}>关闭</button>
            <button
              className="btn btn-primary"
              onClick={submit}
              disabled={pending || !!validation || (!editing && !name.trim())}
            >
              {pending ? '保存中…' : '保存编排'}
            </button>
          </div>
        </header>

        <nav className="workflow-mobile-tabs" aria-label="编排工作区切换">
          {(['library', 'canvas', 'inspector'] as const).map((pane) => (
            <button
              key={pane}
              className={mobilePane === pane ? 'active' : ''}
              onClick={() => setMobilePane(pane)}
            >
              {pane === 'library' ? '角色库' : pane === 'canvas' ? '画布' : '检查器'}
            </button>
          ))}
        </nav>

        <div className="workflow-studio-body">
          <aside className={`workflow-library mobile-pane ${mobilePane === 'library' ? 'mobile-active' : ''}`}>
            <div className="workflow-pane-title">
              <strong>角色库</strong>
              <span>{roles.length} 个可用角色</span>
            </div>
            <p className="muted small">拖入画布，或点击快速添加。</p>
            <div className="workflow-role-list">
              {roles.map((role) => (
                <button
                  key={role.name}
                  className="workflow-role-item"
                  draggable
                  onDragStart={(event) => event.dataTransfer.setData('agent-role', role.name)}
                  onClick={() => appendAgent(role.name)}
                >
                  <span className="role-monogram">{role.label.slice(0, 1)}</span>
                  <span>
                    <strong>{role.label}</strong>
                    <small>{role.name}{role.builtin ? ' · 内置' : ' · 自定义'}</small>
                  </span>
                  <span className="role-add">＋</span>
                </button>
              ))}
            </div>
            <div className="workflow-library-divider" />
            <button className="workflow-role-item control" onClick={appendReflection}>
              <span className="role-monogram">↻</span>
              <span><strong>反思循环</strong><small>评估证据并补充研究</small></span>
              <span className="role-add">＋</span>
            </button>
          </aside>

          <main
            className={`workflow-canvas mobile-pane ${mobilePane === 'canvas' ? 'mobile-active' : ''}`}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault()
              const role = event.dataTransfer.getData('agent-role')
              if (role) appendAgent(role)
            }}
          >
            <div className="canvas-toolbar">
              <span><strong>{steps.length}</strong> 节点</span>
              <span>拖动节点 · 端口连线 · 双击连线删除</span>
              <span className="canvas-mode">FREE CANVAS</span>
            </div>
            <div className="flow-canvas-shell">
              <WorkflowFlowCanvas
                steps={steps}
                nodeKeys={nodeKeys}
                roles={roles}
                dependencies={dependencies}
                conditions={edgeConditions}
                positions={positions}
                selected={selected}
                onSelect={setSelected}
                onPositionsChange={setPositions}
                onConnect={connectNodes}
                onDisconnect={(source, target) => {
                  setDependencies((prev) => ({
                    ...prev,
                    [target]: (prev[target] ?? []).filter((item) => item !== source),
                  }))
                  setEdgeConditions((prev) => {
                    const next = { ...prev }
                    delete next[`${source}->${target}`]
                    return next
                  })
                }}
              />
            </div>
            {!steps.length && <div className="canvas-empty">从左侧角色库拖入第一个 Agent</div>}
            {graphError && <div className="canvas-graph-error">{graphError}</div>}
          </main>

          <aside className={`workflow-inspector mobile-pane ${mobilePane === 'inspector' ? 'mobile-active' : ''}`}>
            <div className="workflow-pane-title"><strong>检查器</strong><span>管线与节点属性</span></div>
            <label className="field-label">
              工作流标识
              <input className="input" value={name} disabled={editing} onChange={(e) => setName(e.target.value)} placeholder="research-pipeline" />
            </label>
            <label className="field-label">
              展示名
              <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="市场研究管线" />
            </label>
            <label className="field-label">
              描述
              <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="说明这条管线解决什么问题" rows={3} />
            </label>

            <div className="inspector-divider" />
            {current ? (
              <div className="stack compact">
                <div className="selected-node-label"><span>NODE {selected + 1}</span><strong>{nodeTitle(current, roles)}</strong></div>
                {current.kind === 'agent' ? (
                  <label className="field-label">
                    执行角色
                    <select className="input" value={current.agent} onChange={(e) => patchStep(selected, { agent: e.target.value })}>
                      {roles.map((role) => <option key={role.name} value={role.name}>{role.label} · {role.name}</option>)}
                    </select>
                  </label>
                ) : (
                  <>
                    <label className="field-label">评估角色<select className="input" value={current.reflector ?? 'reflector'} onChange={(e) => patchStep(selected, { reflector: e.target.value })}>{roles.map((role) => <option key={role.name} value={role.name}>{role.label}</option>)}</select></label>
                    <label className="field-label">补充研究角色<select className="input" value={current.researcher ?? 'researcher'} onChange={(e) => patchStep(selected, { researcher: e.target.value })}>{roles.map((role) => <option key={role.name} value={role.name}>{role.label}</option>)}</select></label>
                    <label className="field-label">最大轮次<input className="input" type="number" min={1} max={5} value={current.max_rounds ?? ''} onChange={(e) => patchStep(selected, { max_rounds: e.target.value ? Number(e.target.value) : null })} /></label>
                  </>
                )}
                <div className="field-label">
                  前置依赖
                  <div className="dependency-picker">
                    {steps.map((step, index) => {
                      if (index === selected) return null
                      const key = nodeKeys[index]
                      const checked = (dependencies[nodeKeys[selected]] ?? []).includes(key)
                      return (
                        <div className="dependency-option" key={key}>
                          <label>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) => {
                                const target = nodeKeys[selected]
                                if (event.target.checked) {
                                  connectNodes(key, target)
                                } else {
                                  setGraphError('')
                                  setDependencies((prev) => ({
                                    ...prev,
                                    [target]: (prev[target] ?? []).filter((parent) => parent !== key),
                                  }))
                                }
                              }}
                            />
                            <span>{index + 1}. {nodeTitle(step, roles)}</span>
                          </label>
                          {checked && (
                            <input
                              className="dependency-condition"
                              value={edgeConditions[`${key}->${nodeKeys[selected]}`] ?? ''}
                              onChange={(event) =>
                                setEdgeConditions((prev) => ({
                                  ...prev,
                                  [`${key}->${nodeKeys[selected]}`]: event.target.value,
                                }))
                              }
                              placeholder="可选条件，如 state.reflections.last.is_sufficient == true"
                            />
                          )}
                        </div>
                      )
                    })}
                    {steps.length === 1 && <span className="muted small">添加其他节点后可配置依赖</span>}
                  </div>
                </div>
                {(dependencies[nodeKeys[selected]] ?? []).length > 1 && (
                  <label className="field-label">
                    汇聚策略
                    <select
                      className="input"
                      value={joinModes[nodeKeys[selected]] ?? 'any'}
                      onChange={(event) => setJoinModes((prev) => ({
                        ...prev,
                        [nodeKeys[selected]]: event.target.value as 'any' | 'all' | 'success_all',
                      }))}
                    >
                      <option value="any">任一分支激活</option>
                      <option value="all">全部分支激活</option>
                      <option value="success_all">全部分支成功</option>
                    </select>
                  </label>
                )}
                <div className="inspector-divider" />
                <div className="reliability-grid">
                  <label className="field-label">
                    超时（秒）
                    <input
                      className="input"
                      type="number"
                      min={1}
                      value={current.timeout_seconds ?? ''}
                      onChange={(e) => patchStep(selected, {
                        timeout_seconds: e.target.value ? Number(e.target.value) : null,
                      })}
                      placeholder="不限"
                    />
                  </label>
                  <label className="field-label">
                    最大尝试
                    <input
                      className="input"
                      type="number"
                      min={1}
                      max={10}
                      value={current.max_attempts ?? 1}
                      onChange={(e) => patchStep(selected, { max_attempts: Number(e.target.value) })}
                    />
                  </label>
                  <label className="field-label">
                    退避基数
                    <input
                      className="input"
                      type="number"
                      min={0}
                      step={0.1}
                      value={current.retry_backoff ?? 0.5}
                      onChange={(e) => patchStep(selected, { retry_backoff: Number(e.target.value) })}
                    />
                  </label>
                  <label className="field-label">
                    失败策略
                    <select
                      className="input"
                      value={current.failure_policy ?? 'continue'}
                      onChange={(e) => patchStep(selected, {
                        failure_policy: e.target.value as 'continue' | 'fail_fast',
                      })}
                    >
                      <option value="continue">隔离并继续</option>
                      <option value="fail_fast">立即终止</option>
                    </select>
                  </label>
                </div>
                <label className="field-label">
                  Fallback Agent
                  <select
                    className="input"
                    value={current.fallback_agent ?? ''}
                    onChange={(e) => patchStep(selected, { fallback_agent: e.target.value || null })}
                  >
                    <option value="">不使用</option>
                    {roles.map((role) => <option key={role.name} value={role.name}>{role.label}</option>)}
                  </select>
                </label>
                <button className="btn ghost danger" onClick={removeSelected}>删除此节点</button>
              </div>
            ) : <p className="muted">选择画布中的节点进行配置。</p>}
            {error && <p className="error-text">✗ {error}</p>}
          </aside>
        </div>
      </div>
    </div>
  )
}
