import { fireEvent, render, screen } from '@testing-library/react'
import ModelProfileEditor from './ModelProfileEditor'

vi.mock('../hooks/useCatalog', () => ({
  useModelProbe: () => ({ test: { mutate: vi.fn() }, discover: { mutate: vi.fn() } }),
}))

it('saves per-model context/output capacities and can clear output to the provider default', () => {
  const save = vi.fn()
  render(<ModelProfileEditor onSubmit={save} onCancel={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('档案名'), { target: { value: 'DeepSeek flash' } })
  fireEvent.change(screen.getByLabelText('上下文容量（token）'), { target: { value: '1000000' } })
  fireEvent.change(screen.getByLabelText('最大输出（token）'), { target: { value: '65536' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({ context_window_tokens: 1000000, max_output_tokens: 65536 }),
  )
  fireEvent.change(screen.getByLabelText('最大输出（token）'), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({ context_window_tokens: 1000000, max_output_tokens: null }),
  )
})

it('rejects inconsistent capacities instead of saving an impossible request', () => {
  render(<ModelProfileEditor onSubmit={vi.fn()} onCancel={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('档案名'), { target: { value: 'test' } })
  fireEvent.change(screen.getByLabelText('上下文容量（token）'), { target: { value: '1000' } })
  fireEvent.change(screen.getByLabelText('最大输出（token）'), { target: { value: '2000' } })
  expect(screen.getByRole('alert')).toHaveTextContent('最大输出必须小于上下文容量')
  expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
})
