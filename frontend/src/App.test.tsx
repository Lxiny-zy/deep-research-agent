import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { clearApiKey, getApiKey, setApiKey } from './api/client'
import App from './App'

vi.mock('./components/WelcomePage', () => ({
  default: ({ onEnter }: { onEnter: () => void }) => <button onClick={onEnter}>open login</button>,
}))

vi.mock('./components/LoginGate', () => ({
  default: () => <div data-testid="login-gate">login</div>,
}))

function renderApp() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<div data-testid="console">console</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('App authentication bootstrap', () => {
  afterEach(() => {
    clearApiKey()
    vi.restoreAllMocks()
  })

  it('enters an anonymous deployment when config is accessible without a key', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }))

    renderApp()

    expect(await screen.findByTestId('console')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/config',
      expect.not.objectContaining({ headers: expect.anything() }),
    )
    expect(screen.queryByTestId('login-gate')).not.toBeInTheDocument()
  })

  it('clears a stored key and opens login only for a 401 response', async () => {
    setApiKey('expired-key')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 401 }))

    renderApp()

    expect(await screen.findByTestId('login-gate')).toBeInTheDocument()
    expect(getApiKey()).toBeNull()
  })

  it('shows the welcome page without an automatic dialog on a first visit', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 401 }))
    renderApp()
    expect(await screen.findByRole('button', { name: 'open login' })).toBeInTheDocument()
    expect(screen.queryByTestId('login-gate')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'open login' }))
    expect(screen.getByTestId('login-gate')).toBeInTheDocument()
  })

  it('automatically authenticates a remembered key in a new tab session', async () => {
    setApiKey('remembered-key')
    sessionStorage.clear()
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }))
    renderApp()
    expect(await screen.findByTestId('console')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/config',
      expect.objectContaining({
        headers: { Authorization: 'Bearer remembered-key' },
      }),
    )
    expect(screen.queryByTestId('login-gate')).not.toBeInTheDocument()
  })

  it('keeps the key on a server error and allows the config check to be retried', async () => {
    setApiKey('still-valid')
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))

    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503')
    expect(getApiKey()).toBe('still-valid')
    expect(screen.queryByTestId('login-gate')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByTestId('console')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(getApiKey()).toBe('still-valid')
  })
})
