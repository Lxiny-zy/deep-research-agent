import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  label: string
  onRetry?: () => void
}

interface State { error: Error | null }

/** Isolates optional heavy panels so a PDF/canvas fault cannot blank the route. */
export default class PanelErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  retry = () => {
    this.setState({ error: null })
    this.props.onRetry?.()
  }

  render() {
    if (this.state.error) {
      return (
        <div className="reader-note" role="alert">
          <p>{this.props.label}暂时无法显示，请重试。</p>
          <button type="button" className="btn btn-secondary btn-sm" onClick={this.retry}>
            重新加载
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
