import { useEffect, useState, type CSSProperties } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import LoginGate from './components/LoginGate'
import WelcomePage from './components/WelcomePage'
import OnboardingTour from './components/OnboardingTour'
import SpectrumBackdrop from './components/SpectrumBackdrop'
import {
  PAGE_SPECTRA,
  rgbCss,
  spectrumKey,
  wavelengthToRgb,
  type SpectrumKey,
} from './lib/spectralLines'
import { usePointerSpotlight } from './hooks/usePointerSpotlight'
import { hasSeenIntro, hasSeenTour, markIntroSeen, markTourSeen } from './lib/onboarding'
import { AppIcon, type AppIconName } from './components/AppIcon'
import {
  ApiError,
  clearApiKey,
  clearWorkspaceState,
  getApiKey,
  getApiKeyStorage,
  getConfig,
  listRuns,
} from './api/client'
import { displayReportTitle } from './lib/reportTitle'
import { useTheme } from './lib/theme'

type Role = 'admin' | 'researcher' | 'reader'

interface NavItem {
  to: string
  label: string
  /** 这一页在研究流程中的一步（见 lib/spectralLines） */
  spectrum: SpectrumKey
  icon: AppIconName
  end?: boolean
  roles: Role[]
}

// 一级入口按科研人员的工作场景组织；工作流构建与角色广场属于高级定制，
// 归在「研究工具」分组里，普通研究者不会被它们打扰。
// 每一页是研究流程中的一步，由紫到红排开，侧栏从上到下读下来就是一段完整的光谱。
const WORKSPACE_NAV: NavItem[] = [
  { to: '/', label: '工作台', spectrum: 'home', icon: 'sparkles', end: true, roles: ['admin', 'researcher'] },
  { to: '/qa', label: '学术问答', spectrum: 'qa', icon: 'chat', roles: ['admin', 'researcher'] },
  { to: '/history', label: '任务记录', spectrum: 'history', icon: 'history', roles: ['admin', 'researcher', 'reader'] },
  { to: '/library', label: '资料库', spectrum: 'library', icon: 'library', roles: ['admin', 'researcher', 'reader'] },
]

const ADVANCED_NAV: NavItem[] = [
  { to: '/workflows', label: '研究策略', spectrum: 'workflows', icon: 'workflow', roles: ['admin'] },
  { to: '/agents', label: '角色广场', spectrum: 'agents', icon: 'users', roles: ['admin'] },
  { to: '/settings', label: '设置', spectrum: 'tools', icon: 'settings', roles: ['admin'] },
]

const PAGE_TITLE: [RegExp, string][] = [
  [/^\/$/, '科研工作台'],
  [/^\/runs\/[^/]+\/read/, '论文精读'],
  [/^\/runs\//, '任务详情'],
  [/^\/history/, '任务记录'],
  [/^\/qa/, '学术问答'],
  [/^\/library/, '资料库'],
  [/^\/workflows/, '研究策略'],
  [/^\/agents/, '角色广场'],
  [/^\/settings/, '设置'],
]

/** 本页所在一步的颜色，写成 CSS 变量供侧栏色标、顶栏与页面强调色使用。 */
function lineStyle(key: SpectrumKey): CSSProperties {
  return { '--line': rgbCss(wavelengthToRgb(PAGE_SPECTRA[key].nm)) } as CSSProperties
}

const ROLE_LABEL: Record<Role, string> = {
  admin: '管理员',
  researcher: '研究者',
  reader: '访客',
}

const COLLAPSE_KEY = 'sr_sidebar_collapsed'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

function RecentRuns({ enabled }: { enabled: boolean }) {
  const location = useLocation()
  const recent = useQuery({
    queryKey: ['runs', { limit: 6, sidebar: true }],
    queryFn: ({ signal }) => listRuns({ limit: 6 }, signal),
    enabled,
    staleTime: 15_000,
  })
  const items = Array.isArray(recent.data) ? recent.data : []
  return (
    <div className="sidebar-section">
      <span className="sidebar-section-title">最近任务</span>
      <div className="sidebar-recent">
        {items.length === 0 && (
          <span className="sidebar-recent-empty">
            {recent.isLoading ? '加载中…' : '还没有任务'}
          </span>
        )}
        {items.map((run) => (
          <NavLink
            key={run.id}
            to={`/runs/${run.id}`}
            title={run.query}
            className={location.pathname === `/runs/${run.id}` ? 'active' : ''}
          >
            {displayReportTitle(run.query) || '未命名任务'}
          </NavLink>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  const queryClient = useQueryClient()
  const [role, setRole] = useState<Role>('reader')
  const [showLogin, setShowLogin] = useState(false)
  const [authStatus, setAuthStatus] = useState<'checking' | 'guest' | 'verified' | 'error'>(
    'checking',
  )
  const [authError, setAuthError] = useState('')
  const [authAttempt, setAuthAttempt] = useState(0)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  // 欢迎光环每个会话一次；新手引导等欢迎光环结束后再出现
  const [introDone, setIntroDone] = useState(hasSeenIntro)
  const [showTour, setShowTour] = useState(() => hasSeenIntro() && !hasSeenTour())
  const theme = useTheme()
  usePointerSpotlight()
  const location = useLocation()
  const navigate = useNavigate()

  const closeTour = () => {
    markTourSeen()
    setShowTour(false)
  }
  const enterWorkspace = () => (authStatus === 'verified' ? navigate('/') : setShowLogin(true))
  const finishIntro = () => {
    markIntroSeen()
    setIntroDone(true)
    if (location.pathname === '/welcome') navigate('/')
    if (!hasSeenTour()) setShowTour(true)
  }
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
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
    } catch {
      // 存储不可用时只影响「记住折叠状态」这一便利功能
    }
  }, [collapsed])

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
    setMobileOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!mobileOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [mobileOpen])

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
        setRole((config.access?.role as Role | undefined) ?? 'admin')
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
          <AppIcon name="network" size={24} />
        </div>
        <div>
          <span className="boot-kicker">Science Research</span>
          <p>正在连接研究服务…</p>
        </div>
        <AppIcon name="loader" size={18} className="spin" aria-label="正在连接" />
      </main>
    )
  }

  if (authStatus === 'error') {
    return (
      <main className="boot-screen boot-screen-error" role="alert">
        <div className="boot-mark">
          <AppIcon name="circle-x" size={24} />
        </div>
        <div>
          <span className="boot-kicker">无法连接服务端</span>
          <p>{authError || '请确认后端已启动'}</p>
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
        <WelcomePage
          onEnter={() => {
            // 游客已经在这里「进入」过：登录成功后不再重播欢迎光环
            markIntroSeen()
            setIntroDone(true)
            enterWorkspace()
          }}
          onTour={() => setShowTour(true)}
          onToggleTheme={theme.toggle}
          dark={theme.dark}
          reveal={false}
          held={showLogin}
        />
        {tour}
        {showLogin && (
          <LoginGate onClose={() => setShowLogin(false)} onAuthenticated={onAuthenticated} />
        )}
      </>
    )
  }

  const titleEntry = PAGE_TITLE.find(([pattern]) => pattern.test(location.pathname))
  const pageTitle = titleEntry?.[1] ?? 'Science Research'
  const pageSpectrum = spectrumKey(location.pathname)
  const pageLine = PAGE_SPECTRA[pageSpectrum]
  const showIntro = !introDone || location.pathname === '/welcome'
  const roleCn = ROLE_LABEL[role]
  const identity = getApiKeyStorage() === 'none' ? '本地研究者' : roleCn
  const visible = (items: NavItem[]) => items.filter((item) => item.roles.includes(role))
  const forbidden =
    (role !== 'admin' && ['/settings', '/agents', '/workflows'].includes(location.pathname)) ||
    (role === 'reader' && (location.pathname === '/' || location.pathname.startsWith('/qa')))
  const wide =
    location.pathname === '/workflows' ||
    location.pathname.startsWith('/runs/') ||
    location.pathname.startsWith('/qa')
  const shellClass = [
    'app-shell',
    collapsed ? 'is-collapsed' : '',
    mobileOpen ? 'is-mobile-open' : '',
  ]
    .filter(Boolean)
    .join(' ')

  const link = (item: NavItem) => (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.end}
      className={({ isActive }) => `sidebar-link${isActive ? ' active' : ''}`}
      title={collapsed ? item.label : PAGE_SPECTRA[item.spectrum].note}
      style={lineStyle(item.spectrum)}
    >
      <AppIcon name={item.icon} size={19} strokeWidth={1.6} aria-hidden="true" />
      <span className="sidebar-link-label">
        {item.label}
        <small>{PAGE_SPECTRA[item.spectrum].stage}</small>
      </span>
    </NavLink>
  )

  return (
    <div className={shellClass}>
      <a className="skip-link" href="#workspace-main">
        跳到页面内容
      </a>
      <aside className="sidebar" aria-label="侧边导航">
        <NavLink to="/" className="sidebar-brand" aria-label="Science Research 首页">
          <span className="sidebar-logo" aria-hidden="true" />
          <span className="sidebar-brand-text">
            <strong>Science Research</strong>
            <small>科研工作台</small>
          </span>
        </NavLink>
        <nav className="sidebar-nav" aria-label="主导航">
          <div className="sidebar-section">
            <span className="visually-hidden">工作区</span>
            {visible(WORKSPACE_NAV).map(link)}
          </div>
          {visible(ADVANCED_NAV).length > 0 && (
            <div className="sidebar-section">
              <span className="sidebar-section-title">研究工具</span>
              {visible(ADVANCED_NAV).map(link)}
            </div>
          )}
          <RecentRuns enabled={authStatus === 'verified'} />
        </nav>
        <div className="sidebar-footer">
          <p className="sidebar-motto" aria-hidden="true">
            亮线是已证之事，
            <br />
            暗线是未解之问。
          </p>
          <button
            type="button"
            className="sidebar-link"
            onClick={() => setShowTour(true)}
            title="使用引导"
          >
            <AppIcon name="help" size={18} aria-hidden="true" />
            <span className="sidebar-link-label">使用引导</span>
          </button>
          <button
            type="button"
            className="sidebar-link sidebar-collapse-toggle"
            onClick={() => setCollapsed((value) => !value)}
            aria-pressed={collapsed}
            title={collapsed ? '展开侧边栏' : '收起侧边栏'}
          >
            <AppIcon name={collapsed ? 'panel-open' : 'panel-close'} size={18} aria-hidden="true" />
            <span className="sidebar-link-label">收起侧边栏</span>
          </button>
        </div>
      </aside>
      <div className="sidebar-scrim" onClick={() => setMobileOpen(false)} aria-hidden="true" />
      {/* 环境背景：横贯页面的光谱带与本页认领的谱线，固定在内容区后方 */}
      <div className="backdrop" data-signature={pageSpectrum} aria-hidden="true">
        <SpectrumBackdrop route={location.pathname} dark={theme.dark} />
      </div>

      <div className="main-column">
        <header className="topbar">
          <button
            type="button"
            className="btn btn-ghost icon-button mobile-menu-button"
            aria-label={mobileOpen ? '关闭导航' : '打开导航'}
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen((open) => !open)}
          >
            <AppIcon name={mobileOpen ? 'x' : 'menu'} size={18} aria-hidden="true" />
          </button>
          <span className="topbar-title" style={lineStyle(pageSpectrum)}>
            {pageTitle}
            <small title={pageLine.note}>
              <i className="topbar-line" aria-hidden="true" />
              {pageLine.stage}
            </small>
          </span>
          <div className="topbar-actions">
            <button
              type="button"
              className="btn btn-ghost icon-button"
              onClick={() => navigate('/history', { state: { focusSearch: true } })}
              aria-label="搜索任务记录"
              title="搜索任务记录"
            >
              <AppIcon name="search" size={17} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="btn btn-ghost icon-button theme-toggle"
              onClick={(event) => {
                const rect = event.currentTarget.getBoundingClientRect()
                theme.toggle({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
              }}
              aria-label={theme.dark ? '切换到浅色主题' : '切换到深色主题'}
              title={theme.dark ? '浅色主题' : '深色主题'}
            >
              <AppIcon name={theme.dark ? 'sun' : 'moon'} size={17} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="identity-chip"
              onClick={() => setShowLogin(true)}
              title={`API 密钥：${keyStatus}`}
              aria-label={`当前身份：${identity}，管理 API 密钥`}
            >
              <span className="identity-avatar" aria-hidden="true">
                <AppIcon name="user" size={16} />
              </span>
              <span className="identity-text">
                <strong>{identity}</strong>
                <small>
                  {roleCn} · {keyStatus}
                </small>
              </span>
            </button>
          </div>
        </header>

        <main
          className="main-content"
          id="workspace-main"
          tabIndex={-1}
          style={lineStyle(pageSpectrum)}
        >
          <div className={`content-area${wide ? ' is-wide' : ''}`} key={location.pathname}>
            {forbidden ? (
              <section className="panel permission-state" role="status">
                <h1>当前身份没有此操作权限</h1>
                <p className="hint">
                  可以查看你有权访问的任务记录，或使用管理员分配的密钥切换身份。
                </p>
                <NavLink className="btn btn-secondary" to="/history">
                  查看任务记录
                </NavLink>
              </section>
            ) : (
              <Outlet context={{ role }} />
            )}
          </div>
        </main>
      </div>

      {showIntro ? (
        <WelcomePage onEnter={finishIntro} onToggleTheme={theme.toggle} dark={theme.dark} />
      ) : (
        tour
      )}
      {showLogin && (
        <LoginGate onClose={() => setShowLogin(false)} onAuthenticated={onAuthenticated} />
      )}
    </div>
  )
}
