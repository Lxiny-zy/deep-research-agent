import { useEffect, useId, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import LoginGate from './components/LoginGate'
import WelcomePage from './components/WelcomePage'
import OnboardingTour from './components/OnboardingTour'
import { hasSeenTour, markTourSeen } from './lib/onboarding'
import { AppIcon, type AppIconName } from './components/AppIcon'
import {
  ApiError,
  clearApiKey,
  clearWorkspaceState,
  getApiKey,
  getApiKeyStorage,
  getConfig,
} from './api/client'
import WorkspaceAtmosphere from './components/WorkspaceAtmosphere'
import ResearchMotif, { type MotifKind } from './components/ResearchMotif'
import { useAmbientMotion } from './hooks/useAmbientMotion'

function motifForPath(path: string): MotifKind {
  if (path.startsWith('/history')) return 'archive'
  if (path.startsWith('/workflows')) return 'weave'
  if (path.startsWith('/agents')) return 'constellation'
  if (path.startsWith('/settings')) return 'orbit'
  if (path.startsWith('/runs/')) return 'pulse'
  return 'ribbons'
}

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
  const queryClient = useQueryClient()
  const [role, setRole] = useState('reader')
  const navigationId = useId()
  const [showLogin, setShowLogin] = useState(false)
  const [authStatus, setAuthStatus] = useState<'checking' | 'guest' | 'verified' | 'error'>(
    'checking',
  )
  const [authError, setAuthError] = useState('')
  const [authAttempt, setAuthAttempt] = useState(0)
  const [navOpen, setNavOpen] = useState(false)
  const motion = useAmbientMotion()
  const headerRef = useRef<HTMLElement>(null)
  const navToggleRef = useRef<HTMLButtonElement>(null)
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
      if (event.key === 'dr_api_key' || event.key === null) {
        clearWorkspaceState()
        queryClient.clear()
        setAuthStatus('checking')
        setAuthAttempt((attempt) => attempt + 1)
      }
    }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [queryClient])

  useEffect(() => {
    setNavOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!navOpen) return
    const onPointer = (event: PointerEvent) => {
      if (!headerRef.current?.contains(event.target as Node)) setNavOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setNavOpen(false)
      navToggleRef.current?.focus()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [navOpen])

  useEffect(() => {
    const onUnauthorized = () => {
      clearApiKey()
      queryClient.clear()
      setAuthError('')
      setAuthStatus('guest')
      setShowLogin(true)
    }
    window.addEventListener('dr:unauthorized', onUnauthorized)
    return () => window.removeEventListener('dr:unauthorized', onUnauthorized)
  }, [queryClient])

  useEffect(() => {
    const controller = new AbortController()
    const key = getApiKey()

    setAuthStatus('checking')
    getConfig(controller.signal)
      .then((config) => {
        if (controller.signal.aborted) return
        setRole(config.access?.role ?? 'admin')
        setAuthError('')
        setAuthStatus('verified')
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        if (error instanceof DOMException && error.name === 'AbortError') return
        if (error instanceof ApiError && error.status === 401) {
          clearApiKey()
          queryClient.clear()
          setAuthError('')
          setAuthStatus('guest')
          setShowLogin(Boolean(key))
          return
        }
        setAuthError(error instanceof Error ? error.message : '无法连接服务端')
        setAuthStatus('error')
      })
    return () => controller.abort()
  }, [authAttempt, queryClient])

  const retryAuth = () => {
    setAuthError('')
    setAuthStatus('checking')
    setAuthAttempt((attempt) => attempt + 1)
  }

  const onAuthenticated = () => {
    queryClient.clear()
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
    <div
      className="app-container top-navigation-layout signal-theme"
      data-atmosphere-paused={motion.inactive}
    >
      <a className="skip-link" href="#workspace-main">
        跳到页面内容
      </a>
      <WorkspaceAtmosphere kind={motifForPath(location.pathname)} paused={motion.inactive} />
      <header className="global-header" ref={headerRef}>
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
          ref={navToggleRef}
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
          {navigation
            .filter(
              (item) =>
                role === 'admin' ||
                item.to === '/history' ||
                (item.to === '/' && role === 'researcher'),
            )
            .map((item) => (
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
          <button
            type="button"
            className="nav-link compact-nav-action"
            onClick={motion.toggle}
            disabled={motion.reduced}
            aria-pressed={motion.paused}
            aria-label={
              motion.reduced
                ? '背景动效已按系统设置暂停'
                : motion.paused
                  ? '播放背景动效'
                  : '暂停背景动效'
            }
            title={
              motion.reduced
                ? '已跟随系统减少动态效果'
                : motion.paused
                  ? '播放背景动效'
                  : '暂停背景动效'
            }
          >
            <AppIcon name={motion.paused ? 'play' : 'pause'} size={15} aria-hidden="true" />
            <span>
              {motion.reduced
                ? '已跟随系统减少动效'
                : motion.paused
                  ? '播放背景动效'
                  : '暂停背景动效'}
            </span>
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
            className="atmosphere-toggle"
            onClick={motion.toggle}
            disabled={motion.reduced}
            aria-pressed={motion.paused}
            aria-label={
              motion.reduced
                ? '背景动效已按系统设置暂停'
                : motion.paused
                  ? '播放背景动效'
                  : '暂停背景动效'
            }
            title={
              motion.reduced
                ? '已跟随系统减少动态效果'
                : motion.paused
                  ? '播放背景动效'
                  : '暂停背景动效'
            }
          >
            <AppIcon name={motion.paused ? 'play' : 'pause'} size={15} aria-hidden="true" />
          </button>

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

      <main className="main-content" id="workspace-main" tabIndex={-1}>
        <div className="content-area route-enter" key={location.pathname}>
          {(role !== 'admin' &&
            ['/settings', '/agents', '/workflows'].includes(location.pathname)) ||
          (role === 'reader' && location.pathname === '/') ? (
            <section className="panel panel-body stack" role="status">
              <h1>当前身份没有此操作权限</h1>
              <p>可以查看你有权访问的研究记录，或使用管理员分配的密钥切换身份。</p>
              <NavLink to="/history">查看研究历史</NavLink>
            </section>
          ) : (
            <Outlet context={{ role }} />
          )}
          <div className="workspace-trail" aria-hidden="true">
            <span className="workspace-trail-line" />
            <ResearchMotif kind={motifForPath(location.pathname)} />
            <span className="workspace-trail-line" />
          </div>
        </div>
      </main>

      {tour}
      {showLogin && (
        <LoginGate onClose={() => setShowLogin(false)} onAuthenticated={onAuthenticated} />
      )}
    </div>
  )
}
