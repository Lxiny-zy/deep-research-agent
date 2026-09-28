import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import ErrorPage, { NotFoundPage } from './ErrorPage'

function renderWithRouteError(loaderError: unknown) {
  const router = createMemoryRouter(
    [
      {
        path: '/',
        loader: () => {
          throw loaderError
        },
        element: <div />,
        errorElement: <ErrorPage />,
      },
    ],
    { initialEntries: ['/'] },
  )
  return render(<RouterProvider router={router} />)
}

describe('ErrorPage', () => {
  it('shows the not-found state for 404 route responses', async () => {
    renderWithRouteError(new Response('missing', { status: 404 }))
    expect(await screen.findByRole('heading', { name: '页面不存在' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /回到工作台/ })).toHaveAttribute('href', '/')
  })

  it('shows a recoverable alert for unexpected errors', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    renderWithRouteError(new Error('boom'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('页面暂时无法加载')
    expect(screen.getByRole('button', { name: /重新加载/ })).toBeInTheDocument()
    // 不向用户暴露内部错误信息
    expect(alert).not.toHaveTextContent('boom')
    spy.mockRestore()
  })
})

describe('NotFoundPage', () => {
  it('links back to the workbench and task history', () => {
    const router = createMemoryRouter([{ path: '/', element: <NotFoundPage /> }])
    render(<RouterProvider router={router} />)
    expect(screen.getByRole('status')).toHaveTextContent('404')
    expect(screen.getByRole('link', { name: '查看任务记录' })).toHaveAttribute('href', '/history')
  })
})
