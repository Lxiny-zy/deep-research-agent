import { describe, expect, it, vi } from 'vitest'
import { beat, drawHalo } from './haloRender'

/** 只记录调用的 2D 上下文替身（jsdom 没有 canvas） */
function fakeContext() {
  const calls: string[] = []
  const record = (name: string) => () => {
    calls.push(name)
  }
  const ctx = {
    calls,
    clearRect: record('clearRect'),
    beginPath: record('beginPath'),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    arc: vi.fn(),
    stroke: record('stroke'),
    fill: record('fill'),
    globalAlpha: 1,
    globalCompositeOperation: 'source-over',
    lineWidth: 1,
    strokeStyle: '',
    fillStyle: '',
  }
  return ctx as unknown as CanvasRenderingContext2D & { calls: string[] }
}

const frame = {
  size: 400,
  radius: 102,
  time: 0,
  energy: 0,
  burst: 0,
  pointer: { angle: 0, s: 0 },
  dark: false,
}

describe('halo beat', () => {
  it('peaks on the downbeat, echoes once, then rests', () => {
    expect(beat(0)).toBeCloseTo(1, 2)
    expect(beat(2400)).toBeCloseTo(1, 2)
    expect(beat(480)).toBeGreaterThan(0.3)
    expect(beat(1200)).toBeLessThan(0.01)
  })
})

describe('drawHalo', () => {
  it('draws strands, rays, markers and dust without a conic gradient', () => {
    const ctx = fakeContext()
    drawHalo(ctx, frame)
    expect(ctx.calls[0]).toBe('clearRect')
    expect(ctx.calls.filter((c) => c === 'stroke').length).toBeGreaterThan(10)
    expect(ctx.calls.filter((c) => c === 'fill').length).toBeGreaterThan(14)
    // 画完恢复默认合成状态，避免影响下一帧
    expect(ctx.globalAlpha).toBe(1)
    expect(ctx.globalCompositeOperation).toBe('source-over')
  })

  it('pushes the pointer-facing arc further out', () => {
    const reach = (s: number) => {
      const ctx = fakeContext()
      // 指针朝向一处原本没有峰的方向（0.4 圈）
      const angle = Math.PI * 0.8
      drawHalo(ctx, { ...frame, pointer: { angle, s } })
      const lineTo = ctx.lineTo as unknown as ReturnType<typeof vi.fn>
      const near = lineTo.mock.calls.filter(
        ([x, y]: number[]) => Math.abs(Math.atan2(y - 200, x - 200) - angle) < 0.03,
      )
      return Math.max(...near.map(([x, y]: number[]) => Math.hypot(x - 200, y - 200)))
    }
    expect(reach(1)).toBeGreaterThan(reach(0) + 5)
  })
})
