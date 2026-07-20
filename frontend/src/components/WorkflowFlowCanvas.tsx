import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  useNodesState,
  type Connection,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
  type XYPosition,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { RoleInfo, WorkflowStep } from '../types'
import {
  assignEdgeRouting,
  buildRoutedEdgePath,
  canConnectNodes,
  layoutWorkflowNodes,
  primaryComponentKeys,
  workflowPathKeys,
} from './workflowCanvasLogic'

type CanvasNodeData = {
  label: string
  subtitle: string
  kind: string
  index: number
  orphan: boolean
  muted: boolean
  portInteractive: boolean
}

type CanvasEdgeData = {
  onDelete?: (source: string, target: string) => void
  sourceOffset?: number
  targetOffset?: number
  laneOffset?: number
  semantic?: boolean
}

type WorkflowCanvasEdge = Edge<CanvasEdgeData, 'workflowEdge'>

function AgentFlowNode({ data, selected }: NodeProps<Node<CanvasNodeData>>) {
  return (
    <div
      className={`flow-agent-node ${selected ? 'selected' : ''} ${data.kind} ${data.orphan ? 'orphan' : ''} ${data.muted ? 'muted' : ''}`}
      aria-label={`${data.label}${data.orphan ? '，尚未连接' : ''}`}
    >
      {data.kind !== 'input' && (
        <Handle
          type="target"
          position={Position.Top}
          isConnectable={data.portInteractive}
          className={data.portInteractive ? 'port-interactive' : 'port-disabled'}
          title={data.portInteractive ? '输入端口：拖入连线，或点击完成连接' : undefined}
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
      {data.orphan && <span className="flow-node-warning">未连接</span>}
      {data.kind !== 'output' && (
        <Handle
          type="source"
          position={Position.Bottom}
          isConnectable={data.portInteractive}
          className={data.portInteractive ? 'port-interactive' : 'port-disabled'}
          title={data.portInteractive ? '输出端口：拖出连线，或点击开始连接' : undefined}
        />
      )}
    </div>
  )
}

function EditableWorkflowEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  markerEnd,
  style,
  label,
  data,
  selected,
}: EdgeProps<WorkflowCanvasEdge>) {
  const { path: edgePath, labelX, labelY } = buildRoutedEdgePath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourceOffset: data?.sourceOffset ?? 0,
    targetOffset: data?.targetOffset ?? 0,
    laneOffset: data?.laneOffset ?? 0,
  })

  return (
    <>
      <path className="flow-edge-halo" d={edgePath} aria-hidden="true" />
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={style}
        label={label}
        interactionWidth={28}
      />
      {selected && data?.onDelete && !data.semantic && (
        <EdgeLabelRenderer>
          <button
            type="button"
            className="flow-edge-delete nodrag nopan"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
            onClick={(event) => {
              event.stopPropagation()
              data.onDelete?.(source, target)
            }}
            title="删除这条依赖线"
            aria-label="删除这条依赖线"
          >
            删除连线
          </button>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

const nodeTypes = { agentNode: AgentFlowNode }
const edgeTypes = { workflowEdge: EditableWorkflowEdge }

interface Props {
  steps: WorkflowStep[]
  nodeKeys: string[]
  roles: RoleInfo[]
  dependencies: Record<string, string[]>
  conditions: Record<string, string>
  positions: Record<string, XYPosition>
  selectedNodeId: string | null
  onSelectNode: (nodeId: string | null) => void
  onConnect: (source: string, target: string) => void
  onDisconnect: (source: string, target: string) => void
  onPositionsChange: (positions: Record<string, XYPosition>) => void
  onAddAgent: (agent: string, position?: XYPosition) => void
  onAddReflection: (position?: XYPosition) => void
}

export default function WorkflowFlowCanvas(props: Props) {
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<Node<CanvasNodeData>, Edge> | null>(null)
  const previousNodeKeys = useRef(new Set(props.nodeKeys))
  const {
    nodeKeys,
    dependencies,
    onConnect,
    onAddAgent,
    onAddReflection,
    onPositionsChange,
    positions,
  } = props

  const roleName = useCallback(
    (step: WorkflowStep) =>
      step.kind === 'reflect_loop'
        ? '反思循环'
        : (props.roles.find((role) => role.name === step.agent)?.label ?? step.agent ?? 'Agent'),
    [props.roles],
  )

  const primaryKeys = useMemo(
    () => primaryComponentKeys(props.nodeKeys, props.dependencies),
    [props.dependencies, props.nodeKeys],
  )
  const relatedNodeKeys = useMemo(
    () => workflowPathKeys(props.selectedNodeId, props.nodeKeys, props.dependencies),
    [props.dependencies, props.nodeKeys, props.selectedNodeId],
  )

  const buildNodes = useCallback(() => {
    const agentNodes = props.steps.map(
      (step, index): Node<CanvasNodeData> => ({
        id: props.nodeKeys[index],
        type: 'agentNode',
        position: props.positions[props.nodeKeys[index]] ?? { x: 220, y: 80 + index * 150 },
        selected: props.selectedNodeId === props.nodeKeys[index],
        draggable: true,
        deletable: false,
        data: {
          label: roleName(step),
          subtitle: step.kind === 'reflect_loop' ? '评估证据并补充研究' : (step.agent ?? ''),
          kind: step.kind,
          index,
          orphan: props.nodeKeys.length > 1 && !primaryKeys.has(props.nodeKeys[index]),
          muted: !!props.selectedNodeId && !relatedNodeKeys.has(props.nodeKeys[index]),
          portInteractive: true,
        },
      }),
    )

    const primaryNodes = agentNodes.filter((node) => primaryKeys.has(node.id))
    const layoutNodes = primaryNodes.length ? primaryNodes : agentNodes
    const xs = layoutNodes.map((node) => node.position.x)
    const ys = layoutNodes.map((node) => node.position.y)
    const centerX = xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : 220

    return [
      {
        id: '__input__',
        type: 'agentNode',
        selectable: false,
        deletable: false,
        draggable: true,
        position: props.positions.__input__ ?? {
          x: centerX,
          y: (ys.length ? Math.min(...ys) : 80) - 155,
        },
        data: {
          label: '用户输入',
          subtitle: '自动连接主流程的起始节点',
          kind: 'input',
          index: -1,
          orphan: false,
          muted: false,
          portInteractive: false,
        },
      },
      ...agentNodes,
      {
        id: '__output__',
        type: 'agentNode',
        selectable: false,
        deletable: false,
        draggable: true,
        position: props.positions.__output__ ?? {
          x: centerX,
          y: (ys.length ? Math.max(...ys) : 80) + 175,
        },
        data: {
          label: '结论输出',
          subtitle: '自动连接主流程的末端节点',
          kind: 'output',
          index: -1,
          orphan: false,
          muted: false,
          portInteractive: false,
        },
      },
    ]
  }, [
    primaryKeys,
    relatedNodeKeys,
    props.nodeKeys,
    props.positions,
    props.selectedNodeId,
    props.steps,
    roleName,
  ])

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<CanvasNodeData>>(buildNodes())

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
          position: props.positions[node.id] ?? existing.position,
        }
      })
    })
  }, [buildNodes, props.positions, setNodes])

  useEffect(() => {
    if (!flowInstance) return
    const previous = previousNodeKeys.current
    const addedKey = props.nodeKeys.find((key) => !previous.has(key))
    previousNodeKeys.current = new Set(props.nodeKeys)
    if (!addedKey) return

    const position = props.positions[addedKey]
    if (!position) return
    window.requestAnimationFrame(() => {
      flowInstance.setCenter(position.x + 115, position.y + 46, {
        zoom: Math.max(0.7, Math.min(flowInstance.getZoom(), 1.15)),
        duration: 280,
      })
    })
  }, [flowInstance, props.nodeKeys, props.positions])

  const workflowEdges = useMemo<WorkflowCanvasEdge[]>(
    () =>
      Object.entries(props.dependencies).flatMap(([target, sources]) =>
        sources
          .filter((source) => props.nodeKeys.includes(source) && props.nodeKeys.includes(target))
          .map((source) => {
            const related = relatedNodeKeys.has(source) && relatedNodeKeys.has(target)
            return {
              id: `${source}->${target}`,
              source,
              target,
              type: 'workflowEdge' as const,
              label: props.conditions[`${source}->${target}`] || undefined,
              animated: !!props.conditions[`${source}->${target}`],
              className: [
                props.conditions[`${source}->${target}`] ? 'conditional-edge' : '',
                props.selectedNodeId ? (related ? 'workflow-edge-related' : 'workflow-edge-muted') : '',
              ].filter(Boolean).join(' '),
              selected: selectedEdgeId === `${source}->${target}`,
              selectable: true,
              deletable: true,
              focusable: true,
              zIndex: selectedEdgeId === `${source}->${target}` ? 4 : related ? 2 : 1,
              markerEnd: {
                type: MarkerType.ArrowClosed,
                width: 18,
                height: 18,
                color: props.conditions[`${source}->${target}`] ? '#d8ff4f' : '#f7f2e8',
              },
              data: { onDelete: props.onDisconnect },
              ariaLabel: `依赖线：${source} 到 ${target}`,
            }
          }),
      ),
    [
      props.conditions,
      props.dependencies,
      props.nodeKeys,
      props.onDisconnect,
      props.selectedNodeId,
      relatedNodeKeys,
      selectedEdgeId,
    ],
  )

  const workflowEdgeIds = useMemo(() => new Set(workflowEdges.map((edge) => edge.id)), [workflowEdges])

  useEffect(() => {
    if (selectedEdgeId && !workflowEdgeIds.has(selectedEdgeId)) setSelectedEdgeId(null)
  }, [selectedEdgeId, workflowEdgeIds])

  const roots = props.nodeKeys.filter(
    (key) => primaryKeys.has(key) && !(props.dependencies[key] ?? []).some((source) => primaryKeys.has(source)),
  )
  const primaryParents = new Set(
    Object.entries(props.dependencies).flatMap(([target, sources]) =>
      primaryKeys.has(target) ? sources.filter((source) => primaryKeys.has(source)) : [],
    ),
  )
  const terminals = props.nodeKeys.filter((key) => primaryKeys.has(key) && !primaryParents.has(key))

  const semanticEdges: WorkflowCanvasEdge[] = [
    ...roots.map((target) => ({
      id: `__input__->${target}`,
      source: '__input__',
      target,
      type: 'workflowEdge' as const,
      className: 'semantic-edge',
      selectable: false,
      deletable: false,
      focusable: false,
      markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: '#d8ff4f' },
      data: { semantic: true },
      zIndex: 0,
      ariaLabel: `自动入口：用户输入到 ${target}`,
    })),
    ...terminals.map((source) => ({
      id: `${source}->__output__`,
      source,
      target: '__output__',
      type: 'workflowEdge' as const,
      className: 'semantic-edge',
      selectable: false,
      deletable: false,
      focusable: false,
      markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: '#d8ff4f' },
      data: { semantic: true },
      zIndex: 0,
      ariaLabel: `自动出口：${source} 到结论输出`,
    })),
  ]

  const allBaseEdges = [
    ...semanticEdges.slice(0, roots.length),
    ...workflowEdges,
    ...semanticEdges.slice(roots.length),
  ]
  const canvasPositions = Object.fromEntries(nodes.map((node) => [node.id, node.position]))
  const routing = assignEdgeRouting(allBaseEdges, canvasPositions)
  const edges: Edge[] = allBaseEdges.map((edge) => ({
    ...edge,
    data: {
      ...edge.data,
      ...(routing[`${edge.source}->${edge.target}`] ?? {}),
    },
  }))

  const connect = useCallback(
    (connection: Connection) => {
      if (
        connection.source &&
        connection.target &&
        canConnectNodes(nodeKeys, dependencies, connection.source, connection.target)
      ) {
        onConnect(connection.source, connection.target)
        setSelectedEdgeId(`${connection.source}->${connection.target}`)
      }
    },
    [dependencies, nodeKeys, onConnect],
  )

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      event.stopPropagation()
      if (!flowInstance) return

      const pointer = flowInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY })
      const position = { x: pointer.x - 115, y: pointer.y - 46 }
      const role = event.dataTransfer.getData('agent-role')
      const control = event.dataTransfer.getData('workflow-control')
      if (role) onAddAgent(role, position)
      if (control === 'reflect_loop') onAddReflection(position)
    },
    [flowInstance, onAddAgent, onAddReflection],
  )

  const handleAutoLayout = useCallback(() => {
    const arranged = layoutWorkflowNodes(nodeKeys, dependencies, positions)
    const arrangedPositions = Object.values(arranged)
    if (!arrangedPositions.length) return
    const centerX = arrangedPositions.reduce((total, position) => total + position.x, 0) / arrangedPositions.length
    const minimumY = Math.min(...arrangedPositions.map((position) => position.y))
    const maximumY = Math.max(...arrangedPositions.map((position) => position.y))
    onPositionsChange({
      ...positions,
      ...arranged,
      __input__: { x: centerX + 10, y: minimumY - 160 },
      __output__: { x: centerX + 10, y: maximumY + 190 },
    })
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        void flowInstance?.fitView({ padding: 0.28, duration: 420 })
      })
    })
  }, [dependencies, flowInstance, nodeKeys, onPositionsChange, positions])

  return (
    <ReactFlow<Node<CanvasNodeData>, Edge>
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onInit={setFlowInstance}
      onNodesChange={onNodesChange}
      onNodeDragStop={(_, draggedNode) => {
        props.onPositionsChange({
          ...props.positions,
          [draggedNode.id]: draggedNode.position,
        })
      }}
      onConnect={connect}
      isValidConnection={(connection) =>
        !!connection.source &&
        !!connection.target &&
        canConnectNodes(props.nodeKeys, props.dependencies, connection.source, connection.target)
      }
      onPaneClick={() => {
        setSelectedEdgeId(null)
      }}
      onEdgesDelete={(deleted) =>
        deleted.forEach((edge) => {
          if (!edge.source.startsWith('__') && !edge.target.startsWith('__')) {
            props.onDisconnect(edge.source, edge.target)
          }
        })
      }
      onEdgeClick={(_, edge) => {
        if (workflowEdgeIds.has(edge.id)) {
          props.onSelectNode(null)
          setSelectedEdgeId(edge.id)
        }
      }}
      onEdgeDoubleClick={(_, edge) => {
        if (workflowEdgeIds.has(edge.id)) props.onDisconnect(edge.source, edge.target)
      }}
      onNodeClick={(_, node) => {
        if (props.nodeKeys.includes(node.id)) {
          setSelectedEdgeId(null)
          props.onSelectNode(node.id)
        }
      }}
      onDragOver={(event) => {
        event.preventDefault()
        event.dataTransfer.dropEffect = 'copy'
      }}
      onDrop={handleDrop}
      fitView
      fitViewOptions={{ padding: 0.28 }}
      minZoom={0.25}
      maxZoom={1.8}
      deleteKeyCode={['Backspace', 'Delete']}
      nodesDraggable
      nodesConnectable
      connectOnClick
      elementsSelectable
      panOnDrag
      nodeDragThreshold={2}
      connectionRadius={28}
      proOptions={{ hideAttribution: true }}
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={22}
        size={1}
        color="rgba(128,160,180,.18)"
      />
      <Panel position="top-left" className="flow-canvas-legend">
        <span><i className="legend-line dependency" />可编辑依赖</span>
        <span><i className="legend-line semantic" />自动入口 / 出口</span>
        <span><i className="legend-node orphan" />未连接节点</span>
      </Panel>
      <Panel position="top-right" className="flow-canvas-actions">
        <button type="button" className="route-action" onClick={handleAutoLayout} title="按执行层级整理节点并减少连线交叉">
          自动整理
        </button>
        {selectedEdgeId ? (
          <button
            type="button"
            className="delete-action"
            onClick={() => {
              const edge = workflowEdges.find((item) => item.id === selectedEdgeId)
              if (edge) props.onDisconnect(edge.source, edge.target)
            }}
          >
            删除已选连线
          </button>
        ) : (
          <span>选中实线后按 Delete</span>
        )}
      </Panel>
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        zoomable
        position="bottom-right"
        nodeColor={(node) =>
          node.data.orphan ? '#ff6b45' : node.data.kind === 'reflect_loop' ? '#8f78b8' : '#4fae9d'
        }
        nodeStrokeColor="#b8d8d2"
        nodeStrokeWidth={1}
        maskColor="rgba(5, 9, 14, 0.72)"
        ariaLabel="工作流缩略导航图"
      />
    </ReactFlow>
  )
}
