import { useEffect, useMemo, useState } from 'react'
import { AppIcon, type AppIconName } from '../components/AppIcon'
import EmptyState from '../components/EmptyState'
import Skeleton from '../components/Skeleton'
import { useConfig } from '../hooks/useConfig'
import {
  useCorpora,
  useCreateCorpus,
  useCreateProject,
  useDeleteLibrarySource,
  useImportSource,
  useLibrarySources,
  useProjects,
  useSetSourceStatus,
  useSourceChunks,
} from '../hooks/useLibrary'
import type { ImportSourceInput, LibrarySourceKind } from '../types'

const SOURCE_MODES: Array<{ value: LibrarySourceKind; label: string; icon: AppIconName }> = [
  { value: 'url', label: '网页', icon: 'external' },
  { value: 'doi', label: 'DOI', icon: 'book' },
  { value: 'text', label: '文本', icon: 'file' },
  { value: 'markdown', label: 'Markdown', icon: 'braces' },
  { value: 'pdf', label: 'PDF', icon: 'file-search' },
]

function formatSize(chars: number) {
  return chars >= 10_000 ? `${(chars / 10_000).toFixed(1)} 万字` : `${chars} 字符`
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  const step = 0x8000
  for (let index = 0; index < bytes.length; index += step) {
    binary += String.fromCharCode(...bytes.subarray(index, index + step))
  }
  return btoa(binary)
}

export default function LibraryPage() {
  const config = useConfig()
  const canEdit = config.data?.access?.role !== 'reader'
  const projects = useProjects()
  const createProject = useCreateProject()
  const [projectId, setProjectId] = useState('')
  const [corpusId, setCorpusId] = useState('')
  const [selectedSourceId, setSelectedSourceId] = useState('')
  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [showProjectForm, setShowProjectForm] = useState(false)
  const [corpusName, setCorpusName] = useState('')
  const [mode, setMode] = useState<LibrarySourceKind>('url')
  const [sourceTitle, setSourceTitle] = useState('')
  const [origin, setOrigin] = useState('')
  const [sourceText, setSourceText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!projectId && projects.data?.length) setProjectId(projects.data[0].id)
    if (projectId && projects.data && !projects.data.some((item) => item.id === projectId)) {
      setProjectId(projects.data[0]?.id ?? '')
    }
  }, [projectId, projects.data])

  const corpora = useCorpora(projectId || undefined)
  useEffect(() => {
    if (!corpusId && corpora.data?.length) setCorpusId(corpora.data[0].id)
    if (corpusId && corpora.data && !corpora.data.some((item) => item.id === corpusId)) {
      setCorpusId(corpora.data[0]?.id ?? '')
    }
  }, [corpusId, corpora.data])

  const sources = useLibrarySources(projectId || undefined, corpusId || undefined)
  const chunks = useSourceChunks(projectId || undefined, selectedSourceId || undefined)
  const createCorpus = useCreateCorpus(projectId || undefined)
  const importSource = useImportSource(projectId || undefined)
  const setStatus = useSetSourceStatus(projectId || undefined)
  const deleteSource = useDeleteLibrarySource(projectId || undefined)
  const activeProject = projects.data?.find((item) => item.id === projectId)
  const selectedSource = sources.data?.find((item) => item.id === selectedSourceId)
  const busy = createProject.isPending || createCorpus.isPending || importSource.isPending
  const importNeedsFile = mode === 'pdf'
  const importNeedsUrl = mode === 'url' || mode === 'doi'
  const importNeedsText = mode === 'text' || mode === 'markdown'
  const projectStats = useMemo(
    () => ({
      sources: activeProject?.source_count ?? 0,
      included: activeProject?.included_source_count ?? 0,
      corpora: activeProject?.corpus_count ?? 0,
    }),
    [activeProject],
  )

  async function addProject() {
    if (!projectName.trim()) return
    setError('')
    try {
      const created = await createProject.mutateAsync({
        name: projectName.trim(),
        description: projectDescription.trim(),
      })
      setProjectId(created.id)
      setCorpusId('')
      setProjectName('')
      setProjectDescription('')
      setShowProjectForm(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '项目创建失败')
    }
  }

  async function addCorpus() {
    if (!corpusName.trim() || !projectId) return
    setError('')
    try {
      const created = await createCorpus.mutateAsync({ name: corpusName.trim() })
      setCorpusId(created.id)
      setCorpusName('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '资料库创建失败')
    }
  }

  async function importCurrentSource() {
    if (!projectId || !corpusId) return
    setError('')
    try {
      const body: ImportSourceInput = {
        corpus_id: corpusId,
        kind: mode,
        title: sourceTitle.trim(),
      }
      if (importNeedsUrl) body.origin_url = origin.trim()
      if (importNeedsText) body.text = sourceText
      if (importNeedsFile && file) {
        body.data_base64 = bytesToBase64(new Uint8Array(await file.arrayBuffer()))
        body.mime_type = file.type || 'application/pdf'
        body.title ||= file.name.replace(/\.pdf$/i, '')
      }
      const created = await importSource.mutateAsync(body)
      setSelectedSourceId(created.id)
      setSourceTitle('')
      setOrigin('')
      setSourceText('')
      setFile(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '来源导入失败')
    }
  }

  const projectForm = (
    <form
      className="library-project-form"
      onSubmit={(event) => {
        event.preventDefault()
        void addProject()
      }}
    >
      <label className="field-label" htmlFor="project-name">
        项目名称
        <input
          id="project-name"
          className="input"
          value={projectName}
          onChange={(event) => setProjectName(event.target.value)}
          placeholder="例如：光谱成像路线调研"
          autoFocus={showProjectForm}
        />
      </label>
      <label className="field-label" htmlFor="project-description">
        项目说明
        <textarea
          id="project-description"
          className="input textarea"
          rows={3}
          value={projectDescription}
          onChange={(event) => setProjectDescription(event.target.value)}
          placeholder="研究范围或交付目标"
        />
      </label>
      <div className="library-form-actions">
        {activeProject && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setShowProjectForm(false)}
          >
            取消
          </button>
        )}
        <button
          type="submit"
          className="btn btn-primary btn-sm"
          disabled={busy || !projectName.trim()}
        >
          <AppIcon
            name={busy ? 'loader' : 'plus'}
            size={14}
            className={busy ? 'spin' : ''}
            aria-hidden="true"
          />
          {busy ? '正在创建' : '创建项目'}
        </button>
      </div>
    </form>
  )

  return (
    <div className="stack page-stack library-page">
      <header className="page-header">
        <div>
          <h1>资料库</h1>
          <p>
            按项目管理研究来源：导入、审核、查看原文片段，已纳入的来源会参与后续任务的证据核验。
          </p>
        </div>
      </header>

      {error && (
        <div className="alert error" role="alert">
          <AppIcon name="circle-x" size={15} aria-hidden="true" /> {error}
        </div>
      )}

      {projects.isLoading ? (
        <section className="panel" role="status" aria-label="正在读取研究项目">
          <Skeleton rows={5} />
        </section>
      ) : !activeProject ? (
        <section className="panel library-onboarding">
          <div className="library-onboarding-copy">
            <span className="library-onboarding-icon" aria-hidden="true">
              <AppIcon name="library" size={24} strokeWidth={1.6} />
            </span>
            <h2>建立第一个研究项目</h2>
            <p className="hint">
              项目用于归集同一研究主题的来源、原文片段和审核状态。创建后即可导入资料。
            </p>
            <ol className="library-steps">
              <li>
                <strong>创建项目</strong>
                <span>写下研究主题与交付目标。</span>
              </li>
              <li>
                <strong>导入来源</strong>
                <span>支持网页、DOI、文本、Markdown 与 PDF，导入后自动分块。</span>
              </li>
              <li>
                <strong>审核与复用</strong>
                <span>纳入的来源会在新建任务时作为证据参与核验。</span>
              </li>
            </ol>
          </div>
          {canEdit ? (
            <div className="library-onboarding-form">
              <h3>新建项目</h3>
              {projectForm}
            </div>
          ) : (
            <div className="library-onboarding-form library-readonly">
              <AppIcon name="lock" size={20} aria-hidden="true" />
              <h3>当前为只读权限</h3>
              <p className="hint">请联系管理员创建或分配研究项目。</p>
            </div>
          )}
        </section>
      ) : (
        <div className="library-layout">
          <aside className="panel library-projects" aria-label="研究项目">
            <div className="library-projects-head">
              <h2 className="panel-title">
                研究项目 <small>{projects.data?.length ?? 0}</small>
              </h2>
              {canEdit && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm icon-button"
                  title="新建项目"
                  aria-label="新建项目"
                  aria-expanded={showProjectForm}
                  onClick={() => setShowProjectForm((value) => !value)}
                >
                  <AppIcon name={showProjectForm ? 'x' : 'plus'} size={15} aria-hidden="true" />
                </button>
              )}
            </div>
            {canEdit && showProjectForm && (
              <div className="library-projects-form">{projectForm}</div>
            )}
            <div className="library-project-list">
              {projects.data?.map((project) => (
                <button
                  type="button"
                  key={project.id}
                  className={`library-project${project.id === projectId ? ' is-active' : ''}`}
                  aria-current={project.id === projectId ? 'true' : undefined}
                  onClick={() => {
                    setProjectId(project.id)
                    setCorpusId('')
                    setSelectedSourceId('')
                  }}
                >
                  <AppIcon name="library" size={16} aria-hidden="true" />
                  <span className="library-project-text">
                    <strong>{project.name}</strong>
                    <small>{project.source_count} 个来源</small>
                  </span>
                </button>
              ))}
            </div>
          </aside>

          <div className="library-main">
            <section className="panel library-summary">
              <div className="library-summary-head">
                <div>
                  <h2>{activeProject.name}</h2>
                  <p className="hint">{activeProject.description || '尚未填写项目说明'}</p>
                </div>
              </div>
              <dl className="library-stats">
                <div>
                  <dt>资料库</dt>
                  <dd>{projectStats.corpora}</dd>
                </div>
                <div>
                  <dt>来源</dt>
                  <dd>{projectStats.sources}</dd>
                </div>
                <div>
                  <dt>参与研究</dt>
                  <dd>{projectStats.included}</dd>
                </div>
              </dl>
              <div className="library-corpus-bar" aria-label="资料库选择">
                <label className="field-label library-corpus-select" htmlFor="corpus-select">
                  当前资料库
                  <span className="select-with-icon">
                    <AppIcon name="database" size={15} aria-hidden="true" />
                    <select
                      id="corpus-select"
                      className="input"
                      value={corpusId}
                      onChange={(event) => {
                        setCorpusId(event.target.value)
                        setSelectedSourceId('')
                      }}
                    >
                      {corpora.data?.map((corpus) => (
                        <option key={corpus.id} value={corpus.id}>
                          {corpus.name} ({corpus.source_count})
                        </option>
                      ))}
                    </select>
                  </span>
                </label>
                {canEdit && (
                  <div className="library-add-corpus">
                    <input
                      className="input"
                      value={corpusName}
                      onChange={(event) => setCorpusName(event.target.value)}
                      placeholder="新资料库名称"
                      aria-label="新资料库名称"
                    />
                    <button
                      type="button"
                      className="btn"
                      title="创建资料库"
                      aria-label="创建资料库"
                      disabled={!corpusName.trim() || createCorpus.isPending}
                      onClick={() => void addCorpus()}
                    >
                      <AppIcon name="plus" size={15} aria-hidden="true" />
                      新建
                    </button>
                  </div>
                )}
              </div>
            </section>

            {canEdit && corpusId && (
              <section className="panel library-importer" aria-labelledby="library-import-title">
                <div className="panel-header">
                  <div>
                    <h2 className="panel-title" id="library-import-title">
                      导入来源
                    </h2>
                    <span className="hint">导入后自动提取正文、分块并生成定位信息。</span>
                  </div>
                </div>
                <div className="segmented-control library-modes" aria-label="来源类型">
                  {SOURCE_MODES.map((item) => (
                    <button
                      type="button"
                      key={item.value}
                      className={mode === item.value ? 'active' : ''}
                      aria-pressed={mode === item.value}
                      onClick={() => {
                        setMode(item.value)
                        setFile(null)
                      }}
                    >
                      <AppIcon name={item.icon} size={14} aria-hidden="true" />
                      {item.label}
                    </button>
                  ))}
                </div>
                <div className="library-import-grid">
                  <label className="field-label">
                    标题
                    <input
                      className="input"
                      value={sourceTitle}
                      onChange={(event) => setSourceTitle(event.target.value)}
                      placeholder="可留空，系统会尝试识别"
                    />
                  </label>
                  {importNeedsUrl && (
                    <label className="field-label">
                      {mode === 'doi' ? 'DOI' : 'HTTPS 地址'}
                      <input
                        className="input"
                        value={origin}
                        onChange={(event) => setOrigin(event.target.value)}
                        placeholder={
                          mode === 'doi' ? '10.xxxx/xxxxx' : 'https://example.org/report'
                        }
                      />
                    </label>
                  )}
                  {importNeedsText && (
                    <label className="field-label library-import-wide">
                      正文
                      <textarea
                        className="input textarea"
                        rows={6}
                        value={sourceText}
                        onChange={(event) => setSourceText(event.target.value)}
                        placeholder="粘贴完整正文，导入后自动分块并生成定位信息"
                      />
                    </label>
                  )}
                  {importNeedsFile && (
                    <label className="field-label library-import-wide library-file-drop">
                      PDF 文件（最大 16 MB）
                      <span className="library-file-box">
                        <AppIcon name="download" size={18} aria-hidden="true" />
                        <span>{file ? file.name : '选择 PDF 文件'}</span>
                        <input
                          type="file"
                          accept="application/pdf,.pdf"
                          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                        />
                      </span>
                    </label>
                  )}
                </div>
                <div className="library-form-actions">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={
                      importSource.isPending ||
                      (importNeedsUrl && !origin.trim()) ||
                      (importNeedsText && !sourceText.trim()) ||
                      (importNeedsFile && !file)
                    }
                    onClick={() => void importCurrentSource()}
                  >
                    <AppIcon
                      name={importSource.isPending ? 'loader' : 'plus'}
                      size={15}
                      className={importSource.isPending ? 'spin' : ''}
                      aria-hidden="true"
                    />
                    {importSource.isPending ? '正在提取与分块…' : '导入资料库'}
                  </button>
                </div>
              </section>
            )}

            <section className="panel library-sources" aria-labelledby="library-sources-title">
              <div className="panel-header">
                <h2 className="panel-title" id="library-sources-title">
                  来源审核 <small>{sources.data?.length ?? 0}</small>
                </h2>
              </div>
              {sources.isLoading && <Skeleton rows={3} />}
              {!sources.isLoading && sources.data?.length === 0 && (
                <EmptyState
                  icon="file-search"
                  title="当前资料库为空"
                  description="导入网页、DOI、文本、Markdown 或 PDF。"
                />
              )}
              <div className="library-source-list">
                {sources.data?.map((source) => {
                  const open = source.id === selectedSourceId
                  return (
                    <article key={source.id} className={`library-source${open ? ' is-open' : ''}`}>
                      <div className="library-source-row">
                        <button
                          type="button"
                          className="library-source-open"
                          aria-expanded={open}
                          onClick={() => setSelectedSourceId(open ? '' : source.id)}
                        >
                          <span className="library-source-icon" aria-hidden="true">
                            <AppIcon name={source.kind === 'pdf' ? 'file' : 'book'} size={16} />
                          </span>
                          <span className="library-source-text">
                            <strong>{source.title}</strong>
                            <small>
                              {source.kind.toUpperCase()} · {formatSize(source.char_count)} ·{' '}
                              {source.chunk_count} 个片段
                            </small>
                          </span>
                          <AppIcon
                            name={open ? 'chevron-down' : 'chevron-right'}
                            size={15}
                            className="library-source-chevron"
                            aria-hidden="true"
                          />
                        </button>
                        <span
                          className={`badge ${source.status === 'included' ? 'success' : 'muted'}`}
                        >
                          {source.status === 'included' ? '参与研究' : '已排除'}
                        </span>
                        {canEdit && (
                          <div className="library-source-actions">
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              onClick={() =>
                                setStatus.mutate({
                                  sourceId: source.id,
                                  status: source.status === 'included' ? 'excluded' : 'included',
                                })
                              }
                            >
                              <AppIcon
                                name={source.status === 'included' ? 'eye-off' : 'eye'}
                                size={14}
                                aria-hidden="true"
                              />
                              {source.status === 'included' ? '排除' : '纳入'}
                            </button>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm icon-button danger"
                              title="删除来源"
                              aria-label={`删除来源 ${source.title}`}
                              onClick={() => {
                                if (window.confirm(`删除来源“${source.title}”？`)) {
                                  void deleteSource.mutateAsync(source.id).then(() => {
                                    if (selectedSourceId === source.id) setSelectedSourceId('')
                                  })
                                }
                              }}
                            >
                              <AppIcon name="trash" size={14} aria-hidden="true" />
                            </button>
                          </div>
                        )}
                      </div>
                      {open && selectedSource && (
                        <div
                          className="library-chunks"
                          aria-label={`${selectedSource.title} 的原文片段`}
                        >
                          {chunks.isLoading && <Skeleton rows={3} />}
                          {chunks.data?.length === 0 && (
                            <p className="hint">这个来源还没有可用的原文片段。</p>
                          )}
                          {chunks.data?.map((chunk) => (
                            <article key={chunk.id} className="source-chunk">
                              <header>
                                <span>{chunk.locator}</span>
                                <code>{chunk.content_hash.slice(0, 10)}</code>
                              </header>
                              <p>{chunk.content}</p>
                            </article>
                          ))}
                        </div>
                      )}
                    </article>
                  )
                })}
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
