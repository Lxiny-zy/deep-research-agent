import { isRouteErrorResponse, Link, useRouteError } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'

export function NotFoundPage() {
  return (
    <section className="panel route-state" role="status">
      <span className="route-state-code" aria-hidden="true">
        404
      </span>
      <h1>页面不存在</h1>
      <p>地址可能已经变更，可以回到研究工作台继续。</p>
      <div className="route-state-actions">
        <Link className="btn btn-primary" to="/">
          <AppIcon name="arrow-left" size={16} aria-hidden="true" />
          回到首页
        </Link>
        <Link className="btn btn-secondary" to="/history">
          查看研究历史
        </Link>
      </div>
    </section>
  )
}

export default function ErrorPage() {
  const error = useRouteError()
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFoundPage />
  return (
    <main className="route-state-page">
      <section className="panel route-state" role="alert">
        <AppIcon name="refresh" size={40} aria-hidden="true" />
        <h1>页面暂时无法加载</h1>
        <p>请刷新页面重试。已提交的研究会继续执行，可以在历史记录中查看。</p>
        <div className="route-state-actions">
          <button className="btn btn-primary" onClick={() => window.location.reload()}>
            重新加载
          </button>
          <a className="btn btn-secondary" href="/">
            回到首页
          </a>
        </div>
      </section>
    </main>
  )
}
