import { useCallback, useEffect } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  useNodesState,
  type Connection,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { RoleInfo, WorkflowStep } from '../types'

type CanvasNodeData = { label: string; subtitle: string; kind: string; index: number }

function AgentFlowNode({ data, selected }: NodeProps<Node<CanvasNodeData>>) {
  return (
    <div className={`flow-agent-node ${selected ? 'selected' : ''} ${data.kind}`}>
      {data.kind !== 'input' && <Handle type="target" position={Position.Top} />}
      <span className="flow-node-kind">
        {data.kind === 'reflect_loop' ? 'CONTROL' : data.kind === 'input' ? 'ENTRY' : data.kind === 'output' ? 'RESULT' : 'AGENT'}
      </span>
      <strong>{data.label}</strong>
      <small>{data.subtitle}</small>
      {data.index >= 0 && <span className="flow-node-index">{data.index + 1}</span>}
      {data.kind !== 'output' && <Handle type="source" position={Position.Bottom} />}
    </div>
  )
}

const nodeTypes = { agentNode: AgentFlowNode }

interface Props {
  steps: WorkflowStep[]
  nodeKeys: string[]
  roles: RoleInfo[]
  dependencies: Record<string, string[]>
  conditions: Record<string, string>
  positions: Record<string, { x: number; y: number }>
  selected: number
  onSelect: (index: number) => void
  onConnect: (source: string, target: string) => void
  onDisconnect: (source: string, target: string) => void
  onPositionsChange: (positions: Record<string, { x: number; y: number }>) => void
}

export default function WorkflowFlowCanvas(props: Props) {
  const roleName = useCallback(
    (step: WorkflowStep) =>
      step.kind === 'reflect_loop'
        ? '反思循环'
        : props.roles.find((role) => role.name === step.agent)?.label ?? step.agent ?? 'Agent',
    [props.roles],
  )
  const buildNodes = useCallback(
    () => {
      const agentNodes = props.steps.map((step, index): Node<CanvasNodeData> => ({
        id: props.nodeKeys[index],
        type: 'agentNode',
        position: props.positions[props.nodeKeys[index]] ?? { x: 220, y: 80 + index * 150 },
        selected: props.selected === index,
        data: {
          label: roleName(step),
          subtitle: step.kind === 'reflect_loop' ? '评估证据并补充研究' : step.agent ?? '',
          kind: step.kind,
          index,
        },
      }))
      const xs = agentNodes.map((node) => node.position.x)
      const ys = agentNodes.map((node) => node.position.y)
      const centerX = xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : 220
      return [
        {
          id: '__input__', type: 'agentNode', selectable: false, deletable: false, draggable: false,
          position: { x: centerX, y: (ys.length ? Math.min(...ys) : 80) - 155 },
          data: { label: '用户输入', subtitle: '研究问题与运行参数', kind: 'input', index: -1 },
        },
        ...agentNodes,
        {
          id: '__output__', type: 'agentNode', selectable: false, deletable: false, draggable: false,
          position: { x: centerX, y: (ys.length ? Math.max(...ys) : 80) + 175 },
          data: { label: '结论输出', subtitle: '汇总报告与引用来源', kind: 'output', index: -1 },
        },
      ]
    },
    [props.steps, props.nodeKeys, props.positions, props.selected, roleName],
  )
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<CanvasNodeData>>(buildNodes())

  // Keep React Flow's measured and interaction state. Replacing the complete
  // node array during selection or dragging can make every node disappear.
  useEffect(() => {
    const desired = buildNodes()
    setNodes((current) => {
      const currentById = new Map(current.map((node) => [node.id, node]))
      return desired.map((node) => {
        const existing = currentById.get(node.id)
        if (!existing) return node
        return {
          ...existing,
          type: node.type,
          selected: node.selected,
          data: node.data,
          position: existing.position,
        }
      })
    })
  }, [buildNodes, setNodes])
  const workflowEdges = Object.entries(props.dependencies).flatMap(([target, sources]) =>
    sources.map((source) => ({
      id: `${source}->${target}`,
      source,
      target,
      label: props.conditions[`${source}->${target}`] || undefined,
      animated: !!props.conditions[`${source}->${target}`],
      className: props.conditions[`${source}->${target}`] ? 'conditional-edge' : '',
    })),
  )
  const roots = props.nodeKeys.filter((key) => !(props.dependencies[key] ?? []).length)
  const parents = new Set(Object.values(props.dependencies).flat())
  const terminals = props.nodeKeys.filter((key) => !parents.has(key))
  const edges = [
    ...roots.map((target) => ({ id: `__input__->${target}`, source: '__input__', target, className: 'semantic-edge' })),
    ...workflowEdges,
    ...terminals.map((source) => ({ id: `${source}->__output__`, source, target: '__output__', className: 'semantic-edge' })),
  ]

  function connect(connection: Connection) {
    if (connection.source && connection.target && connection.source !== connection.target) {
      props.onConnect(connection.source, connection.target)
    }
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onNodeDragStop={(_, draggedNode) => {
        props.onPositionsChange({
          ...props.positions,
          [draggedNode.id]: draggedNode.position,
        })
      }}
      onConnect={connect}
      onEdgesDelete={(deleted) => deleted.forEach((edge) => props.onDisconnect(edge.source, edge.target))}
      onEdgeDoubleClick={(_, edge) => props.onDisconnect(edge.source, edge.target)}
      onNodeClick={(_, node) => {
        const index = props.nodeKeys.indexOf(node.id)
        if (index >= 0) props.onSelect(index)
      }}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.25}
      maxZoom={1.8}
      deleteKeyCode={['Backspace', 'Delete']}
      nodesDraggable
      nodesConnectable
      elementsSelectable
      panOnDrag
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="rgba(128,160,180,.18)" />
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        zoomable
        position="bottom-right"
        nodeColor={(node) => node.data.kind === 'reflect_loop' ? '#8f78b8' : '#4fae9d'}
        nodeStrokeColor="#b8d8d2"
        nodeStrokeWidth={1}
        maskColor="rgba(5, 9, 14, 0.72)"
        ariaLabel="工作流缩略导航图"
      />
    </ReactFlow>
  )
}
