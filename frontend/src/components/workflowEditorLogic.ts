type CanvasPosition = { x: number; y: number }

const NODE_SPACING_X = 300
const NODE_SPACING_Y = 150
const NODE_COLLISION_X = 270
const NODE_COLLISION_Y = 132

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
