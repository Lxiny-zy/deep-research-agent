type CanvasPosition = { x: number; y: number }

type WorkflowEdgeLike = {
  id: string
  source: string
  target: string
  condition?: string | null
}

const NODE_SPACING_X = 300
const NODE_SPACING_Y = 150
const NODE_COLLISION_X = 270
const NODE_COLLISION_Y = 132

function edgeIdBase(source: string, target: string): string {
  const raw = `edge-${source}-${target}`
  if (raw.length <= 90) return raw

  let hash = 2166136261
  for (let index = 0; index < raw.length; index += 1) {
    hash ^= raw.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return `edge-${(hash >>> 0).toString(36)}`
}

/** Allocate an edge id once without changing the ids of existing edges. */
export function nextWorkflowEdgeId(
  edges: ReadonlyArray<Pick<WorkflowEdgeLike, 'id'>>,
  source: string,
  target: string,
): string {
  const used = new Set(edges.map((edge) => edge.id))
  const base = edgeIdBase(source, target)
  if (!used.has(base)) return base

  let suffix = 2
  let candidate = `${base.slice(0, 98)}-${suffix}`
  while (used.has(candidate)) {
    suffix += 1
    const suffixText = `-${suffix}`
    candidate = `${base.slice(0, 100 - suffixText.length)}${suffixText}`
  }
  return candidate
}

/**
 * Preserve persisted ids and conditions, repairing only missing or duplicate ids.
 * Parallel edges must remain independent throughout an edit/save round trip.
 */
export function normalizeWorkflowEdges<T extends WorkflowEdgeLike>(edges: ReadonlyArray<T>): T[] {
  const normalized: T[] = []
  for (const edge of edges) {
    const id =
      edge.id && !normalized.some((item) => item.id === edge.id)
        ? edge.id
        : nextWorkflowEdgeId(normalized, edge.source, edge.target)
    normalized.push({ ...edge, id })
  }
  return normalized
}

export function dependenciesFromEdges(
  nodeKeys: string[],
  edges: ReadonlyArray<Pick<WorkflowEdgeLike, 'source' | 'target'>>,
): Record<string, string[]> {
  const validKeys = new Set(nodeKeys)
  const dependencies = Object.fromEntries(nodeKeys.map((key) => [key, [] as string[]]))
  for (const edge of edges) {
    if (validKeys.has(edge.source) && validKeys.has(edge.target)) {
      dependencies[edge.target].push(edge.source)
    }
  }
  return dependencies
}

/** Return the nodes with no outgoing dependency (the graph's terminal nodes). */
export function terminalNodeKeys(
  nodeKeys: string[],
  dependencies: Record<string, string[]>,
): Set<string> {
  const validKeys = new Set(nodeKeys)
  const sources = new Set<string>()
  for (const [target, parents] of Object.entries(dependencies)) {
    if (!validKeys.has(target)) continue
    for (const source of parents) {
      if (validKeys.has(source)) sources.add(source)
    }
  }
  return new Set(nodeKeys.filter((key) => !sources.has(key)))
}

export function hasSingleTerminalAgent(
  steps: Array<{ kind: string; agent?: string }>,
  nodeKeys: string[],
  dependencies: Record<string, string[]>,
  agents: string | ReadonlySet<string>,
): boolean {
  const terminals = terminalNodeKeys(nodeKeys, dependencies)
  const reportAgents = typeof agents === 'string' ? new Set([agents]) : agents
  const matching = nodeKeys.filter(
    (_key, index) => steps[index]?.kind === 'agent' && reportAgents.has(steps[index]?.agent ?? ''),
  )
  return terminals.size === 1 && matching.length === 1 && terminals.has(matching[0])
}

export function reportAgentNames(
  roles: Array<{ name: string; produces_report?: boolean }>,
): Set<string> {
  return new Set(
    roles
      .filter((role) => role.produces_report ?? role.name === 'synthesizer')
      .map((role) => role.name),
  )
}

export function findAvailableNodePosition(
  existing: CanvasPosition[],
  anchor?: CanvasPosition,
  preferred?: CanvasPosition,
): CanvasPosition {
  const origin = preferred
    ? { x: Math.round(preferred.x / 20) * 20, y: Math.round(preferred.y / 20) * 20 }
    : anchor
      ? { x: anchor.x + NODE_SPACING_X, y: anchor.y }
      : { x: 220, y: 70 }

  const isFree = (candidate: CanvasPosition) =>
    !existing.some(
      (position) =>
        Math.abs(position.x - candidate.x) < NODE_COLLISION_X &&
        Math.abs(position.y - candidate.y) < NODE_COLLISION_Y,
    )

  for (let ring = 0; ring <= 12; ring += 1) {
    for (let column = -ring; column <= ring; column += 1) {
      for (let row = -ring; row <= ring; row += 1) {
        if (Math.max(Math.abs(column), Math.abs(row)) !== ring) continue
        const candidate = {
          x: Math.max(40, origin.x + column * NODE_SPACING_X),
          y: Math.max(40, origin.y + row * NODE_SPACING_Y),
        }
        if (isFree(candidate)) return candidate
      }
    }
  }

  return { x: origin.x + existing.length * NODE_SPACING_X, y: origin.y }
}
