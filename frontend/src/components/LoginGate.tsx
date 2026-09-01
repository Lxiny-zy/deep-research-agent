import { useState } from 'react'
import { clearApiKey, getApiKey, setApiKey } from '../api/client'
import { AppIcon } from './AppIcon'

interface Props {
  onClose: () => void
}

/** 密钥登录：后端启用 API_KEY 鉴权时，输入密钥后存本地并刷新生效。 */
export default function LoginGate({ onClose }: Props) {
  const existing = getApiKey() ?? ''
  const [key, setKey] = useState(existing)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function save() {
    const value = key.trim()
    if (!value || pending) return
    setPending(true)
    setError('')
    try {
      const response = await fetch('/api/config', {
        headers: { Authorization: `Bearer ${value}` },
      })
      if (!response.ok) {
        throw new Error(
          response.status === 401 ? '管理员凭证无效' : `验证失败（${response.status}）`,
        )
      }
      setApiKey(value)
      window.location.reload()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法验证管理员凭证')
      setPending(false)
    }
  }

  function logout() {
    clearApiKey()
    window.location.reload()
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal scale-in auth-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="api-key-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="auth-modal-inner">
          <div className="modal-header-row">
            <div className="panel-title" id="api-key-title">
              <AppIcon name="lock" size={19} aria-hidden="true" />
              API 密钥管理
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm icon-button"
              onClick={onClose}
              aria-label="关闭"
            >
              <AppIcon name="x" size={15} aria-hidden="true" />
            </button>
          </div>

          <div className="stack auth-modal-body">
            <div className="badge warning auth-notice">
              <AppIcon name="shield" size={14} aria-hidden="true" />
              本服务已启用密钥鉴权
            </div>

            <p className="auth-copy">
              请输入服务端配置的 <code>API_KEY</code>。密钥仅存于本浏览器，不会上传到服务器。
            </p>

            <div>
              <label className="field-label" htmlFor="api-key-input">
                API 密钥
              </label>
              <input
                id="api-key-input"
                className="input"
                type="password"
                value={key}
                onChange={(event) => setKey(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && void save()}
                placeholder="粘贴服务端 API_KEY"
                autoFocus
                autoComplete="current-password"
                style={{ fontFamily: 'var(--font-mono)' }}
              />
            </div>

            {error && (
              <p className="test-result test-fail">
                <AppIcon name="circle-x" size={14} aria-hidden="true" />
                {error}
              </p>
            )}

            <div className="auth-actions">
              {existing ? (
                <button className="btn btn-secondary" onClick={logout} type="button">
                  <AppIcon name="logout" size={14} aria-hidden="true" />
                  清除密钥
                </button>
              ) : (
                <button className="btn btn-ghost" onClick={onClose} type="button">
                  取消
                </button>
              )}
              <button
                className="btn btn-primary"
                onClick={() => void save()}
                disabled={!key.trim() || pending}
                type="button"
              >
                <AppIcon
                  name={pending ? 'loader' : 'arrow-right'}
                  size={14}
                  aria-hidden="true"
                  className={pending ? 'spin' : ''}
                />
                {pending ? '验证中…' : '验证并进入'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
