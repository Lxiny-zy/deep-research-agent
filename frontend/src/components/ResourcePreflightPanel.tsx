import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { checkResourcePreflight } from '../api/client'
import type { ResourcePreflight } from '../types'

export default function ResourcePreflightPanel({ workflow }: { workflow: string }) {
  const [result, setResult] = useState<ResourcePreflight | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const requestRef = useRef<AbortController | null>(null)
  useEffect(() => {
    requestRef.current?.abort()
    setResult(null)
    setError('')
    setPending(false)
    const invalidate = () => {
      requestRef.current?.abort()
      setResult(null)
      setPending(false)
    }
    window.addEventListener('dr:config-changed', invalidate)
    window.addEventListener('focus', invalidate)
    return () => {
      requestRef.current?.abort()
      window.removeEventListener('dr:config-changed', invalidate)
      window.removeEventListener('focus', invalidate)
    }
  }, [workflow])
  async function check() {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    setPending(true)
    setError('')
    setResult(null)
    try {
      const next = await checkResourcePreflight(workflow || 'deep', controller.signal)
      if (!controller.signal.aborted) setResult(next)
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(cause instanceof Error ? cause.message : '无法检查配置')
    } finally {
      if (!controller.signal.aborted) setPending(false)
    }
  }
  return (
    <section className="stack search-preflight" aria-label="运行前配置检查">
      <button type="button" className="btn ghost" onClick={check} disabled={pending}>
        {pending ? '正在检查配置…' : '检查角色与检索配置'}
      </button>
      {error && <p role="alert">{error}</p>}
      {result && (
        <>
          <p role="status">{result.ok ? '配置检查通过' : '配置需要修正'}</p>
          <p className="hint">结果仅代表本次检查；开始研究时会再次校验配置。</p>
          {result.errors.map((message) => (
            <p key={message} className="error-text">
              {message}
            </p>
          ))}
          <ul>
            {result.roles.map((role) => (
              <li key={role.role}>
                <strong>{role.role}</strong> · {role.model_profile} / {role.model}
                {role.search_profiles.length > 0 && (
                  <p className="hint">
                    {role.inherits_search ? '继承默认检索：' : '专属检索：'}
                    {role.search_profiles
                      .map(
                        (p) =>
                          `${p.name}（${p.ready ? '已配置' : '不可用'}，${p.key_count} 个启用 Key）`,
                      )
                      .join('、')}
                  </p>
                )}
              </li>
            ))}
          </ul>
          {result.warnings.map((message) => (
            <p className="hint" key={message}>
              {message}
            </p>
          ))}
          <Link to="/agents?tab=keys">管理检索资源</Link>
        </>
      )}
    </section>
  )
}
