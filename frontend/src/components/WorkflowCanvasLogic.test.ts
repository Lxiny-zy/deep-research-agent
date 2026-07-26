import {
  allocateSemanticEdgeId,
  allocateSemanticNodeIds,
  assignEdgeRouting,
  buildRoutedEdgePath,
  canConnectNodes,
  layoutWorkflowNodes,
  mergeDraggedNodePositions,
  primaryComponentKeys,
  workflowPathKeys,
} from './workflowCanvasLogic'
import {
  dependenciesFromEdges,
  findAvailableNodePosition,
  hasSingleTerminalAgent,
  nextWorkflowEdgeId,
  normalizeWorkflowEdges,
  reportAgentNames,
} from './workflowEditorLogic'

describe('workflow canvas interaction logic', () => {
  it('preserves parallel edge ids and independent conditions', () => {
    const edges = normalizeWorkflowEdges([
      { id: 'first', source: 'a', target: 'b', condition: 'state.first' },
      { id: 'second', source: 'a', target: 'b', condition: 'state.second' },
    ])

    expect(edges.map((edge) => [edge.id, edge.condition])).toEqual([
      ['first', 'state.first'],
      ['second', 'state.second'],
    ])
    expect(dependenciesFromEdges(['a', 'b'], edges)).toEqual({ a: [], b: ['a', 'a'] })
    expect(nextWorkflowEdgeId(edges, 'a', 'b')).toBe('edge-a-b')
  })

  it('repairs duplicate edge ids without rewriting the first persisted id', () => {
    const edges = normalizeWorkflowEdges([
      { id: 'persisted', source: 'a', target: 'b', condition: null },
      { id: 'persisted', source: 'a', target: 'b', condition: 'state.ready' },
    ])

    expect(edges[0].id).toBe('persisted')
    expect(edges[1]).toMatchObject({
      id: 'edge-a-b',
      source: 'a',
      target: 'b',
      condition: 'state.ready',
    })
  })

  it('places an added node beside the selected node without overlapping existing cards', () => {
    const position = findAvailableNodePosition(
      [
        { x: 220, y: 70 },
        { x: 220, y: 220 },
        { x: 220, y: 370 },
      ],
      { x: 220, y: 70 },
    )

    expect(position).toEqual({ x: 520, y: 70 })
  })

  it('respects a dropped position while moving away from an occupied card', () => {
    const position = findAvailableNodePosition(
      [{ x: 500, y: 200 }],
      undefined,
      { x: 500, y: 200 },
    )

    expect(position).not.toEqual({ x: 500, y: 200 })
    expect(Math.abs(position.x - 500) >= 270 || Math.abs(position.y - 200) >= 132).toBe(true)
  })

  it('requires the synthesizer to have no downstream node', () => {
    const steps = [
      { kind: 'agent', agent: 'synthesizer' },
      { kind: 'agent', agent: 'researcher' },
    ]
    const nodeKeys = ['synth', 'research']

    expect(
      hasSingleTerminalAgent(
        steps,
        nodeKeys,
        { synth: [], research: ['synth'] },
        'synthesizer',
      ),
    ).toBe(false)
    expect(
      hasSingleTerminalAgent(
        steps,
        nodeKeys,
        { synth: ['research'], research: [] },
        'synthesizer',
      ),
    ).toBe(true)
  })

  it('requires every branch to merge into a synthesizer', () => {
    const steps = [
      { kind: 'agent', agent: 'planner' },
      { kind: 'agent', agent: 'researcher' },
      { kind: 'agent', agent: 'synthesizer' },
    ]

    expect(
      hasSingleTerminalAgent(
        steps,
        ['planner', 'research', 'synth'],
        { planner: [], research: ['planner'], synth: ['planner'] },
        'synthesizer',
      ),
    ).toBe(false)
  })

  it('rejects an early synthesizer even when another synthesizer is terminal', () => {
    const steps = [
      { kind: 'agent', agent: 'synthesizer' },
      { kind: 'agent', agent: 'researcher' },
      { kind: 'agent', agent: 'synthesizer' },
    ]

    expect(
      hasSingleTerminalAgent(
        steps,
        ['early-synth', 'research', 'final-synth'],
        { 'early-synth': [], research: ['early-synth'], 'final-synth': ['research'] },
        'synthesizer',
      ),
    ).toBe(false)
  })

  it('supports custom report roles while counting every report-producing node', () => {
    const reportAgents = reportAgentNames([
      { name: 'synthesizer' },
      { name: 'my-writer', produces_report: true },
      { name: 'critic', produces_report: false },
    ])
    expect([...reportAgents]).toEqual(['synthesizer', 'my-writer'])
    expect(
      hasSingleTerminalAgent(
        [
          { kind: 'agent', agent: 'researcher' },
          { kind: 'agent', agent: 'my-writer' },
        ],
        ['research', 'writer'],
        { research: [], writer: ['research'] },
        reportAgents,
      ),
    ).toBe(true)
    expect(
      hasSingleTerminalAgent(
        [
          { kind: 'agent', agent: 'my-writer' },
          { kind: 'agent', agent: 'synthesizer' },
        ],
        ['writer', 'synth'],
        { writer: [], synth: ['writer'] },
        reportAgents,
      ),
    ).toBe(false)
  })

  it('lets explicit capability metadata override the legacy synthesizer fallback', () => {
    expect(
      reportAgentNames([{ name: 'synthesizer', produces_report: false }]),
    ).toEqual(new Set())
  })

  it('allows parallel connections while still rejecting cycles', () => {
    const nodeKeys = ['a', 'b', 'c']
    const dependencies = { a: [], b: ['a'], c: ['b'] }

    expect(canConnectNodes(nodeKeys, dependencies, 'a', 'b')).toBe(true)
    expect(canConnectNodes(nodeKeys, dependencies, 'c', 'a')).toBe(false)
    expect(canConnectNodes(nodeKeys, dependencies, 'a', 'c')).toBe(true)
  })

  it('allocates semantic edge ids outside persisted edge id collisions', () => {
    const used = new Set([
      '__semantic__:input:a',
      '__semantic__:input:a:2',
      '__semantic__:output:b',
    ])

    expect(allocateSemanticEdgeId(used, 'input', 'a')).toBe('__semantic__:input:a:3')
    expect(allocateSemanticEdgeId(used, 'output', 'b')).toBe('__semantic__:output:b:2')
    expect(used.size).toBe(5)
  })

  it('allocates semantic node ids outside workflow node collisions', () => {
    expect(
      allocateSemanticNodeIds([
        '__canvas_semantic__:input',
        '__canvas_semantic__:input:2',
        '__canvas_semantic__:output',
      ]),
    ).toEqual({
      input: '__canvas_semantic__:input:3',
      output: '__canvas_semantic__:output:2',
    })
  })

  it('keeps generated edge ids within the API length limit', () => {
    const source = 's'.repeat(100)
    const target = 't'.repeat(100)
    const first = nextWorkflowEdgeId([], source, target)
    const second = nextWorkflowEdgeId([{ id: first }], source, target)

    expect(first.length).toBeLessThanOrEqual(100)
    expect(second.length).toBeLessThanOrEqual(100)
    expect(second).not.toBe(first)
  })

  it('uses an ASCII edge id for long Unicode node ids', () => {
    const id = nextWorkflowEdgeId([], `a${'😀'.repeat(99)}`, '终'.repeat(100))

    expect(id).toMatch(/^edge-[a-z0-9]+$/)
    expect(id.length).toBeLessThanOrEqual(100)
  })

  it('finds the main component and leaves newly added orphan nodes outside it', () => {
    const connected = primaryComponentKeys(
      ['a', 'b', 'c', 'new'],
      { a: [], b: ['a'], c: ['b'], new: [] },
    )

    expect([...connected]).toEqual(['a', 'b', 'c'])
  })

  it('fans branch and merge edges into separate visual lanes', () => {
    const routing = assignEdgeRouting(
      [
        { id: 'a-b', source: 'a', target: 'b' },
        { id: 'a-c', source: 'a', target: 'c' },
        { id: 'b-d', source: 'b', target: 'd' },
        { id: 'c-d', source: 'c', target: 'd' },
      ],
      {
        a: { x: 300, y: 0 },
        b: { x: 100, y: 190 },
        c: { x: 500, y: 190 },
        d: { x: 300, y: 380 },
      },
    )

    expect(routing['a-b'].sourceOffset).not.toBe(routing['a-c'].sourceOffset)
    expect(routing['b-d'].targetOffset).not.toBe(routing['c-d'].targetOffset)
    expect(routing['a-b'].laneOffset).not.toBe(routing['a-c'].laneOffset)
  })

  it('keeps parallel edge routing and conditions addressable by stable ids', () => {
    const routing = assignEdgeRouting(
      [
        { id: 'edge-a-b-1', source: 'a', target: 'b' },
        { id: 'edge-a-b-2', source: 'a', target: 'b' },
      ],
      { a: { x: 100, y: 0 }, b: { x: 100, y: 190 } },
    )

    expect(routing['edge-a-b-1']).toBeDefined()
    expect(routing['edge-a-b-2']).toBeDefined()
    expect(routing['edge-a-b-1'].sourceOffset).not.toBe(routing['edge-a-b-2'].sourceOffset)
    expect(routing['edge-a-b-1'].targetOffset).not.toBe(routing['edge-a-b-2'].targetOffset)
  })

  it('builds distinct rounded orthogonal paths for neighboring route lanes', () => {
    const first = buildRoutedEdgePath({
      sourceX: 200,
      sourceY: 100,
      targetX: 400,
      targetY: 320,
      sourceOffset: -12,
      targetOffset: -12,
      laneOffset: -10,
    })
    const second = buildRoutedEdgePath({
      sourceX: 200,
      sourceY: 100,
      targetX: 400,
      targetY: 320,
      sourceOffset: 12,
      targetOffset: 12,
      laneOffset: 10,
    })

    expect(first.path).toContain(' Q ')
    expect(first.path).not.toBe(second.path)
    expect(first.labelY).not.toBe(second.labelY)
  })

  it('keeps reverse or tightly spaced connections on separate side lanes', () => {
    const leftLane = buildRoutedEdgePath({
      sourceX: 200,
      sourceY: 260,
      targetX: 360,
      targetY: 180,
      sourceOffset: 0,
      targetOffset: 0,
      laneOffset: -10,
    })
    const rightLane = buildRoutedEdgePath({
      sourceX: 200,
      sourceY: 260,
      targetX: 360,
      targetY: 180,
      sourceOffset: 0,
      targetOffset: 0,
      laneOffset: 10,
    })

    expect(leftLane.labelX).not.toBe(rightLane.labelX)
    expect(leftLane.path).not.toBe(rightLane.path)
  })

  it('keeps only upstream and downstream nodes in the selected workflow path', () => {
    const related = workflowPathKeys(
      'b',
      ['a', 'b', 'c', 'd'],
      { a: [], b: ['a'], c: ['a'], d: ['b'] },
    )

    expect([...related].sort()).toEqual(['a', 'b', 'd'])
  })

  it('persists every node of a multi-selection drag, not just the anchor', () => {
    const merged = mergeDraggedNodePositions(
      { a: { x: 0, y: 0 }, b: { x: 300, y: 0 }, c: { x: 600, y: 0 } },
      [
        { id: 'a', position: { x: 40, y: 160 } },
        { id: 'b', position: { x: 340, y: 160 } },
      ],
    )

    expect(merged).toEqual({
      a: { x: 40, y: 160 },
      b: { x: 340, y: 160 },
      c: { x: 600, y: 0 },
    })
  })

  it('merges a single-node drag without touching other persisted positions', () => {
    const positions = { a: { x: 0, y: 0 }, b: { x: 300, y: 0 } }
    const merged = mergeDraggedNodePositions(positions, [
      { id: 'b', position: { x: 320, y: 90 } },
    ])

    expect(merged).toEqual({ a: { x: 0, y: 0 }, b: { x: 320, y: 90 } })
    expect(positions.b).toEqual({ x: 300, y: 0 })
  })

  it('lays a DAG out by execution depth and separates parallel nodes', () => {
    const layout = layoutWorkflowNodes(
      ['a', 'b', 'c', 'd'],
      { a: [], b: ['a'], c: ['a'], d: ['b', 'c'] },
      {},
    )

    expect(layout.a.y).toBeLessThan(layout.b.y)
    expect(layout.b.y).toBe(layout.c.y)
    expect(layout.b.x).not.toBe(layout.c.x)
    expect(layout.d.y).toBeGreaterThan(layout.b.y)
  })
})
