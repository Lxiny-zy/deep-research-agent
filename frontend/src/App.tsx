import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import LoginGate from './components/LoginGate'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'nav-link active' : 'nav-link'

export default function App() {
  const [showLogin, setShowLogin] = useState(false)

  // 任意 /api 请求返回 401 时（client.ts 派发），弹出密钥登录
  useEffect(() => {
    const onUnauthorized = () => setShowLogin(true)
    window.addEventListener('dr:unauthorized', onUnauthorized)
    return () => window.removeEventListener('dr:unauthorized', onUnauthorized)
  }, [])

  return (
    <div className="container">
      <header className="site-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            ✦
          </span>
          <div>
            <h1>
              Deep <span className="accent">Research</span> Agent
            </h1>
            <p className="tagline">
              Planner → Researcher（并行）→ Reflector（反思）→ Synthesizer（引用综合）
            </p>
          </div>
        </div>
        <nav className="nav">
          <NavLink to="/" end className={linkClass}>
            新建研究
          </NavLink>
          <NavLink to="/history" className={linkClass}>
            历史
          </NavLink>
          <NavLink to="/workflows" className={linkClass}>
            工作流
          </NavLink>
          <NavLink to="/agents" className={linkClass}>
            角色广场
          </NavLink>
          <NavLink to="/settings" className={linkClass}>
            设置
          </NavLink>
          <button
            className="btn ghost sm"
            onClick={() => setShowLogin(true)}
            title="设置 / 更换 API 密钥"
          >
            🔑 密钥
          </button>
        </nav>
      </header>
      <main>
        <Outlet />
      </main>
      <footer className="footer">
        多 Agent 深度研究 · 拆解 → 并行检索 → 反思补洞 → 带引用的研究报告
      </footer>
      {showLogin && <LoginGate onClose={() => setShowLogin(false)} />}
    </div>
  )
}
