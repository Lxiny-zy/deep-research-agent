import { useEffect, useId, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import LoginGate from './components/LoginGate'
import WelcomePage from './components/WelcomePage'
import OnboardingTour from './components/OnboardingTour'
import { hasSeenTour, markTourSeen } from './lib/onboarding'
import { AppIcon, type AppIconName } from './components/AppIcon'
import { clearApiKey, getApiKey, getApiKeyStorage } from './api/client'

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
  const [authStatus, setAuthStatus] = useState<'checking' | 'guest' | 'verified' | 'error'>(
    'checking',
  )
  const [authError, setAuthError] = useState('')
  const [authAttempt, setAuthAttempt] = useState(0)
  const [navOpen, setNavOpen] = useState(false)
  const [showTour, setShowTour] = useState(() => !hasSeenTour())
  const location = useLocation()
  const navigate = useNavigate()
  const closeTour = () => {
    markTourSeen()
    setShowTour(false)
  }
  const enterWorkspace = () => (authStatus === 'verified' ? navigate('/') : setShowLogin(true))
  const tour =
    showTour && !showLogin ? (
      <OnboardingTour
        onClose={closeTour}
        onComplete={() => {
          closeTour()
          enterWorkspace()
        }}
      />
    ) : null
  const keyStatus = {
    local: '密钥已记住',
    session: '仅本次会话',
    memory: '仅当前页面',
    none: '无需密钥',
  }[getApiKeyStorage()]

  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.key === 'dr_api_key' || event.key === null) setAuthAttempt((attempt) => attempt + 1)
    }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [])

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
      ...(key ? { headers: { Authorization: `Bearer ${key}` } } : {}),
      signal: controller.signal,
    })
      .then((response) => {
        if (controller.signal.aborted) return
        if (response.ok) {
          setAuthError('')
          setAuthStatus('verified')
          return
        }
        if (response.status === 401) {
          clearApiKey()
          setAuthError('')
          setAuthStatus('guest')
          setShowLogin(Boolean(key))
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

  const onAuthenticated = () => {
    setShowLogin(false)
    if (location.pathname === '/welcome' && getApiKey()) navigate('/')
    retryAuth()
  }

  if (authStatus === 'checking') {
    return (
      <main className="boot-screen">
        <div className="boot-mark">
          <AppIcon name="network" size={26} />
        </div>
        <div>
          <span className="boot-kicker">Deep Research / 系统检查</span>
          <p>正在连接研究引擎…</p>
        </div>
        <AppIcon name="loader" size={18} className="spin" aria-label="正在连接" />
      </main>
    )
  }

  if (authStatus === 'error') {
    return (
      <main className="boot-screen boot-screen-error" role="alert">
        <div className="boot-mark">
          <AppIcon name="circle-x" size={26} />
        </div>
        <div>
          <span className="boot-kicker">连接中断</span>
          <p>{authError || '无法连接服务端'}</p>
        </div>
        <button className="btn btn-primary" onClick={retryAuth}>
          <AppIcon name="refresh" size={15} aria-hidden="true" />
          重试
        </button>
      </main>
    )
  }

  if (authStatus === 'guest' || location.pathname === '/welcome') {
    return (
      <>
        <WelcomePage onEnter={enterWorkspace} onTour={() => setShowTour(true)} />
        {tour}
        {showLogin && (
          <LoginGate onClose={() => setShowLogin(false)} onAuthenticated={onAuthenticated} />
        )}
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
          className="compact-key-status"
          onClick={() => setShowLogin(true)}
          title="API 密钥管理"
        >
          <AppIcon name="key" size={14} aria-hidden="true" />
          {keyStatus}
        </button>

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

        <nav
          className={`top-navigation${navOpen ? ' is-open' : ''}`}
          id={navigationId}
          aria-label="主导航"
        >
          {navigation.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={linkClass}>
              <AppIcon name={item.icon} size={15} aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
          <button
            type="button"
            className="nav-link compact-nav-action"
            onClick={() => {
              setNavOpen(false)
              setShowTour(true)
            }}
          >
            <AppIcon name="help" size={15} aria-hidden="true" />
            入门引导
          </button>
          <NavLink
            to="/welcome"
            className="nav-link compact-nav-action"
            aria-label="欢迎页"
            title="欢迎页"
          >
            <AppIcon name="orbit" size={15} aria-hidden="true" />
            <span>欢迎页</span>
          </NavLink>
          <button
            type="button"
            className="nav-link compact-nav-action"
            aria-label="API 密钥管理"
            title="API 密钥管理"
            onClick={() => {
              setNavOpen(false)
              setShowLogin(true)
            }}
          >
            <AppIcon name="key" size={15} aria-hidden="true" />
            <span>API 密钥管理</span>
          </button>
        </nav>

        <div className="global-header-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm icon-button"
            onClick={() => setShowTour(true)}
            title="入门引导"
            aria-label="入门引导"
          >
            <AppIcon name="help" size={17} aria-hidden="true" />
          </button>
          <NavLink
            to="/welcome"
            className="btn btn-ghost btn-sm icon-button"
            aria-label="欢迎页"
            title="欢迎页"
          >
            <AppIcon name="orbit" size={17} aria-hidden="true" />
          </NavLink>
          <span className="current-page-label">{getPageTitle()}</span>
          <button
            className="btn btn-ghost btn-sm api-access-button"
            onClick={() => setShowLogin(true)}
            title="API 密钥管理"
          >
            <AppIcon name="key" size={14} aria-hidden="true" />
            {keyStatus}
          </button>
        </div>
      </header>

      <main className="main-content">
        <div className="content-area route-enter" key={location.pathname}>
          <Outlet />
        </div>
      </main>

      {tour}
      {showLogin && (
        <LoginGate onClose={() => setShowLogin(false)} onAuthenticated={onAuthenticated} />
      )}
    </div>
  )
}
