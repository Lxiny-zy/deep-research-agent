import { useState } from 'react'
import { clearApiKey, getApiKey, setApiKey } from '../api/client'

interface Props {
  onClose: () => void
}

/** 密钥登录：后端启用 API_KEY 鉴权时，输入密钥后存本地（localStorage）并刷新生效。

 401 时由 App 自动弹出；导航栏「🔑 密钥」可主动打开以设置/更换。
 */
export default function LoginGate({ onClose }: Props) {
  const existing = getApiKey() ?? ''
  const [key, setKey] = useState(existing)

  function save() {
    const k = key.trim()
    if (!k) return
    setApiKey(k)
    window.location.reload() // 重载让所有请求带上新密钥重试
  }

  function logout() {
    clearApiKey()
    window.location.reload()
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal scale-in" onClick={(e) => e.stopPropagation()}>
        <div style={{ position: 'relative' }}>
          <div className="geo-corner top-left"></div>
          <div className="geo-corner top-right"></div>

          <div className="panel-header">
            <div className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <svg width="20" height="20" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="3" y="6" width="10" height="7" stroke="currentColor" strokeWidth="1.5" fill="none"/>
                <path d="M5 6V4C5 2.34315 6.34315 1 8 1C9.65685 1 11 2.34315 11 4V6" stroke="currentColor" strokeWidth="1.5"/>
                <circle cx="8" cy="10" r="1" fill="currentColor"/>
              </svg>
              API 密钥管理
            </div>
            <button
              className="btn btn-ghost btn-sm"
              onClick={onClose}
              style={{ padding: '8px 12px' }}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </button>
          </div>

          <div className="stack" style={{ marginTop: '20px' }}>
            <div className="badge warning" style={{ width: '100%' }}>
              本服务已启用密钥鉴权
            </div>

            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', lineHeight: 1.6 }}>
              请输入服务端配置的 <code style={{
                background: 'var(--surface-3)',
                padding: '2px 6px',
                borderRadius: '4px',
                color: 'var(--accent-primary)'
              }}>API_KEY</code>，密钥仅存于本浏览器，不会上传到服务器。
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
                onChange={(e) => setKey(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && save()}
                placeholder="粘贴服务端 API_KEY"
                autoFocus
                style={{ fontFamily: 'var(--font-mono)' }}
              />
            </div>

            <div style={{
              display: 'flex',
              gap: '12px',
              marginTop: '16px',
              justifyContent: 'space-between'
            }}>
              {existing ? (
                <button className="btn btn-secondary" onClick={logout} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M3 4H13M5 4V3C5 2.44772 5.44772 2 6 2H10C10.5523 2 11 2.44772 11 3V4M6 7V12M10 7V12M4 4L5 13C5 13.5523 5.44772 14 6 14H10C10.5523 14 11 13.5523 11 13L12 4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                  </svg>
                  清除密钥
                </button>
              ) : (
                <button className="btn btn-ghost" onClick={onClose}>
                  取消
                </button>
              )}
              <button className="btn btn-primary" onClick={save} disabled={!key.trim()} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M13 2L14 3V14H2V2H11L13 2ZM13 2L11 4M11 4V2M11 4H13M5 9H11M5 11H9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                </svg>
                保存并刷新
              </button>
            </div>
          </div>

          <div className="geo-corner bottom-left"></div>
          <div className="geo-corner bottom-right"></div>
        </div>
      </div>
    </div>
  )
}
