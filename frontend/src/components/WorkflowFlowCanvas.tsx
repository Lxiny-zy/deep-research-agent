import { useCallback, useEffect, useState } from 'react'
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

type PortKind = 'source' | 'target'

type CanvasNodeData = {
  label: string
  subtitle: string
  kind: string
  index: number
  portInteractive: boolean
  pendingSource: string | null
  validTarget: boolean
  onPortClick: (nodeId: string, port: PortKind) => void
}

function AgentFlowNode({ id, data, selected }: NodeProps<Node<CanvasNodeData>>) {
  const targetClass = data.pendingSource ? (data.validTarget ? 'port-valid' : 'port-invalid') : ''
  return (
    <div className={`flow-agent-node ${selected ? 'selected' : ''} ${data.kind}`}>
      {data.kind !== 'input' && (
        <Handle
          type="target"
          position={Position.Top}
          isConnectable={false}
          className={`${targetClass} ${data.portInteractive ? 'port-interactive' : 'port-disabled'}`}
          onClick={(event) => {
            event.stopPropagation()
            if (data.portInteractive) data.onPortClick(id, 'target')
          }}
        />
      )}
      <span className="flow-node-kind">
        {data.kind === 'reflect_loop'
          ? 'CONTROL'
          : data.kind === 'input'
            ? 'ENTRY'
            : data.kind === 'output'
              ? 'RESULT'
              : 'AGENT'}
      </span>
      <strong>{data.label}</strong>
      <small>{data.subtitle}</small>
      {data.index >= 0 && <span className="flow-node-index">{data.index + 1}</span>}
      {data.kind !== 'output' && (
        <Handle
          type="source"
          position={Position.Bottom}
          isConnectable={false}
          className={`${data.pendingSource === id ? 'port-active' : ''} ${data.portInteractive ? 'port-interactive' : 'port-disabled'}`}
          onClick={(event) => {
            event.stopPropagation()
            if (data.portInteractive) data.onPortClick(id, 'source')
          }}
        />
      )}
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
  const [activeNodeId, setActiveNodeId] = useState<string | null>(
    () => props.nodeKeys[props.selected] ?? null,
  )
  const [pendingSource, setPendingSource] = useState<string | null>(null)

  const isValidTarget = useCallback(
    (source: string, target: string) => {
      if (
        source === target ||
        !props.nodeKeys.includes(source) ||
        !props.nodeKeys.includes(target)
      ) {
        return false
      }
      if ((props.dependencies[target] ?? []).includes(source)) return false

      const outgoing = new Map<string, string[]>()
      for (const [child, parents] of Object.entries(props.dependencies)) {
        for (const parent of parents) {
          outgoing.set(parent, [...(outgoing.get(parent) ?? []), child])
        }
      }
      const queue = [target]
      const visited = new Set<string>()
      while (queue.length) {
        const current = queue.shift()!
        if (current === source) return false
        if (visited.has(current)) continue
        visited.add(current)
        queue.push(...(outgoing.get(current) ?? []))
      }
      return true
    },
    [props.dependencies, props.nodeKeys],
  )

  const handlePortClick = useCallback(
    (nodeId: string, port: PortKind) => {
      const index = props.nodeKeys.indexOf(nodeId)
      if (index < 0) return
      setActiveNodeId(nodeId)
      props.onSelect(index)

      if (port === 'source') {
        setPendingSource((current) => (current === nodeId ? null : nodeId))
        return
      }
      if (pendingSource && isValidTarget(pendingSource, nodeId)) {
        props.onConnect(pendingSource, nodeId)
        setPendingSource(null)
      }
    },
    [isValidTarget, pendingSource, props.nodeKeys, props.onConnect, props.onSelect],
  )

  const roleName = useCallback(
    (step: WorkflowStep) =>
      step.kind === 'reflect_loop'
        ? '反思循环'
        : (props.roles.find((role) => role.name === step.agent)?.label ?? step.agent ?? 'Agent'),
    [props.roles],
  )
  const buildNodes = useCallback(() => {
    const agentNodes = props.steps.map(
      (step, index): Node<CanvasNodeData> => ({
        id: props.nodeKeys[index],
        type: 'agentNode',
        position: props.positions[props.nodeKeys[index]] ?? { x: 220, y: 80 + index * 150 },
        selected: activeNodeId === props.nodeKeys[index],
        draggable: activeNodeId === props.nodeKeys[index],
        deletable: false,
        data: {
          label: roleName(step),
          subtitle: step.kind === 'reflect_loop' ? '评估证据并补充研究' : (step.agent ?? ''),
          kind: step.kind,
          index,
          portInteractive: true,
          pendingSource,
          validTarget: !!pendingSource && isValidTarget(pendingSource, props.nodeKeys[index]),
          onPortClick: handlePortClick,
        },
      }),
    )
    const xs = agentNodes.map((node) => node.position.x)
    const ys = agentNodes.map((node) => node.position.y)
    const centerX = xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : 220
    return [
      {
        id: '__input__',
        type: 'agentNode',
        selectable: true,
        deletable: false,
        selected: activeNodeId === '__input__',
        draggable: activeNodeId === '__input__',
        position: props.positions.__input__ ?? {
          x: centerX,
          y: (ys.length ? Math.min(...ys) : 80) - 155,
        },
        data: {
          label: '用户输入',
          subtitle: '研究问题与运行参数',
          kind: 'input',
          index: -1,
          portInteractive: false,
          pendingSource,
          validTarget: false,
          onPortClick: handlePortClick,
        },
      },
      ...agentNodes,
      {
        id: '__output__',
        type: 'agentNode',
        selectable: true,
        deletable: false,
        selected: activeNodeId === '__output__',
        draggable: activeNodeId === '__output__',
        position: props.positions.__output__ ?? {
          x: centerX,
          y: (ys.length ? Math.max(...ys) : 80) + 175,
        },
        data: {
          label: '结论输出',
          subtitle: '汇总报告与引用来源',
          kind: 'output',
          index: -1,
          portInteractive: false,
          pendingSource,
          validTarget: false,
          onPortClick: handlePortClick,
        },
      },
    ]
  }, [
    activeNodeId,
    handlePortClick,
    isValidTarget,
    pendingSource,
    props.steps,
    props.nodeKeys,
    props.positions,
    roleName,
  ])
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
          selectable: node.selectable,
          deletable: node.deletable,
          draggable: node.draggable,
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
    ...roots.map((target) => ({
      id: `__input__->${target}`,
      source: '__input__',
      target,
      className: 'semantic-edge',
      selectable: false,
      deletable: false,
      focusable: false,
    })),
    ...workflowEdges,
    ...terminals.map((source) => ({
      id: `${source}->__output__`,
      source,
      target: '__output__',
      className: 'semantic-edge',
      selectable: false,
      deletable: false,
      focusable: false,
    })),
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
      onPaneClick={() => {
        setActiveNodeId(null)
        setPendingSource(null)
      }}
      onEdgesDelete={(deleted) =>
        deleted.forEach((edge) => props.onDisconnect(edge.source, edge.target))
      }
      onEdgeDoubleClick={(_, edge) => {
        if (!edge.source.startsWith('__') && !edge.target.startsWith('__')) {
          props.onDisconnect(edge.source, edge.target)
        }
      }}
      onNodeClick={(_, node) => {
        setActiveNodeId(node.id)
        setPendingSource(null)
        const index = props.nodeKeys.indexOf(node.id)
        if (index >= 0) props.onSelect(index)
      }}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.25}
      maxZoom={1.8}
      deleteKeyCode={['Backspace', 'Delete']}
      nodesDraggable
      nodesConnectable={false}
      elementsSelectable
      panOnDrag
      proOptions={{ hideAttribution: true }}
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={22}
        size={1}
        color="rgba(128,160,180,.18)"
      />
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        zoomable
        position="bottom-right"
        nodeColor={(node) => (node.data.kind === 'reflect_loop' ? '#8f78b8' : '#4fae9d')}
        nodeStrokeColor="#b8d8d2"
        nodeStrokeWidth={1}
        maskColor="rgba(5, 9, 14, 0.72)"
        ariaLabel="工作流缩略导航图"
      />
    </ReactFlow>
  )
}
