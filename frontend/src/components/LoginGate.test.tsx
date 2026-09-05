import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { clearApiKey, getApiKey, isApiKeyRemembered, setApiKey } from '../api/client'
import LoginGate from './LoginGate'

describe('LoginGate', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    clearApiKey()
  })

  it('verifies before persisting and enters without reloading the page', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }))
    const onAuthenticated = vi.fn()
    render(<LoginGate onClose={vi.fn()} onAuthenticated={onAuthenticated} />)
    expect(screen.getByRole('checkbox')).toBeChecked()
    fireEvent.change(screen.getByLabelText('访问密钥'), { target: { value: 'valid-key' } })
    fireEvent.click(screen.getByRole('button', { name: '验证并进入' }))
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce())
    expect(getApiKey()).toBe('valid-key')
    expect(isApiKeyRemembered()).toBe(true)
  })

  it('does not replace a working credential when verification fails', async () => {
    setApiKey('working-key')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 401 }))
    const onAuthenticated = vi.fn()
    render(<LoginGate onClose={vi.fn()} onAuthenticated={onAuthenticated} />)
    fireEvent.change(screen.getByLabelText('访问密钥'), { target: { value: 'wrong-key' } })
    fireEvent.click(screen.getByRole('button', { name: '验证并进入' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('访问密钥无效')
    expect(getApiKey()).toBe('working-key')
    expect(onAuthenticated).not.toHaveBeenCalled()
  })

  it('supports session-only login and visibility controls', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }))
    const onAuthenticated = vi.fn()
    render(<LoginGate onClose={vi.fn()} onAuthenticated={onAuthenticated} />)
    fireEvent.change(screen.getByLabelText('访问密钥'), { target: { value: 'temporary-key' } })
    fireEvent.click(screen.getByRole('button', { name: '显示密钥' }))
    expect(screen.getByLabelText('访问密钥')).toHaveAttribute('type', 'text')
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: '验证并进入' }))
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce())
    expect(isApiKeyRemembered()).toBe(false)
    sessionStorage.clear()
    expect(getApiKey()).toBeNull()
  })

  it('cancels verification when the dialog is dismissed', async () => {
    let finish!: (response: Response) => void
    vi.spyOn(globalThis, 'fetch').mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const onAuthenticated = vi.fn()
    const { unmount } = render(<LoginGate onClose={vi.fn()} onAuthenticated={onAuthenticated} />)
    fireEvent.change(screen.getByLabelText('访问密钥'), { target: { value: 'abandoned-key' } })
    fireEvent.click(screen.getByRole('button', { name: '验证并进入' }))
    unmount()
    finish(new Response('{}', { status: 200 }))
    await waitFor(() => expect(getApiKey()).toBeNull())
    expect(onAuthenticated).not.toHaveBeenCalled()
  })
})
