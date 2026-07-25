import { useEffect, useId, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import LoginGate from './components/LoginGate'
import WelcomePage from './components/WelcomePage'
import { AppIcon, type AppIconName } from './components/AppIcon'
import { clearApiKey, getApiKey } from './api/client'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'nav-link active' : 'nav-link'

const navigation: { to: string; label: string; end?: boolean; icon: AppIconName }[] = [
  { to: '/', label: '新建研究', end: true, icon: 'sparkles' },
  { to: '/history', label: '研究历史', icon: 'history' },
  { to: '/workflows', label: '工作流构建', icon: 'workflow' },
  { to: '/agents', label: '角色广场', icon: 'users' },
  { to: '/settings', label: '全局设置', icon: 'settings' },
]

export default function App() {
  const navigationId = useId()
  const [showLogin, setShowLogin] = useState(false)
  const [authStatus, setAuthStatus] = useState<'checking' | 'guest' | 'verified' | 'error'>('checking')
  const [authError, setAuthError] = useState('')
  const [authAttempt, setAuthAttempt] = useState(0)
  const [navOpen, setNavOpen] = useState(false)
  const location = useLocation()

  useEffect(() => {
    setNavOpen(false)
  }, [location.pathname])

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
    return (
      <main className="boot-screen">
        <div className="boot-mark"><AppIcon name="network" size={26} /></div>
        <div>
          <span className="boot-kicker">DEEP RESEARCH / SYSTEM CHECK</span>
          <p>正在连接研究引擎…</p>
        </div>
        <AppIcon name="loader" size={18} className="spin" aria-label="正在连接" />
      </main>
    )
  }

  if (authStatus === 'error') {
    return (
      <main className="boot-screen boot-screen-error" role="alert">
        <div className="boot-mark"><AppIcon name="circle-x" size={26} /></div>
        <div>
          <span className="boot-kicker">CONNECTION INTERRUPTED</span>
          <p>{authError || '无法连接服务端'}</p>
        </div>
        <button className="btn btn-primary" onClick={retryAuth}>
          <AppIcon name="refresh" size={15} aria-hidden="true" />
          重试
        </button>
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
            <AppIcon name="network" size={24} strokeWidth={1.7} />
          </span>
          <span className="top-brand-copy">
            <strong>Deep Research</strong>
            <small>Multi-Agent System</small>
          </span>
        </NavLink>

        <button
          type="button"
          className="mobile-nav-toggle"
          aria-controls={navigationId}
          aria-expanded={navOpen}
          aria-label={navOpen ? '关闭导航' : '打开导航'}
          onClick={() => setNavOpen((open) => !open)}
        >
          <AppIcon name={navOpen ? 'x' : 'menu'} size={19} aria-hidden="true" />
        </button>

        <nav className={`top-navigation${navOpen ? ' is-open' : ''}`} id={navigationId} aria-label="主导航">
          {navigation.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={linkClass}>
              <AppIcon name={item.icon} size={15} aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="global-header-actions">
          <span className="current-page-label">{getPageTitle()}</span>
          <span className="system-status"><i /><AppIcon name="activity" size={13} aria-hidden="true" />在线</span>
          <button className="btn btn-ghost btn-sm api-access-button" onClick={() => setShowLogin(true)}>
            <AppIcon name="key" size={14} aria-hidden="true" />
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
