// 加载骨架：金色微光扫过的占位行（纯装饰，aria-hidden）。
export default function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="sk sk-row" />
      ))}
    </div>
  )
}
