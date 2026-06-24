import { useEffect, useId, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import LoginGate from './components/LoginGate'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'nav-link active' : 'nav-link'

export default function App() {
  const sidebarTitleId = useId()
  const [showLogin, setShowLogin] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()

  useEffect(() => {
    const onUnauthorized = () => setShowLogin(true)
    window.addEventListener('dr:unauthorized', onUnauthorized)
    return () => window.removeEventListener('dr:unauthorized', onUnauthorized)
  }, [])

  // 移动端：路由切换时关闭侧边栏
  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!sidebarOpen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [sidebarOpen])

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
    <div className="app-container">
      {/* 侧边栏 */}
      <aside
        className={`sidebar ${sidebarOpen ? 'open' : ''}`}
        aria-labelledby={sidebarTitleId}
      >
        <div className="sidebar-brand">
          <div className="brand-logo">
            <div className="brand-icon">
              <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M16 2L28 9V23L16 30L4 23V9L16 2Z" stroke="currentColor" strokeWidth="2" fill="none"/>
                <path d="M16 2V30M4 9L28 23M28 9L4 23" stroke="currentColor" strokeWidth="1.5" opacity="0.5"/>
                <circle cx="16" cy="16" r="3" fill="currentColor"/>
              </svg>
            </div>
            <div className="brand-text">
              <h1 id={sidebarTitleId}>Deep Research</h1>
            </div>
          </div>
          <p className="brand-tagline">Multi-Agent Research System</p>
        </div>

        <nav className="sidebar-nav">
          <div className="nav-section">
            <div className="nav-section-title">核心功能</div>
            <NavLink to="/" end className={linkClass}>
              <span className="nav-icon">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 1L15 8L8 15L1 8L8 1Z" fill="currentColor"/>
                </svg>
              </span>
              <span>新建研究</span>
            </NavLink>
            <NavLink to="/history" className={linkClass}>
              <span className="nav-icon">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <rect x="2" y="3" width="12" height="2" fill="currentColor"/>
                  <rect x="2" y="7" width="12" height="2" fill="currentColor"/>
                  <rect x="2" y="11" width="12" height="2" fill="currentColor"/>
                </svg>
              </span>
              <span>研究历史</span>
            </NavLink>
          </div>

          <div className="nav-section">
            <div className="nav-section-title">高级配置</div>
            <NavLink to="/workflows" className={linkClass}>
              <span className="nav-icon">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" fill="none"/>
                  <circle cx="8" cy="4" r="1.5" fill="currentColor"/>
                  <circle cx="12" cy="8" r="1.5" fill="currentColor"/>
                  <circle cx="8" cy="12" r="1.5" fill="currentColor"/>
                  <circle cx="4" cy="8" r="1.5" fill="currentColor"/>
                </svg>
              </span>
              <span>工作流构建</span>
            </NavLink>
            <NavLink to="/agents" className={linkClass}>
              <span className="nav-icon">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <rect x="1" y="1" width="6" height="6" fill="currentColor"/>
                  <rect x="9" y="1" width="6" height="6" fill="currentColor"/>
                  <rect x="1" y="9" width="6" height="6" fill="currentColor"/>
                  <rect x="9" y="9" width="6" height="6" fill="currentColor"/>
                </svg>
              </span>
              <span>角色广场</span>
            </NavLink>
            <NavLink to="/settings" className={linkClass}>
              <span className="nav-icon">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 2L10 6H14L11 9L12 14L8 11L4 14L5 9L2 6H6L8 2Z" fill="currentColor"/>
                </svg>
              </span>
              <span>全局设置</span>
            </NavLink>
          </div>
        </nav>

        <div className="sidebar-footer">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setShowLogin(true)}
            style={{ width: '100%' }}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ marginRight: '6px' }}>
              <rect x="3" y="6" width="10" height="7" stroke="currentColor" strokeWidth="1.5" fill="none"/>
              <path d="M5 6V4C5 2.34315 6.34315 1 8 1C9.65685 1 11 2.34315 11 4V6" stroke="currentColor" strokeWidth="1.5"/>
              <circle cx="8" cy="10" r="1" fill="currentColor"/>
            </svg>
            API 密钥管理
          </button>
        </div>
      </aside>
      {sidebarOpen && (
        <button
          type="button"
          className="sidebar-overlay"
          aria-label="关闭导航"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* 主内容区 */}
      <div className="main-content">
        <header className="top-bar">
          <div className="row">
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              id="mobile-menu-btn"
              type="button"
              aria-label={sidebarOpen ? '关闭导航' : '打开导航'}
              aria-controls={sidebarTitleId}
              aria-expanded={sidebarOpen}
            >
              ☰
            </button>
            <h2 className="top-bar-title">{getPageTitle()}</h2>
          </div>
          <div className="top-bar-actions">
            <div className="badge info">
              <span>系统在线</span>
            </div>
          </div>
        </header>

        <div className="content-area">
          <Outlet />
        </div>
      </div>

      {showLogin && <LoginGate onClose={() => setShowLogin(false)} />}
    </div>
  )
}
