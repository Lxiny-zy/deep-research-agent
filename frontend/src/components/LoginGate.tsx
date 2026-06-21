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
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="panel-title">🔑 需要 API 密钥</h3>
        <div className="stack">
          <p className="hint">
            本服务已启用密钥鉴权（服务端 API_KEY）。请输入密钥后保存——密钥仅存于本浏览器，不会上传。
          </p>
          <label className="field-label">
            API 密钥
            <input
              className="input"
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && save()}
              placeholder="粘贴服务端 API_KEY"
              autoFocus
            />
          </label>
          <div className="row between" style={{ marginTop: 8 }}>
            {existing ? (
              <button className="btn ghost danger" onClick={logout}>
                清除密钥
              </button>
            ) : (
              <button className="btn ghost" onClick={onClose}>
                取消
              </button>
            )}
            <button className="btn" onClick={save} disabled={!key.trim()}>
              保存并刷新
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
