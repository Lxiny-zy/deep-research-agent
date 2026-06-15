import { useState } from 'react'
import { useSetTags } from '../hooks/useRuns'

// 运行详情页的标签编辑：现有标签可删，输入回车新增；每次提交完整列表（替换语义）。
export default function TagEditor({ runId, tags }: { runId: string; tags: string[] }) {
  const [input, setInput] = useState('')
  const setTags = useSetTags(runId)

  function add() {
    const t = input.trim().slice(0, 64)
    setInput('')
    if (!t || tags.includes(t)) return
    setTags.mutate([...tags, t])
  }

  function remove(t: string) {
    setTags.mutate(tags.filter((x) => x !== t))
  }

  return (
    <div className="tag-editor">
      {tags.map((t) => (
        <span className="tag-chip removable" key={t}>
          {t}
          <button
            type="button"
            className="tag-x"
            aria-label={`移除标签 ${t}`}
            onClick={() => remove(t)}
            disabled={setTags.isPending}
          >
            ✕
          </button>
        </span>
      ))}
      <input
        className="tag-input"
        value={input}
        placeholder="+ 标签"
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            add()
          }
        }}
        onBlur={add}
        disabled={setTags.isPending}
      />
    </div>
  )
}
