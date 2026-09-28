import { isRouteErrorResponse, Link, useRouteError } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'

export function NotFoundPage() {
  return (
    <section className="route-state" role="status">
      <span className="route-state-code" aria-hidden="true">
        404
      </span>
      <h1>页面不存在</h1>
      <p>地址可能已经变更，可以回到工作台继续。</p>
      <div className="route-state-actions">
        <Link className="btn btn-primary" to="/">
          <AppIcon name="arrow-left" size={16} aria-hidden="true" />
          回到工作台
        </Link>
        <Link className="btn" to="/history">
          查看任务记录
        </Link>
      </div>
    </section>
  )
}

export default function ErrorPage() {
  const error = useRouteError()
  if (isRouteErrorResponse(error) && error.status === 404) {
    return (
      <main className="route-state-page">
        <NotFoundPage />
      </main>
    )
  }
  return (
    <main className="route-state-page">
      <section className="route-state" role="alert">
        <span className="route-state-icon" aria-hidden="true">
          <AppIcon name="alert" size={26} />
        </span>
        <h1>页面暂时无法加载</h1>
        <p>请刷新页面重试。已提交的任务会继续执行，可以在任务记录中查看。</p>
        <div className="route-state-actions">
          <button className="btn btn-primary" onClick={() => window.location.reload()}>
            <AppIcon name="refresh" size={15} aria-hidden="true" />
            重新加载
          </button>
          <a className="btn" href="/">
            回到工作台
          </a>
        </div>
      </section>
    </main>
  )
}
