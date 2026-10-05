/** The same iris mark used in the application header. */
export default function QaAvatar({ active = false }: { active?: boolean }) {
  return (
    <span className="qa-avatar" aria-hidden="true">
      <span className={`brand-ring${active ? ' is-active' : ''}`} />
    </span>
  )
}
