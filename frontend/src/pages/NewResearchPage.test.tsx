import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import type { WorkflowInfo } from '../types'
import NewResearchPage from './NewResearchPage'

const mocks = vi.hoisted(() => ({
  listWorkflows: vi.fn(),
  createRun: vi.fn(),
}))

vi.mock('../api/client', () => ({
  listWorkflows: mocks.listWorkflows,
  createRun: mocks.createRun,
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
    vi.clearAllMocks()
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
})
