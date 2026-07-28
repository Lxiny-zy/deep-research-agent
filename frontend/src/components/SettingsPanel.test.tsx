import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import type { ResearchParams } from '../types'
import SettingsPanel from './SettingsPanel'

function Harness({ globalRequireCorroboration = false }: { globalRequireCorroboration?: boolean }) {
  const [value, setValue] = useState<ResearchParams>({})
  return (
    <SettingsPanel
      value={value}
      onChange={setValue}
      globalRequireCorroboration={globalRequireCorroboration}
    />
  )
}

describe('SettingsPanel 严格双源门禁覆盖', () => {
  it('沿用开启的全局值，并允许首次点击显式关闭', async () => {
    const user = userEvent.setup()
    render(<Harness globalRequireCorroboration />)

    await user.click(screen.getByRole('button', { name: /高级设置/ }))
    const gate = screen.getByRole('switch', { name: '本次研究启用严格双源门禁' })

    expect(gate).toBeChecked()
    expect(gate.closest('.toggle-switch')).toHaveClass('inherited')

    await user.click(gate)
    expect(gate).not.toBeChecked()
    expect(screen.getByText('本次研究已关闭')).toBeInTheDocument()
  })

  it('区分沿用全局、显式开启和显式关闭，并允许恢复默认', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('button', { name: /高级设置/ }))
    const gate = screen.getByRole('switch', { name: '本次研究启用严格双源门禁' })

    expect(gate).not.toBeChecked()
    expect(screen.getByText('沿用全局设置')).toBeInTheDocument()
    expect(gate.closest('.toggle-switch')).toHaveClass('inherited')

    await user.click(gate)
    expect(gate).toBeChecked()
    expect(screen.getByText('本次研究已开启')).toBeInTheDocument()

    await user.click(gate)
    expect(gate).not.toBeChecked()
    expect(screen.getByText('本次研究已关闭')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '沿用全局' }))
    expect(screen.getByText('沿用全局设置')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '沿用全局' })).not.toBeInTheDocument()
  })
})
