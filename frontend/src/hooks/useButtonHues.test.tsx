import { act, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { HUE_COUNT, hueFor, useButtonHues } from './useButtonHues'

function Harness() {
  useButtonHues()
  const [more, setMore] = useState(false)
  return (
    <div>
      <button className="btn" onClick={() => setMore(true)}>
        开始研究
      </button>
      <button className="btn icon-button" aria-label="删除会话" />
      <button className="plain">不是按钮样式</button>
      {more && <button className="btn">后来出现</button>}
    </div>
  )
}

describe('hueFor', () => {
  it('is stable per label and stays in range', () => {
    expect(hueFor('开始研究')).toBe(hueFor('  开始研究 '))
    const hues = ['新会话', '提问', '保存', '取消', '导出', '重试', '编辑', '删除'].map(hueFor)
    hues.forEach((h) => expect(h).toBeGreaterThanOrEqual(0))
    hues.forEach((h) => expect(h).toBeLessThan(HUE_COUNT))
    // 常见标签不会全挤在同一个色上
    expect(new Set(hues).size).toBeGreaterThan(3)
  })
})

describe('useButtonHues', () => {
  it('tags .btn elements, including ones rendered later', async () => {
    render(<Harness />)
    const start = screen.getByRole('button', { name: '开始研究' })
    expect(start.dataset.hue).toBe(String(hueFor('开始研究')))
    // 图标按钮没有文字时用无障碍名称
    expect(screen.getByRole('button', { name: '删除会话' }).dataset.hue).toBe(
      String(hueFor('删除会话')),
    )
    expect(screen.getByRole('button', { name: '不是按钮样式' }).dataset.hue).toBeUndefined()

    act(() => start.click())
    await act(() => new Promise((resolve) => requestAnimationFrame(() => resolve(null))))
    expect(screen.getByRole('button', { name: '后来出现' }).dataset.hue).toBeDefined()
  })
})
