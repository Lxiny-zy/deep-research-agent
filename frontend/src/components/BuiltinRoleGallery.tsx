import { agentIconName } from '../lib/agentIcons'
import { BUILTIN_BEHAVIORS } from '../lib/behaviors'
import type { RoleInfo } from '../types'
import { AppIcon, type AppIconName } from './AppIcon'

/**
 * 内置角色陈列：展示通用角色与科研模板执行器，可创建同名配置卡。
 */

const ROLE_ICONS: Record<string, AppIconName> = {
  planner: 'route',
  researcher: 'search-code',
  reflector: 'refresh',
  synthesizer: 'file',
  critic: 'shield',
}

export default function BuiltinRoleGallery({ roles, onConfigure }: {
  roles: RoleInfo[]
  onConfigure?: (role: RoleInfo) => void
}) {
  const builtins = roles.filter((role) => role.builtin)
  if (!builtins.length) return null
  return (
    <section className="builtin-rail" aria-label="内置角色">
      <div className="catalog-section-head">
        <h3 className="builtin-rail-title">内置角色</h3>
        <span className="hint">
          按执行顺序排列的 {builtins.length} 个基座角色，始终可用，可直接被工作流引用。
        </span>
      </div>
      <div className="builtin-card-grid builtin-role-grid">
        {builtins.map((role, index) => {
          const pos = String(index + 1).padStart(2, '0')
          return (
            <article key={role.name} className="builtin-card builtin-role-card" data-pos={pos}>
              <div className="builtin-card-top">
                <span className="builtin-card-index">{pos}</span>
                <span className="builtin-card-badges">
                  {role.produces_report && <span className="builtin-badge report">产出报告</span>}
                </span>
              </div>
              <div className="builtin-card-title">
                <span className="builtin-card-glyph" aria-hidden="true">
                  <AppIcon name={ROLE_ICONS[role.name] ?? agentIconName(role.icon)} size={19} />
                </span>
                <div className="builtin-card-name">
                  <strong>{role.label}</strong>
                  <code aria-hidden="true">{role.name}</code>
                </div>
              </div>
              {role.description && <p className="builtin-card-desc">{role.description}</p>}
              {onConfigure && BUILTIN_BEHAVIORS[role.name] && (
                <button type="button" className="btn btn-ghost btn-sm"
                  onClick={() => onConfigure(role)} aria-label={`配置${role.label}`}>
                  配置提示词与模型
                </button>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
