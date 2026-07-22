export function primaryComponentKeys(
  nodeKeys: string[],
  dependencies: Record<string, string[]>,
): Set<string> {
  if (!nodeKeys.length) return new Set()

  const validKeys = new Set(nodeKeys)
  const neighbors = new Map(nodeKeys.map((key) => [key, [] as string[]]))
  for (const [target, sources] of Object.entries(dependencies)) {
    if (!validKeys.has(target)) continue
    for (const source of sources) {
      if (!validKeys.has(source)) continue
      neighbors.get(source)!.push(target)
      neighbors.get(target)!.push(source)
    }
  }

  const connected = new Set<string>()
  const queue = [nodeKeys[0]]
  while (queue.length) {
    const key = queue.shift()!
    if (connected.has(key)) continue
    connected.add(key)
    queue.push(...(neighbors.get(key) ?? []))
  }
  return connected
}

export function canConnectNodes(
  nodeKeys: string[],
  dependencies: Record<string, string[]>,
  source: string,
  target: string,
): boolean {
  if (source === target || !nodeKeys.includes(source) || !nodeKeys.includes(target)) return false

  const outgoing = new Map<string, string[]>()
  for (const [child, parents] of Object.entries(dependencies)) {
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
}

export function allocateSemanticEdgeId(
  usedIds: Set<string>,
  endpoint: 'input' | 'output',
  nodeId: string,
): string {
  const base = `__semantic__:${endpoint}:${nodeId}`
  let candidate = base
  let suffix = 2
  while (usedIds.has(candidate)) {
    candidate = `${base}:${suffix}`
    suffix += 1
  }
  usedIds.add(candidate)
  return candidate
}

export function allocateSemanticNodeIds(nodeIds: Iterable<string>): {
  input: string
  output: string
} {
  const used = new Set(nodeIds)
  const allocate = (base: string) => {
    let candidate = base
    let suffix = 2
    while (used.has(candidate)) {
      candidate = `${base}:${suffix}`
      suffix += 1
    }
    used.add(candidate)
    return candidate
  }
  return {
    input: allocate('__canvas_semantic__:input'),
    output: allocate('__canvas_semantic__:output'),
  }
}

export type CanvasPosition = { x: number; y: number }

export type RoutableEdge = {
  id: string
  source: string
  target: string
}

export type EdgeRouting = {
  sourceOffset: number
  targetOffset: number
  laneOffset: number
}

const NODE_CENTER_X = 115
const NODE_CENTER_Y = 46

function centeredOffset(index: number, count: number, spacing: number): number {
  return (index - (count - 1) / 2) * spacing
}

function nodeCenter(position: CanvasPosition | undefined): CanvasPosition {
  return {
    x: (position?.x ?? 0) + NODE_CENTER_X,
    y: (position?.y ?? 0) + NODE_CENTER_Y,
  }
}

/**
 * Assign stable fan-out, fan-in and horizontal-lane offsets to edges.
 * Edges in the same visual band never receive an identical route lane.
 */
export function assignEdgeRouting(
  edges: RoutableEdge[],
  positions: Record<string, CanvasPosition>,
): Record<string, EdgeRouting> {
  const routing = Object.fromEntries(
    edges.map((edge) => [
      edge.id,
      { sourceOffset: 0, targetOffset: 0, laneOffset: 0 },
    ]),
  ) as Record<string, EdgeRouting>

  const outgoing = new Map<string, RoutableEdge[]>()
  const incoming = new Map<string, RoutableEdge[]>()
  for (const edge of edges) {
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge])
    incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge])
  }

  for (const group of outgoing.values()) {
    group.sort((left, right) => {
      const leftTarget = nodeCenter(positions[left.target])
      const rightTarget = nodeCenter(positions[right.target])
      return leftTarget.x - rightTarget.x || leftTarget.y - rightTarget.y
    })
    group.forEach((edge, index) => {
      routing[edge.id].sourceOffset = centeredOffset(index, group.length, 24)
    })
  }

  for (const group of incoming.values()) {
    group.sort((left, right) => {
      const leftSource = nodeCenter(positions[left.source])
      const rightSource = nodeCenter(positions[right.source])
      return leftSource.x - rightSource.x || leftSource.y - rightSource.y
    })
    group.forEach((edge, index) => {
      routing[edge.id].targetOffset = centeredOffset(index, group.length, 24)
    })
  }

  const bands = new Map<number, RoutableEdge[]>()
  for (const edge of edges) {
    const source = nodeCenter(positions[edge.source])
    const target = nodeCenter(positions[edge.target])
    const band = Math.round(((source.y + target.y) / 2) / 90)
    bands.set(band, [...(bands.get(band) ?? []), edge])
  }

  for (const group of bands.values()) {
    group.sort((left, right) => {
      const leftSource = nodeCenter(positions[left.source])
      const rightSource = nodeCenter(positions[right.source])
      const leftTarget = nodeCenter(positions[left.target])
      const rightTarget = nodeCenter(positions[right.target])
      return leftSource.x - rightSource.x || leftTarget.x - rightTarget.x
    })
    group.forEach((edge, index) => {
      routing[edge.id].laneOffset = centeredOffset(index, group.length, 20)
    })
  }

  return routing
}

type RoutedPathInput = EdgeRouting & {
  sourceX: number
  sourceY: number
  targetX: number
  targetY: number
}

type Point = { x: number; y: number }

function compactOrthogonalPoints(points: Point[]): Point[] {
  const distinct = points.filter(
    (point, index) => index === 0 || point.x !== points[index - 1].x || point.y !== points[index - 1].y,
  )
  return distinct.filter((point, index) => {
    if (index === 0 || index === distinct.length - 1) return true
    const previous = distinct[index - 1]
    const next = distinct[index + 1]
    return !(
      (previous.x === point.x && point.x === next.x) ||
      (previous.y === point.y && point.y === next.y)
    )
  })
}

function roundedOrthogonalPath(points: Point[], radius = 10): string {
  const compact = compactOrthogonalPoints(points)
  if (compact.length < 2) return ''
  const number = (value: number) => Math.round(value * 10) / 10
  let path = `M ${number(compact[0].x)} ${number(compact[0].y)}`

  for (let index = 1; index < compact.length - 1; index += 1) {
    const previous = compact[index - 1]
    const current = compact[index]
    const next = compact[index + 1]
    const incoming = Math.abs(current.x - previous.x) + Math.abs(current.y - previous.y)
    const outgoing = Math.abs(next.x - current.x) + Math.abs(next.y - current.y)
    const bend = Math.min(radius, incoming / 2, outgoing / 2)
    const before = {
      x: current.x + Math.sign(previous.x - current.x) * Math.min(bend, Math.abs(previous.x - current.x)),
      y: current.y + Math.sign(previous.y - current.y) * Math.min(bend, Math.abs(previous.y - current.y)),
    }
    const after = {
      x: current.x + Math.sign(next.x - current.x) * Math.min(bend, Math.abs(next.x - current.x)),
      y: current.y + Math.sign(next.y - current.y) * Math.min(bend, Math.abs(next.y - current.y)),
    }
    path += ` L ${number(before.x)} ${number(before.y)}`
    path += ` Q ${number(current.x)} ${number(current.y)} ${number(after.x)} ${number(after.y)}`
  }

  const last = compact[compact.length - 1]
  return `${path} L ${number(last.x)} ${number(last.y)}`
}

/** Build an orthogonal route that fans out early and reserves a distinct middle lane. */
export function buildRoutedEdgePath(input: RoutedPathInput): {
  path: string
  labelX: number
  labelY: number
} {
  const { sourceX, sourceY, targetX, targetY, sourceOffset, targetOffset, laneOffset } = input
  const sourceStubY = sourceY + 24
  const targetStubY = targetY - 24
  const sourceLaneX = sourceX + sourceOffset
  const targetLaneX = targetX + targetOffset
  const hasVerticalRoom = targetY - sourceY >= 128

  if (hasVerticalRoom) {
    const minimumLane = sourceStubY + 16
    const maximumLane = targetStubY - 16
    const laneY = Math.max(
      minimumLane,
      Math.min(maximumLane, (sourceY + targetY) / 2 + laneOffset),
    )
    const points = [
      { x: sourceX, y: sourceY },
      { x: sourceX, y: sourceStubY },
      { x: sourceLaneX, y: sourceStubY },
      { x: sourceLaneX, y: laneY },
      { x: targetLaneX, y: laneY },
      { x: targetLaneX, y: targetStubY },
      { x: targetX, y: targetStubY },
      { x: targetX, y: targetY },
    ]
    return {
      path: roundedOrthogonalPath(points),
      labelX: (sourceLaneX + targetLaneX) / 2,
      labelY: laneY,
    }
  }

  const side = sourceX <= targetX ? 1 : -1
  const routeX =
    (side > 0 ? Math.max(sourceX, targetX) + 72 : Math.min(sourceX, targetX) - 72) +
    laneOffset
  const points = [
    { x: sourceX, y: sourceY },
    { x: sourceX, y: sourceStubY },
    { x: sourceLaneX, y: sourceStubY },
    { x: routeX, y: sourceStubY },
    { x: routeX, y: targetStubY },
    { x: targetLaneX, y: targetStubY },
    { x: targetX, y: targetStubY },
    { x: targetX, y: targetY },
  ]
  return {
    path: roundedOrthogonalPath(points),
    labelX: routeX,
    labelY: (sourceStubY + targetStubY) / 2,
  }
}

export function workflowPathKeys(
  selected: string | null,
  nodeKeys: string[],
  dependencies: Record<string, string[]>,
): Set<string> {
  if (!selected || !nodeKeys.includes(selected)) return new Set(nodeKeys)
  const parents = new Map(nodeKeys.map((key) => [key, [] as string[]]))
  const children = new Map(nodeKeys.map((key) => [key, [] as string[]]))
  for (const [target, sources] of Object.entries(dependencies)) {
    for (const source of sources) {
      if (!parents.has(target) || !children.has(source)) continue
      parents.get(target)!.push(source)
      children.get(source)!.push(target)
    }
  }

  const related = new Set([selected])
  for (const adjacent of [parents, children]) {
    const queue = [...(adjacent.get(selected) ?? [])]
    while (queue.length) {
      const current = queue.shift()!
      if (related.has(current)) continue
      related.add(current)
      queue.push(...(adjacent.get(current) ?? []))
    }
  }
  return related
}

/** Top-to-bottom DAG layout with a barycentric pass to reduce branch crossings. */
export function layoutWorkflowNodes(
  nodeKeys: string[],
  dependencies: Record<string, string[]>,
  currentPositions: Record<string, CanvasPosition>,
): Record<string, CanvasPosition> {
  if (!nodeKeys.length) return {}
  const valid = new Set(nodeKeys)
  const children = new Map(nodeKeys.map((key) => [key, [] as string[]]))
  const indegree = new Map(nodeKeys.map((key) => [key, 0]))
  const depth = new Map(nodeKeys.map((key) => [key, 0]))
  for (const [target, sources] of Object.entries(dependencies)) {
    if (!valid.has(target)) continue
    for (const source of sources) {
      if (!valid.has(source)) continue
      children.get(source)!.push(target)
      indegree.set(target, (indegree.get(target) ?? 0) + 1)
    }
  }

  const declarationOrder = new Map(nodeKeys.map((key, index) => [key, index]))
  const ready = nodeKeys.filter((key) => indegree.get(key) === 0)
  for (let index = 0; index < ready.length; index += 1) {
    const source = ready[index]
    for (const target of children.get(source) ?? []) {
      depth.set(target, Math.max(depth.get(target) ?? 0, (depth.get(source) ?? 0) + 1))
      indegree.set(target, (indegree.get(target) ?? 1) - 1)
      if (indegree.get(target) === 0) ready.push(target)
    }
  }

  const layers = new Map<number, string[]>()
  for (const key of nodeKeys) {
    const layer = depth.get(key) ?? 0
    layers.set(layer, [...(layers.get(layer) ?? []), key])
  }

  const placedOrder = new Map<string, number>()
  for (const layer of [...layers.keys()].sort((left, right) => left - right)) {
    const keys = layers.get(layer)!
    keys.sort((left, right) => {
      const leftParents = dependencies[left] ?? []
      const rightParents = dependencies[right] ?? []
      const average = (parents: string[]) =>
        parents.length
          ? parents.reduce((total, parent) => total + (placedOrder.get(parent) ?? 0), 0) / parents.length
          : declarationOrder.get(parents[0] ?? '') ?? 0
      return average(leftParents) - average(rightParents) ||
        (declarationOrder.get(left) ?? 0) - (declarationOrder.get(right) ?? 0)
    })
    keys.forEach((key, index) => placedOrder.set(key, index))
  }

  const existing = nodeKeys.map((key) => currentPositions[key]).filter(Boolean)
  const centerX = existing.length
    ? existing.reduce((total, position) => total + position.x, 0) / existing.length
    : 360
  const startY = existing.length
    ? Math.max(180, Math.min(...existing.map((position) => position.y)))
    : 180
  const positions: Record<string, CanvasPosition> = {}
  for (const [layer, keys] of [...layers.entries()].sort(([left], [right]) => left - right)) {
    const startX = centerX - ((keys.length - 1) * 310) / 2
    keys.forEach((key, index) => {
      positions[key] = { x: startX + index * 310, y: startY + layer * 190 }
    })
  }
  return positions
}
