import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AppIcon } from './AppIcon'
import { readWorkspaceFile } from '../api/client'
import { humanSize } from '../lib/workbench'
import type { RunWorkspace, WorkspaceFile } from '../types'

const PREVIEWABLE = /^(text\/|application\/json)/

interface Group {
  key: string
  title: string
  files: WorkspaceFile[]
}

function groupFiles(files: WorkspaceFile[]): Group[] {
  const groups = new Map<string, Group>()
  for (const file of files) {
    const key = `${file.area}/${file.stage}`
    const title = `${file.area === 'output' ? '成品' : '中间产物'} · ${file.stage}`
    const group = groups.get(key) ?? { key, title, files: [] }
    group.files.push(file)
    groups.set(key, group)
  }
  return [...groups.values()]
}

function Preview({ runId, file }: { runId: string; file: WorkspaceFile }) {
  const content = useQuery({
    queryKey: ['workspace-file', runId, file.path, file.sha256],
    queryFn: ({ signal }) => readWorkspaceFile(runId, file.path, signal),
    staleTime: Infinity,
  })
  return (
    <div className="file-preview" aria-live="polite">
      <div className="file-preview-head">
        <strong>{file.name}</strong>
        <span className="hint">
          {humanSize(file.size)} · sha256 {file.sha256.slice(0, 10)}
        </span>
      </div>
      {content.isLoading && <p className="hint">正在读取…</p>}
      {content.isError && (
        <p className="error-text small" role="alert">
          读取失败：{content.error instanceof Error ? content.error.message : '未知错误'}
        </p>
      )}
      {content.data && (
        <>
          <pre className="file-preview-body">{content.data.text}</pre>
          {content.data.truncated && <p className="hint">文件较大，仅显示前 400 KB。</p>}
        </>
      )}
    </div>
  )
}

/**
 * 右栏：运行产物文件树。成品在前、中间产物默认折叠；文本类产物可内联预览
 * （服务端按清单哈希复核后返回，HTML 产物也只以纯文本展示，不在页面里执行）。
 */
export default function FileTree({
  runId,
  workspace,
}: {
  runId: string
  workspace: RunWorkspace | undefined
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const groups = useMemo(() => groupFiles(workspace?.files ?? []), [workspace?.files])
  const active = workspace?.files.find((file) => file.path === selected)

  return (
    <section className="panel file-tree" aria-label="运行产物">
      <div className="step-rail-head">
        <h2 className="panel-title">产物文件</h2>
        <span className="hint">{workspace?.files.length ?? 0} 个</span>
      </div>
      {groups.length === 0 ? (
        <p className="hint">本次运行还没有登记产物文件。</p>
      ) : (
        groups.map((group) => (
          <details key={group.key} className="file-group" open={group.key.startsWith('output')}>
            <summary>
              <AppIcon name="stack" size={13} aria-hidden="true" /> {group.title}
              <span className="hint"> ({group.files.length})</span>
            </summary>
            <ul>
              {group.files.map((file) => {
                const previewable = PREVIEWABLE.test(file.mime_type)
                return (
                  <li key={file.path}>
                    <button
                      type="button"
                      className={'file-entry' + (file.path === selected ? ' is-selected' : '')}
                      disabled={!previewable}
                      title={previewable ? '预览' : '二进制文件请在交付物面板下载'}
                      aria-pressed={file.path === selected}
                      onClick={() => setSelected(file.path === selected ? null : file.path)}
                    >
                      <AppIcon name="file" size={13} aria-hidden="true" />
                      <span className="file-name">{file.name}</span>
                      <span className="hint">{humanSize(file.size)}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </details>
        ))
      )}
      {active && <Preview runId={runId} file={active} />}
    </section>
  )
}
