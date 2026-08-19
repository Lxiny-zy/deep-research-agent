import type { CSSProperties } from 'react'
import { agentIconName } from '../lib/agentIcons'
import type { RoleInfo } from '../types'
import { AppIcon, type AppIconName } from './AppIcon'

/**
 * 内置角色陈列（角色广场）：按管线执行序（/api/roles 返回序）展示 5 个内置角色。
 * 只读——内置角色不可编辑删除；synthesizer 带「产出报告」徽章。
 */

const ROLE_ICONS: Record<string, AppIconName> = {
  planner: 'route',
  researcher: 'search-code',
  reflector: 'refresh',
  synthesizer: 'file',
  critic: 'shield',
}

function staggerStyle(index: number): CSSProperties {
  return { '--stagger-i': index } as CSSProperties
}

export default function BuiltinRoleGallery({ roles }: { roles: RoleInfo[] }) {
  const builtins = roles.filter((role) => role.builtin)
  if (!builtins.length) return null
  return (
    <section className="builtin-rail" aria-label="内置角色">
      <div className="builtin-rail-head">
        <div>
          <span className="panel-kicker">
            <AppIcon name="bot" size={12} aria-hidden="true" /> BUILTIN / PIPELINE ORDER
          </span>
          <h3 className="builtin-rail-title">内置角色</h3>
        </div>
        <span className="builtin-rail-hint">
          按管线执行序排列的 {builtins.length} 个基座角色：始终可用，可直接被工作流引用。
        </span>
      </div>
      <div className="builtin-card-grid builtin-role-grid">
        {builtins.map((role, index) => {
          const pos = String(index + 1).padStart(2, '0')
          return (
            <article
              key={role.name}
              className="builtin-card builtin-role-card"
              data-pos={pos}
              style={staggerStyle(index)}
            >
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
                  <code>{role.name}</code>
                </div>
              </div>
              {role.description && <p className="builtin-card-desc">{role.description}</p>}
            </article>
          )
        })}
      </div>
    </section>
  )
}
