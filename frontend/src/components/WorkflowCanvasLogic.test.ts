import {
  assignEdgeRouting,
  buildRoutedEdgePath,
  canConnectNodes,
  layoutWorkflowNodes,
  primaryComponentKeys,
  workflowPathKeys,
} from './workflowCanvasLogic'
import {
  findAvailableNodePosition,
  hasSingleTerminalAgent,
  reportAgentNames,
} from './workflowEditorLogic'

describe('workflow canvas interaction logic', () => {
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

  it('rejects duplicate and cyclic connections', () => {
    const nodeKeys = ['a', 'b', 'c']
    const dependencies = { a: [], b: ['a'], c: ['b'] }

    expect(canConnectNodes(nodeKeys, dependencies, 'a', 'b')).toBe(false)
    expect(canConnectNodes(nodeKeys, dependencies, 'c', 'a')).toBe(false)
    expect(canConnectNodes(nodeKeys, dependencies, 'a', 'c')).toBe(true)
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
        { source: 'a', target: 'b' },
        { source: 'a', target: 'c' },
        { source: 'b', target: 'd' },
        { source: 'c', target: 'd' },
      ],
      {
        a: { x: 300, y: 0 },
        b: { x: 100, y: 190 },
        c: { x: 500, y: 190 },
        d: { x: 300, y: 380 },
      },
    )

    expect(routing['a->b'].sourceOffset).not.toBe(routing['a->c'].sourceOffset)
    expect(routing['b->d'].targetOffset).not.toBe(routing['c->d'].targetOffset)
    expect(routing['a->b'].laneOffset).not.toBe(routing['a->c'].laneOffset)
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
