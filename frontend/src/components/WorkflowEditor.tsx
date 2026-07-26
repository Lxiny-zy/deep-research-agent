import { useMemo, useRef, useState } from 'react'
import WorkflowFlowCanvas from './WorkflowFlowCanvas'
import { AgentGlyph, AppIcon } from './AppIcon'
import { allocateSemanticNodeIds } from './workflowCanvasLogic'
import {
  dependenciesFromEdges,
  findAvailableNodePosition,
  hasSingleTerminalAgent,
  nextWorkflowEdgeId,
  normalizeWorkflowEdges,
  reportAgentNames,
} from './workflowEditorLogic'
import type {
  RoleInfo,
  WorkflowDef,
  WorkflowDefInput,
  WorkflowEdge,
  WorkflowStep,
  WorkflowViewport,
} from '../types'

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

type CanvasPosition = { x: number; y: number }

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
  const initialNodeKeys = initial?.nodes?.length
    ? initial.nodes.map((node) => node.id)
    : initialSteps.map((_, index) => `node-${index + 1}`)
  const semanticNodeIds = useRef(allocateSemanticNodeIds(initialNodeKeys)).current
  const [name, setName] = useState(initial?.name ?? '')
  const [displayName, setDisplayName] = useState(initial?.display_name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [steps, setSteps] = useState<WorkflowStep[]>(initialSteps)
  const [nodeKeys, setNodeKeys] = useState<string[]>(initialNodeKeys)
  const [workflowEdges, setWorkflowEdges] = useState<WorkflowEdge[]>(() => {
    if (initial?.nodes?.length) return normalizeWorkflowEdges(initial.edges ?? [])
    return initialNodeKeys.slice(1).map((target, index) => ({
      id: `edge-${initialNodeKeys[index]}-${target}`,
      source: initialNodeKeys[index],
      target,
      condition: null,
    }))
  })
  const dependencies = useMemo(
    () => dependenciesFromEdges(nodeKeys, workflowEdges),
    [nodeKeys, workflowEdges],
  )
  const [joinModes, setJoinModes] = useState<Record<string, 'any' | 'all' | 'success_all'>>(
    () => Object.fromEntries((initial?.nodes ?? []).map((node) => [node.id, node.join_mode ?? 'any'])),
  )
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>(() =>
    ({
      ...Object.fromEntries(
        initial?.nodes?.length
          ? initial.nodes.map((node) => [node.id, node.position])
          : initialSteps.map((_, index) => [`node-${index + 1}`, { x: 220, y: 70 + index * 150 }]),
      ),
      ...(initial?.viewport?.input_position
        ? { [semanticNodeIds.input]: initial.viewport.input_position }
        : {}),
      ...(initial?.viewport?.output_position
        ? { [semanticNodeIds.output]: initial.viewport.output_position }
        : {}),
    }),
  )
  const [viewport, setViewport] = useState<WorkflowViewport>(() => ({
    x: initial?.viewport?.x ?? 0,
    y: initial?.viewport?.y ?? 0,
    zoom: initial?.viewport?.zoom ?? 1,
    input_position: initial?.viewport?.input_position,
    output_position: initial?.viewport?.output_position,
  }))
  const fitViewOnInit =
    initial?.viewport?.x == null ||
    initial.viewport.y == null ||
    initial.viewport.zoom == null
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(() =>
    initial?.nodes?.[0]?.id ?? (initialSteps.length ? 'node-1' : null),
  )
  const [mobilePane, setMobilePane] = useState<'library' | 'canvas' | 'inspector'>('canvas')
  const [graphError, setGraphError] = useState('')
  const nodeSequence = useRef(initialSteps.length)

  const selected = selectedNodeId ? nodeKeys.indexOf(selectedNodeId) : -1
  const current = selected >= 0 ? steps[selected] : undefined
  const reportAgents = useMemo(() => reportAgentNames(roles), [roles])
  const hasReportAgent = steps.some(
    (step) => step.kind === 'agent' && reportAgents.has(step.agent ?? ''),
  )
  const hasSingleTerminalReportAgent = hasSingleTerminalAgent(
    steps,
    nodeKeys,
    dependencies,
    reportAgents,
  )
  const validation = useMemo(() => {
    if (!steps.length) return '画布至少需要一个节点'
    if (!hasReportAgent) return '缺少可产出报告的终端角色节点'
    if (steps.length > 1) {
      const connected = new Set<string>()
      const neighbors = new Map<string, string[]>()
      for (const key of nodeKeys) neighbors.set(key, [])
      for (const [target, sources] of Object.entries(dependencies)) {
        for (const source of sources) {
          if (!neighbors.has(source) || !neighbors.has(target)) continue
          neighbors.get(source)!.push(target)
          neighbors.get(target)!.push(source)
        }
      }
      const queue = nodeKeys.length ? [nodeKeys[0]] : []
      while (queue.length) {
        const key = queue.shift()!
        if (connected.has(key)) continue
        connected.add(key)
        queue.push(...(neighbors.get(key) ?? []))
      }
      if (connected.size !== nodeKeys.length) return '工作流包含未连接节点，请先完成节点连线'
    }
    if (!hasSingleTerminalReportAgent) return '所有分支都必须汇入唯一的终端报告角色节点'
    return null
  }, [dependencies, hasReportAgent, hasSingleTerminalReportAgent, nodeKeys, steps.length])

  function patchStep(index: number, patch: Partial<WorkflowStep>) {
    setSteps((prev) => prev.map((step, i) => (i === index ? { ...step, ...patch } : step)))
  }

  function appendStep(step: WorkflowStep, preferredPosition?: CanvasPosition) {
    const key = `node-${Date.now()}-${nodeSequence.current}`
    nodeSequence.current += 1
    const existingPositions = nodeKeys
      .map((nodeKey) => positions[nodeKey])
      .filter((position): position is CanvasPosition => !!position)
    const anchor = selectedNodeId
      ? positions[selectedNodeId]
      : positions[nodeKeys[nodeKeys.length - 1]]
    const position = findAvailableNodePosition(existingPositions, anchor, preferredPosition)

    setSteps((prev) => [...prev, step])
    setNodeKeys((prev) => [...prev, key])
    setPositions((prev) => ({ ...prev, [key]: position }))
    setSelectedNodeId(key)
    setGraphError('')
    setMobilePane('canvas')
  }

  function appendAgent(agent: string, preferredPosition?: CanvasPosition) {
    appendStep({ kind: 'agent', agent }, preferredPosition)
  }

  function appendReflection(preferredPosition?: CanvasPosition) {
    appendStep(
      { kind: 'reflect_loop', reflector: 'reflector', researcher: 'researcher' },
      preferredPosition,
    )
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

  function connectNodes(source: string, target: string): string | undefined {
    if (!nodeKeys.includes(source) || !nodeKeys.includes(target) || source === target) return
    const cycle = cyclePath(source, target)
    if (cycle) {
      const labels = cycle.map((key) => {
        const index = nodeKeys.indexOf(key)
        return index >= 0 ? nodeTitle(steps[index], roles) : key
      })
      setGraphError(`无法连接：该依赖会形成循环 ${labels.join(' · ')}`)
      return
    }
    setGraphError('')
    const id = nextWorkflowEdgeId(workflowEdges, source, target)
    setWorkflowEdges((prev) => [...prev, { id, source, target, condition: null }])
    return id
  }

  function disconnectNodes(edgeId: string) {
    setGraphError('')
    setWorkflowEdges((prev) => prev.filter((edge) => edge.id !== edgeId))
  }

  function removeSelected() {
    if (selected < 0) return
    const removedKey = nodeKeys[selected]
    const remainingKeys = nodeKeys.filter((_, index) => index !== selected)
    const nextSelection = remainingKeys[Math.min(selected, remainingKeys.length - 1)] ?? null
    setSteps((prev) => prev.filter((_, i) => i !== selected))
    setNodeKeys(remainingKeys)
    setWorkflowEdges((prev) =>
      prev.filter((edge) => edge.source !== removedKey && edge.target !== removedKey),
    )
    setJoinModes((prev) => {
      const next = { ...prev }
      delete next[removedKey]
      return next
    })
    setPositions((prev) => {
      const next = { ...prev }
      delete next[removedKey]
      return next
    })
    setSelectedNodeId(nextSelection)
    setGraphError('')
  }

  function submit() {
    const nodes = steps.map((step, index) => ({
      id: nodeKeys[index],
      type: 'step',
      position: positions[nodeKeys[index]] ?? { x: 220, y: 80 + index * 150 },
      step,
      join_mode: joinModes[nodeKeys[index]] ?? 'any',
    }))
    const nodeSet = new Set(nodes.map((node) => node.id))
    const edges = workflowEdges
      .filter((edge) => nodeSet.has(edge.source) && nodeSet.has(edge.target))
      .map((edge) => ({
        ...edge,
        condition: edge.condition?.trim() || null,
      }))
    const body: WorkflowDefInput = {
      display_name: displayName.trim(),
      description: description.trim(),
      steps,
      nodes,
      edges,
      viewport: {
        x: viewport.x,
        y: viewport.y,
        zoom: viewport.zoom,
        input_position: positions[semanticNodeIds.input],
        output_position: positions[semanticNodeIds.output],
      },
      version: initial?.version ?? 1,
      enabled: initial?.enabled ?? true,
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
            <button className="btn ghost" onClick={onCancel} type="button"><AppIcon name="x" size={14} aria-hidden="true" />关闭</button>
            <button
              className="btn btn-primary"
              onClick={submit}
              disabled={pending || !!validation || (!editing && !name.trim())}
            >
              <AppIcon name={pending ? 'loader' : 'save'} size={15} aria-hidden="true" className={pending ? 'spin' : ''} />
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
                  title={role.description || undefined}
                  onDragStart={(event) => {
                    event.dataTransfer.effectAllowed = 'copy'
                    event.dataTransfer.setData('agent-role', role.name)
                  }}
                  onClick={() => appendAgent(role.name)}
                >
                   <span className="role-monogram"><AgentGlyph icon={role.icon} size={18} /></span>
                  <span>
                    <strong>{role.label}</strong>
                    <small>{role.name}{role.builtin ? ' · 内置' : ' · 自定义'}</small>
                    {role.description && <small className="role-item-desc">{role.description}</small>}
                  </span>
                   <span className="role-add"><AppIcon name="plus" size={15} aria-hidden="true" /></span>
                </button>
              ))}
            </div>
            <div className="workflow-library-divider" />
            <button
              className="workflow-role-item control"
              draggable
              onDragStart={(event) => {
                event.dataTransfer.effectAllowed = 'copy'
                event.dataTransfer.setData('workflow-control', 'reflect_loop')
              }}
              onClick={() => appendReflection()}
            >
               <span className="role-monogram"><AppIcon name="refresh" size={18} aria-hidden="true" /></span>
              <span><strong>反思循环</strong><small>评估证据并补充研究</small></span>
               <span className="role-add"><AppIcon name="plus" size={15} aria-hidden="true" /></span>
            </button>
          </aside>

          <main
            className={`workflow-canvas mobile-pane ${mobilePane === 'canvas' ? 'mobile-active' : ''}`}
          >
            <div className="canvas-toolbar">
              <span><strong>{steps.length}</strong> 节点 · <strong>{Object.values(dependencies).reduce((count, parents) => count + parents.length, 0)}</strong> 条依赖</span>
              <span>拖动卡片移动 · 从端口拖线或依次点击两端 · 选中实线后按 Delete</span>
              <span className="canvas-mode">DAG CANVAS</span>
            </div>
            <div className="flow-canvas-shell">
              <WorkflowFlowCanvas
                steps={steps}
                nodeKeys={nodeKeys}
                roles={roles}
                dependencies={dependencies}
                workflowEdges={workflowEdges}
                semanticNodeIds={semanticNodeIds}
                positions={positions}
                viewport={{ x: viewport.x, y: viewport.y, zoom: viewport.zoom }}
                fitViewOnInit={fitViewOnInit}
                selectedNodeId={selectedNodeId}
                onSelectNode={setSelectedNodeId}
                onPositionsChange={setPositions}
                onViewportChange={(nextViewport) =>
                  setViewport((currentViewport) => ({ ...currentViewport, ...nextViewport }))
                }
                onConnect={connectNodes}
                onDisconnect={disconnectNodes}
                onAddAgent={appendAgent}
                onAddReflection={appendReflection}
              />
            </div>
            {!steps.length && <div className="canvas-empty">从左侧角色库拖入第一个 Agent</div>}
            {graphError && (
              <div className="canvas-graph-error" role="alert">
                <span>{graphError}</span>
                <button type="button" onClick={() => setGraphError('')} aria-label="关闭提示">×</button>
              </div>
            )}
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
                      const target = nodeKeys[selected]
                      const matchingEdges = workflowEdges.filter(
                        (edge) => edge.source === key && edge.target === target,
                      )
                      const checked = matchingEdges.length > 0
                      return (
                        <div className="dependency-option" key={key}>
                          <label>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) => {
                                if (event.target.checked) {
                                  connectNodes(key, target)
                                } else {
                                  const edge = matchingEdges[matchingEdges.length - 1]
                                  if (edge) disconnectNodes(edge.id)
                                }
                              }}
                            />
                            <span>{index + 1}. {nodeTitle(step, roles)}</span>
                          </label>
                          {matchingEdges.map((edge) => (
                            <input
                              key={edge.id}
                              className="dependency-condition"
                              value={edge.condition ?? ''}
                              onChange={(event) =>
                                setWorkflowEdges((prev) =>
                                  prev.map((item) =>
                                    item.id === edge.id
                                      ? { ...item, condition: event.target.value }
                                      : item,
                                  ),
                                )
                              }
                              aria-label={`依赖条件 ${edge.id}`}
                              placeholder="可选条件，如 state.reflections.last.is_sufficient == true"
                            />
                          ))}
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
            {error && <p className="error-text"><AppIcon name="circle-x" size={14} aria-hidden="true" />{error}</p>}
          </aside>
        </div>
      </div>
    </div>
  )
}
