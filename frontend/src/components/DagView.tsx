import type { DagData } from '../types'

// 把后端的拓扑分层（layers + deps）渲染成「逐层、带前驱标注」的视图。
export default function DagView({ dag }: { dag: DagData }) {
  return (
    <div className="dag">
      {dag.layers.map((layer, li) => (
        <div className="dag-layer" key={li}>
          <span className="dag-layer-label">第 {li + 1} 层</span>
          <div className="dag-nodes">
            {layer.map((n) => {
              const deps = dag.deps[String(n)] ?? []
              return (
                <span className="dag-node" key={n}>
                  #{n}
                  {deps.length > 0 && (
                    <span className="dag-dep">← {deps.map((d) => `#${d}`).join(', ')}</span>
                  )}
                </span>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
