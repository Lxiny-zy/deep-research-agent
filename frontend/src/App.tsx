import { NavLink, Outlet } from 'react-router-dom'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'nav-link active' : 'nav-link'

export default function App() {
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
        </nav>
      </header>
      <main>
        <Outlet />
      </main>
      <footer className="footer">
        多 Agent 深度研究 · 拆解 → 并行检索 → 反思补洞 → 带引用的研究报告
      </footer>
    </div>
  )
}
