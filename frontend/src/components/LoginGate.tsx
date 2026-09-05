import { useEffect, useRef, useState } from 'react'
import { clearApiKey, getApiKey, isApiKeyRemembered, setApiKey } from '../api/client'
import { AppIcon } from './AppIcon'

interface Props {
  onClose: () => void
  onAuthenticated: () => void
}

export default function LoginGate({ onClose, onAuthenticated }: Props) {
  const existing = getApiKey() ?? ''
  const [key, setKey] = useState(existing)
  const [remember, setRemember] = useState(existing ? isApiKeyRemembered() : true)
  const [visible, setVisible] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [storageNotice, setStorageNotice] = useState('')
  const dialogRef = useRef<HTMLDivElement>(null)
  const requestRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialogRef.current?.querySelector<HTMLInputElement>('input')?.focus()
    return () => {
      requestRef.current?.abort()
      document.body.style.overflow = previousOverflow
      previousFocus?.focus()
    }
  }, [])

  async function save() {
    const value = key.trim()
    if (!value || pending) return
    setPending(true)
    setError('')
    const controller = new AbortController()
    requestRef.current = controller
    try {
      const response = await fetch('/api/config', {
        headers: { Authorization: `Bearer ${value}` },
        signal: controller.signal,
      })
      if (controller.signal.aborted) return
      if (!response.ok) {
        throw new Error(
          response.status === 401
            ? '访问密钥无效，请检查后重试。'
            : `验证失败（HTTP ${response.status}）`,
        )
      }
      const storage = setApiKey(value, remember)
      if ((remember && storage !== 'local') || storage === 'memory') {
        setStorageNotice(
          storage === 'memory'
            ? '浏览器阻止了存储，本次登录仅在当前页面有效。'
            : '浏览器阻止了长期存储，本次登录仅在当前标签页有效。',
        )
      } else {
        onAuthenticated()
      }
    } catch (cause) {
      if (controller.signal.aborted) return
      setError(cause instanceof Error ? cause.message : '无法连接服务端，请重试。')
    } finally {
      if (!controller.signal.aborted) setPending(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        ref={dialogRef}
        className="modal scale-in auth-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="api-key-title"
        aria-describedby="api-key-description"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose()
          if (event.key !== 'Tab') return
          const elements = [
            ...(dialogRef.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled), input:not(:disabled)',
            ) ?? []),
          ]
          const first = elements[0]
          const last = elements[elements.length - 1]
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault()
            last?.focus()
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault()
            first?.focus()
          }
        }}
      >
        <div className="auth-modal-inner">
          <div className="auth-topline">
            <span className="auth-symbol">
              <AppIcon name="key" size={24} aria-hidden="true" />
            </span>
            <span className="panel-kicker">WORKSPACE ACCESS</span>
            <button
              type="button"
              className="btn btn-ghost icon-button"
              onClick={onClose}
              aria-label="关闭"
              title="关闭"
            >
              <AppIcon name="x" size={18} aria-hidden="true" />
            </button>
          </div>
          <h2 id="api-key-title">连接你的研究工作台</h2>
          <p className="auth-copy" id="api-key-description">
            使用服务端的访问密钥（API_KEY）验证身份。这里无需填写模型服务的密钥。
          </p>
          <form
            className="auth-form"
            onSubmit={(event) => {
              event.preventDefault()
              void save()
            }}
          >
            <label className="field-label" htmlFor="api-key-input">
              访问密钥
            </label>
            <div className="auth-key-input">
              <input
                id="api-key-input"
                className="input"
                type={visible ? 'text' : 'password'}
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder="输入 API_KEY"
                autoComplete="current-password"
                disabled={pending || Boolean(storageNotice)}
              />
              <button
                type="button"
                className="btn btn-ghost icon-button"
                onClick={() => setVisible(!visible)}
                title={visible ? '隐藏密钥' : '显示密钥'}
                aria-label={visible ? '隐藏密钥' : '显示密钥'}
                aria-pressed={visible}
              >
                <AppIcon name={visible ? 'eye-off' : 'eye'} size={17} aria-hidden="true" />
              </button>
            </div>
            <label className="auth-remember">
              <input
                type="checkbox"
                checked={remember}
                onChange={(event) => setRemember(event.target.checked)}
                disabled={pending || Boolean(storageNotice)}
              />
              <span>
                记住此设备<small>下次自动登录；公共设备请取消勾选。</small>
              </span>
            </label>
            {error && (
              <p className="test-result test-fail" role="alert">
                <AppIcon name="circle-x" size={16} aria-hidden="true" />
                {error}
              </p>
            )}
            {storageNotice && (
              <p className="auth-storage-notice" role="status">
                {storageNotice}
              </p>
            )}
            {storageNotice ? (
              <button
                className="btn btn-primary auth-submit"
                type="button"
                onClick={onAuthenticated}
              >
                继续进入
                <AppIcon name="arrow-right" size={17} aria-hidden="true" />
              </button>
            ) : (
              <button
                className="btn btn-primary auth-submit"
                type="submit"
                disabled={!key.trim() || pending}
              >
                {pending ? '正在验证' : '验证并进入'}
                <AppIcon
                  name={pending ? 'loader' : 'arrow-right'}
                  size={17}
                  className={pending ? 'spin' : ''}
                  aria-hidden="true"
                />
              </button>
            )}
          </form>
          <div className="auth-footnote">
            <AppIcon name="shield" size={15} aria-hidden="true" />
            <p>密钥保存在此浏览器，并随请求发送至当前服务进行验证。</p>
          </div>
          {existing && (
            <button
              className="btn btn-ghost auth-logout"
              type="button"
              disabled={pending}
              onClick={() => {
                clearApiKey()
                onAuthenticated()
              }}
            >
              <AppIcon name="logout" size={15} aria-hidden="true" />
              清除密钥并退出
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
