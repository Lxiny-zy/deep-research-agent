import { useEffect, useId, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import LoginGate from './components/LoginGate'
import WelcomePage from './components/WelcomePage'
import { clearApiKey, getApiKey } from './api/client'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'nav-link active' : 'nav-link'

const navigation = [
  { to: '/', label: '新建研究', end: true },
  { to: '/history', label: '研究历史' },
  { to: '/workflows', label: '工作流构建' },
  { to: '/agents', label: '角色广场' },
  { to: '/settings', label: '全局设置' },
]

export default function App() {
  const navigationId = useId()
  const [showLogin, setShowLogin] = useState(false)
  const [authStatus, setAuthStatus] = useState<'checking' | 'guest' | 'verified' | 'error'>('checking')
  const [authError, setAuthError] = useState('')
  const [authAttempt, setAuthAttempt] = useState(0)
  const location = useLocation()

  useEffect(() => {
    const onUnauthorized = () => {
      clearApiKey()
      setAuthError('')
      setAuthStatus('guest')
      setShowLogin(true)
    }
    window.addEventListener('dr:unauthorized', onUnauthorized)
    return () => window.removeEventListener('dr:unauthorized', onUnauthorized)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const key = getApiKey()

    setAuthStatus('checking')
    fetch('/api/config', {
      ...(key ? { headers: { 'X-API-Key': key } } : {}),
      signal: controller.signal,
    })
      .then((response) => {
        if (response.ok) {
          setAuthError('')
          setAuthStatus('verified')
          return
        }
        if (response.status === 401) {
          clearApiKey()
          setAuthError('')
          setAuthStatus('guest')
          setShowLogin(true)
          return
        }
        throw new Error(`无法加载服务配置（HTTP ${response.status}）`)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setAuthError(error instanceof Error ? error.message : '无法连接服务端')
        setAuthStatus('error')
      })
    return () => controller.abort()
  }, [authAttempt])

  const retryAuth = () => {
    setAuthError('')
    setAuthStatus('checking')
    setAuthAttempt((attempt) => attempt + 1)
  }

  if (authStatus === 'checking') {
    return <main className="visitor-welcome"><div className="panel">正在连接服务端…</div></main>
  }

  if (authStatus === 'error') {
    return (
      <main className="visitor-welcome">
        <div className="panel" role="alert">
          <p>{authError || '无法连接服务端'}</p>
          <button className="btn btn-primary" onClick={retryAuth}>重试</button>
        </div>
      </main>
    )
  }

  if (authStatus === 'guest') {
    return (
      <>
        <WelcomePage onEnter={() => setShowLogin(true)} />
        {showLogin && <LoginGate onClose={() => setShowLogin(false)} />}
      </>
    )
  }

  const getPageTitle = () => {
    if (location.pathname === '/') return '新建研究'
    if (location.pathname.startsWith('/runs/')) return '研究详情'
    if (location.pathname === '/history') return '研究历史'
    if (location.pathname === '/workflows') return '工作流构建器'
    if (location.pathname === '/agents') return '角色广场'
    if (location.pathname === '/settings') return '全局设置'
    return 'Deep Research Agent'
  }

  return (
    <div className="app-container top-navigation-layout signal-theme">
      <div className="ambient-stage" aria-hidden="true">
        <span className="ambient-block block-coral" />
        <span className="ambient-block block-blue" />
        <span className="ambient-block block-lime" />
        <span className="ambient-grid" />
      </div>
      <header className="global-header">
        <NavLink to="/" className="top-brand" aria-label="Deep Research 首页">
          <span className="brand-icon" aria-hidden="true">
            <svg width="28" height="28" viewBox="0 0 32 32" fill="none">
              <path d="M16 2L28 9V23L16 30L4 23V9L16 2Z" stroke="currentColor" strokeWidth="2" />
              <path d="M16 2V30M4 9L28 23M28 9L4 23" stroke="currentColor" strokeWidth="1.5" opacity="0.5" />
              <circle cx="16" cy="16" r="3" fill="currentColor" />
            </svg>
          </span>
          <span className="top-brand-copy">
            <strong>Deep Research</strong>
            <small>Multi-Agent System</small>
          </span>
        </NavLink>

        <nav className="top-navigation" id={navigationId} aria-label="主导航">
          {navigation.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={linkClass}>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="global-header-actions">
          <span className="current-page-label">{getPageTitle()}</span>
          <span className="system-status"><i />在线</span>
          <button className="btn btn-ghost btn-sm api-access-button" onClick={() => setShowLogin(true)}>
            API 管理
          </button>
        </div>
      </header>

      <main className="main-content">
        <div className="content-area">
          <Outlet />
        </div>
      </main>

      {showLogin && <LoginGate onClose={() => setShowLogin(false)} />}
    </div>
  )
}
