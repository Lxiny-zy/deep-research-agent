import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import type { WorkflowInfo } from '../types'
import NewResearchPage from './NewResearchPage'

const mocks = vi.hoisted(() => ({
  listWorkflows: vi.fn(),
  createRun: vi.fn(),
  useConfig: vi.fn(),
}))

vi.mock('../api/client', () => ({
  listWorkflows: mocks.listWorkflows,
  createRun: mocks.createRun,
}))

vi.mock('../hooks/useConfig', () => ({
  useConfig: mocks.useConfig,
}))

const WORKFLOWS: WorkflowInfo[] = [
  { name: 'alpha', description: '内置默认流程', default: 'True' },
  { name: 'beta', description: '自定义流程', default: 'False', custom: 'True' },
]

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

function Harness() {
  const navigate = useNavigate()
  return (
    <>
      <button type="button" data-testid="go-beta" onClick={() => navigate('/?workflow=beta')}>
        go beta
      </button>
      <NewResearchPage />
    </>
  )
}

describe('NewResearchPage workflow list race', () => {
  beforeEach(() => {
    mocks.listWorkflows.mockReset()
    mocks.createRun.mockReset()
    mocks.useConfig.mockReset()
    mocks.useConfig.mockReturnValue({ data: { require_corroboration: false } })
  })

  it('ignores a late response from a superseded listWorkflows request', async () => {
    const first = deferred<WorkflowInfo[]>()
    const second = deferred<WorkflowInfo[]>()
    mocks.listWorkflows
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)

    render(
      <MemoryRouter initialEntries={['/']}>
        <Harness />
      </MemoryRouter>,
    )

    // 导航到 ?workflow=beta 触发第二次请求；第二次先返回并选中 beta。
    fireEvent.click(screen.getByTestId('go-beta'))
    expect(mocks.listWorkflows).toHaveBeenCalledTimes(2)
    await act(async () => {
      second.resolve(WORKFLOWS)
      await Promise.resolve()
    })
    expect(screen.getByRole('combobox')).toHaveValue('beta')

    // 第一次请求的迟到响应不得把选择弹回默认的 alpha。
    await act(async () => {
      first.resolve(WORKFLOWS)
      await Promise.resolve()
    })
    expect(screen.getByRole('combobox')).toHaveValue('beta')
  })

  it('把严格双源门禁的单次覆盖随创建请求提交', async () => {
    mocks.listWorkflows.mockResolvedValue(WORKFLOWS)
    mocks.createRun.mockResolvedValue({ run_id: 'run-1' })

    render(
      <MemoryRouter initialEntries={['/']}>
        <NewResearchPage />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /高级设置/ }))
    fireEvent.click(screen.getByRole('switch', { name: '本次研究启用严格双源门禁' }))
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }))

    await waitFor(() =>
      expect(mocks.createRun).toHaveBeenCalledWith(
        expect.objectContaining({ params: { require_corroboration: true } }),
      ),
    )
  })

  it('在单次覆盖为空时展示开启的全局门禁状态', async () => {
    mocks.listWorkflows.mockReturnValue(new Promise<WorkflowInfo[]>(() => {}))
    mocks.useConfig.mockReturnValue({ data: { require_corroboration: true } })

    render(
      <MemoryRouter initialEntries={['/']}>
        <NewResearchPage />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /高级设置/ }))

    expect(
      screen.getByRole('switch', { name: '本次研究启用严格双源门禁' }),
    ).toBeChecked()
  })
})
