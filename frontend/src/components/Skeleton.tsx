// 加载骨架：柔和的占位行（纯装饰，对辅助技术隐藏）。
export default function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton-block" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="sk sk-row" />
      ))}
    </div>
  )
}
