import { useEffect, useRef, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useConfig } from '../hooks/useConfig'
import { downloadBlob } from '../lib/download'
import {
  AcceptanceError,
  createAcceptanceRecord,
  getAcceptanceContext,
  getAcceptancePackage,
  getAcceptanceRecord,
  getAcceptanceSlice,
  listAcceptanceRecords,
  type AcceptanceConclusion,
  type AcceptanceInput,
  type AcceptanceIssueInput,
  type FirstAttemptStatus,
  type IssueCategory,
} from '../api/acceptance'

const conclusions = {
  pending: '待人工确认',
  pass: '通过',
  fail: '不通过',
  mixed: '部分通过',
  uncertain: '暂无法判断',
}
const categories: Record<IssueCategory, string> = {
  correctness: '内容正确性',
  coverage: '要求覆盖',
  citation: '引用与依据',
  format: '交付格式',
  layout: '布局阅读',
  interaction: '交互',
  context: '上下文',
  performance: '耗时与性能',
  other: '其他',
}
const statuses: Record<string, string> = {
  unknown: '未记录',
  done: '完成',
  needs_review: '待复核',
  error: '失败',
  cancelled: '已停止',
  covered: '已覆盖',
  missing: '未覆盖',
  partial: '部分覆盖',
  insufficient: '依据不足',
  not_checked: '未检查',
}
const statusText = (value: string) => statuses[value] || value || '未知'
const message = (error: unknown) => (error instanceof Error ? error.message : '操作失败，请重试。')

export default function AcceptancePanel({
  runId,
  documentVersion,
  deliveryVersion,
  includeHsiTables,
  onRefreshVersion,
}: {
  runId: string
  documentVersion: string
  deliveryVersion?: string
  includeHsiTables: boolean
  onRefreshVersion: () => void
}) {
  const [open, setOpen] = useState(false)
  const [version, setVersion] = useState(documentVersion)
  const [withDelivery, setWithDelivery] = useState(false)
  const [phase, setPhase] = useState<'initial' | 'recovery'>('initial')
  const [parent, setParent] = useState('')
  const [firstStatus, setFirstStatus] = useState<FirstAttemptStatus>('unknown')
  const [conclusion, setConclusion] = useState<AcceptanceConclusion>('pending')
  const [note, setNote] = useState('')
  const [issues, setIssues] = useState<AcceptanceIssueInput[]>([])
  const [locationId, setLocationId] = useState('')
  const [locationOffset, setLocationOffset] = useState(0)
  const [locationExcerpt, setLocationExcerpt] = useState('')
  const [includeExcerpt, setIncludeExcerpt] = useState(false)
  const [evidenceId, setEvidenceId] = useState('')
  const [evidenceExcerpt, setEvidenceExcerpt] = useState('')
  const [includeEvidence, setIncludeEvidence] = useState(false)
  const [requirementId, setRequirementId] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [category, setCategory] = useState<IssueCategory>('correctness')
  const [observation, setObservation] = useState('')
  const [issueConclusion, setIssueConclusion] =
    useState<Exclude<AcceptanceConclusion, 'mixed'>>('pending')
  const [selectedRecord, setSelectedRecord] = useState('')
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const submission = useRef<{ payload: string; id: string } | null>(null)
  const client = useQueryClient()
  const config = useConfig()
  const canWrite = Boolean(config.data && config.data.access?.role !== 'reader')
  const versionChanged = version !== documentVersion
  const context = useQuery({
    queryKey: ['acceptance-context', runId, version, includeHsiTables],
    queryFn: ({ signal }) => getAcceptanceContext(runId, version, includeHsiTables, signal),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const records = useInfiniteQuery({
    queryKey: ['acceptance-records', runId],
    queryFn: ({ pageParam, signal }) => listAcceptanceRecords(runId, pageParam, signal),
    initialPageParam: '',
    getNextPageParam: (page) => page.next_cursor || undefined,
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const history = records.data?.pages.flatMap((page) => page.items) || []
  const location = useQuery({
    queryKey: ['acceptance-location', runId, version, includeHsiTables, locationId, locationOffset],
    queryFn: ({ signal }) =>
      getAcceptanceSlice(
        runId,
        version,
        includeHsiTables,
        'locations',
        locationId,
        locationOffset,
        signal,
      ),
    enabled: open && Boolean(locationId),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
  const evidence = useQuery({
    queryKey: ['acceptance-evidence', runId, version, includeHsiTables, evidenceId],
    queryFn: ({ signal }) =>
      getAcceptanceSlice(runId, version, includeHsiTables, 'evidence', evidenceId, 0, signal),
    enabled: open && Boolean(evidenceId),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
  const record = useQuery({
    queryKey: ['acceptance-record', runId, selectedRecord],
    queryFn: ({ signal }) => getAcceptanceRecord(runId, selectedRecord, signal),
    enabled: open && Boolean(selectedRecord),
    retry: false,
  })
  useEffect(() => {
    setLocationExcerpt(location.data?.excerpt || '')
    setIncludeExcerpt(false)
  }, [location.data])
  useEffect(() => {
    setEvidenceExcerpt(evidence.data?.excerpt || '')
    setIncludeEvidence(false)
  }, [evidence.data])
  const save = useMutation({
    mutationFn: (body: AcceptanceInput) => createAcceptanceRecord(runId, body),
    onSuccess: (saved) => {
      setSelectedRecord(saved.id)
      setError('')
      client.setQueryData(['acceptance-record', runId, saved.id], saved)
      void client.invalidateQueries({ queryKey: ['acceptance-records', runId] })
    },
  })
  const conflict =
    versionChanged ||
    [context.error, save.error, location.error, evidence.error].some(
      (cause) =>
        cause instanceof AcceptanceError &&
        cause.status === 409 &&
        cause.code === 'document_version_changed',
    )

  function resetIssue() {
    setLocationId('')
    setLocationOffset(0)
    setIncludeExcerpt(false)
    setLocationExcerpt('')
    setEvidenceId('')
    setIncludeEvidence(false)
    setEvidenceExcerpt('')
    setRequirementId('')
    setSourceId('')
    setObservation('')
    setIssueConclusion('pending')
  }
  function addIssue() {
    if (!observation.trim()) return setError('请填写具体观察。')
    if (!locationId && !requirementId && !sourceId && !(includeEvidence && evidenceId))
      return setError('请选择正文位置、要求、来源或原文片段。')
    if (
      (includeExcerpt && (location.data?.excerpt_redacted || !locationExcerpt.trim())) ||
      (includeEvidence && (evidence.data?.excerpt_redacted || !evidenceExcerpt.trim()))
    )
      return setError('请取消遮盖片段的勾选，或填写所选原文中的必要片段。')
    setIssues((current) => [
      ...current,
      {
        ...(locationId ? { location_id: locationId } : {}),
        requirement_ids: requirementId ? [requirementId] : [],
        source_ids: sourceId ? [sourceId] : [],
        excerpt: includeExcerpt ? locationExcerpt : '',
        evidence_selections: includeEvidence
          ? [{ evidence_id: evidenceId, excerpt: evidenceExcerpt }]
          : [],
        category,
        observation: observation.trim(),
        conclusion: issueConclusion,
        next_work: [],
      },
    ])
    resetIssue()
    setError('')
  }
  function submit() {
    if (observation.trim()) return setError('先把正在填写的观察加入问题清单，再保存记录。')
    if (phase === 'recovery' && !parent) return setError('请选择这次恢复观察对应的首次或先前记录。')
    const payload = {
      document_version: version,
      ...(withDelivery && deliveryVersion ? { delivery_version: deliveryVersion } : {}),
      include_hsi_tables: includeHsiTables,
      phase,
      first_attempt_status: phase === 'recovery' ? ('unknown' as const) : firstStatus,
      ...(phase === 'recovery' ? { parent_record_id: parent } : {}),
      conclusion,
      issues,
      note,
    }
    const fingerprint = JSON.stringify(payload)
    if (submission.current?.payload !== fingerprint)
      submission.current = { payload: fingerprint, id: crypto.randomUUID() }
    setError('')
    save.mutate({ ...payload, request_id: submission.current.id })
  }
  function startCurrent() {
    setVersion(documentVersion)
    setWithDelivery(false)
    setIssues([])
    setNote('')
    setConclusion('pending')
    setFirstStatus('unknown')
    setPhase('initial')
    setParent('')
    resetIssue()
    save.reset()
    setError('')
    submission.current = null
  }
  async function download() {
    if (!record.data) return
    setDownloading(true)
    setError('')
    try {
      downloadBlob(
        `acceptance-${record.data.id.slice(0, 16)}.json`,
        await getAcceptancePackage(runId, record.data),
      )
    } catch (cause) {
      setError(message(cause))
    } finally {
      setDownloading(false)
    }
  }

  return (
    <details
      className="panel acceptance-panel"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="run-collapsible-head">
        <span className="panel-title">人工验收记录</span>
        <span className="hint">问题定位、首次与恢复观察</span>
      </summary>
      {open && (
        <div className="acceptance-body stack">
          <p className="hint">仅保存你主动选择的位置、片段和观察。没有人工结论时保持待确认。</p>
          <p>
            本次登记绑定文档版本 <code title={version}>{version.slice(0, 12)}</code>
          </p>
          {conflict && (
            <div className="alert warning" role="alert">
              报告版本已变化或所选版本不可用。当前填写内容仍保留，不能自动关联到新稿。
              <button className="btn btn-secondary btn-sm" type="button" onClick={onRefreshVersion}>
                加载最新报告版本
              </button>
              {versionChanged && (
                <button className="btn btn-secondary btn-sm" type="button" onClick={startCurrent}>
                  清空未提交内容并登记当前版本
                </button>
              )}
            </div>
          )}
          {context.isError && !conflict && <p role="alert">{message(context.error)}</p>}
          {context.isLoading && <p role="status">正在读取版本与可选位置…</p>}
          {context.data && (
            <>
              <p className="hint">
                当前运行：{statusText(context.data.observed_status)}
                ；工程核验记录与人工结论分别保留。
              </p>
              {(context.data.coverage_issues?.length > 0 ||
                context.data.prose_review_issues?.length > 0) && (
                <ul className="acceptance-engineering-notes">
                  {[...context.data.coverage_issues, ...context.data.prose_review_issues].map(
                    (item, i) => (
                      <li key={i}>{item}</li>
                    ),
                  )}
                </ul>
              )}
              {canWrite ? (
                <fieldset className="stack" disabled={save.isPending || conflict}>
                  <div className="acceptance-grid">
                    <label className="field-label">
                      本次性质
                      <select
                        className="input"
                        value={phase}
                        onChange={(event) => setPhase(event.target.value as typeof phase)}
                      >
                        <option value="initial">首次登记</option>
                        <option value="recovery">恢复观察（保留原记录）</option>
                      </select>
                    </label>
                    <label className="field-label">
                      首次实际结果
                      <select
                        className="input"
                        value={firstStatus}
                        disabled={phase === 'recovery'}
                        onChange={(event) =>
                          setFirstStatus(event.target.value as FirstAttemptStatus)
                        }
                      >
                        {Object.entries(statuses)
                          .filter(([key]) =>
                            ['unknown', 'done', 'needs_review', 'error', 'cancelled'].includes(key),
                          )
                          .map(([key, label]) => (
                            <option key={key} value={key}>
                              {label}
                            </option>
                          ))}
                      </select>
                    </label>
                  </div>
                  {phase === 'recovery' && (
                    <label className="field-label">
                      先前验收记录
                      <select
                        className="input"
                        value={parent}
                        onChange={(event) => setParent(event.target.value)}
                      >
                        <option value="">请选择</option>
                        {history.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.id.slice(0, 12)} · {conclusions[item.conclusion]} ·{' '}
                            {item.document_version.slice(0, 12)}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {deliveryVersion && (
                    <label className="acceptance-check">
                      <input
                        type="checkbox"
                        checked={withDelivery}
                        onChange={(event) => setWithDelivery(event.target.checked)}
                      />
                      关联当前交付版本 {deliveryVersion.slice(0, 12)}
                    </label>
                  )}
                  <details className="acceptance-issue-editor">
                    <summary>添加具体问题</summary>
                    <div className="stack">
                      <label className="field-label">
                        正文或图表位置
                        <select
                          className="input"
                          value={locationId}
                          onChange={(event) => {
                            setLocationId(event.target.value)
                            setLocationOffset(0)
                            setIncludeExcerpt(false)
                            setLocationExcerpt('')
                          }}
                        >
                          <option value="">请选择位置</option>
                          {context.data.locations.map((item, index) => (
                            <option key={item.id} value={item.id}>
                              {index + 1}. {item.preview?.slice(0, 80) || item.kind}
                            </option>
                          ))}
                        </select>
                      </label>
                      {location.data && (
                        <div className="stack">
                          <p className="hint">所选位置片段（第 {locationOffset + 1} 字起）</p>
                          <blockquote className="acceptance-excerpt">
                            {location.data.excerpt}
                          </blockquote>
                          <div className="row">
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              disabled={locationOffset === 0}
                              onClick={() => setLocationOffset(Math.max(0, locationOffset - 600))}
                            >
                              上一段
                            </button>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              disabled={locationOffset + 600 >= location.data.total_characters}
                              onClick={() => setLocationOffset(locationOffset + 600)}
                            >
                              下一段
                            </button>
                          </div>
                          {location.data.excerpt_redacted ? (
                            <p className="hint">片段含遮盖内容，仅保存位置标识。</p>
                          ) : (
                            <>
                              <label className="acceptance-check">
                                <input
                                  type="checkbox"
                                  checked={includeExcerpt}
                                  onChange={(event) => setIncludeExcerpt(event.target.checked)}
                                />
                                将必要的正文片段附入记录
                              </label>
                              {includeExcerpt && (
                                <label className="field-label">
                                  正文问题片段
                                  <textarea
                                    className="textarea"
                                    maxLength={1200}
                                    value={locationExcerpt}
                                    onChange={(event) => setLocationExcerpt(event.target.value)}
                                  />
                                </label>
                              )}
                            </>
                          )}
                        </div>
                      )}
                      <label className="field-label">
                        对应任务要求
                        <select
                          className="input"
                          value={requirementId}
                          onChange={(event) => setRequirementId(event.target.value)}
                        >
                          <option value="">不指定</option>
                          {context.data.requirements.map((item) => (
                            <option value={item.id} key={item.id}>
                              {item.label} · {statusText(item.status)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field-label">
                        来源标识
                        <select
                          className="input"
                          value={sourceId}
                          onChange={(event) => setSourceId(event.target.value)}
                        >
                          <option value="">不指定</option>
                          {context.data.sources.map((item, index) => (
                            <option value={item.id} key={item.id}>
                              {index + 1}. {item.locator || item.section || item.id.slice(0, 20)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field-label">
                        查看原文依据
                        <select
                          className="input"
                          value={evidenceId}
                          onChange={(event) => {
                            setEvidenceId(event.target.value)
                            setIncludeEvidence(false)
                            setEvidenceExcerpt('')
                          }}
                        >
                          <option value="">不附原文</option>
                          {context.data.evidence.map((item, index) => (
                            <option value={item.id} key={item.id}>
                              依据 {index + 1} · {statusText(item.semantic_status)}
                            </option>
                          ))}
                        </select>
                      </label>
                      {evidence.data && (
                        <>
                          <blockquote className="acceptance-excerpt">
                            {evidence.data.excerpt}
                          </blockquote>
                          {evidence.data.excerpt_redacted ? (
                            <p className="hint">该依据含遮盖内容，请仅选择来源标识。</p>
                          ) : (
                            <>
                              <label className="acceptance-check">
                                <input
                                  type="checkbox"
                                  checked={includeEvidence}
                                  onChange={(event) => setIncludeEvidence(event.target.checked)}
                                />
                                将这段原文附入问题记录
                              </label>
                              {includeEvidence && (
                                <label className="field-label">
                                  必要的依据片段
                                  <textarea
                                    className="textarea"
                                    maxLength={1200}
                                    value={evidenceExcerpt}
                                    onChange={(event) => setEvidenceExcerpt(event.target.value)}
                                  />
                                </label>
                              )}
                            </>
                          )}
                        </>
                      )}
                      {(location.error || evidence.error) && (
                        <p role="alert">{message(location.error || evidence.error)}</p>
                      )}
                      <div className="acceptance-grid">
                        <label className="field-label">
                          问题类别
                          <select
                            className="input"
                            value={category}
                            onChange={(event) => setCategory(event.target.value as IssueCategory)}
                          >
                            {Object.entries(categories).map(([key, label]) => (
                              <option value={key} key={key}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field-label">
                          该问题结论
                          <select
                            className="input"
                            value={issueConclusion}
                            onChange={(event) =>
                              setIssueConclusion(event.target.value as typeof issueConclusion)
                            }
                          >
                            {Object.entries(conclusions)
                              .filter(([key]) => key !== 'mixed')
                              .map(([key, label]) => (
                                <option key={key} value={key}>
                                  {label}
                                </option>
                              ))}
                          </select>
                        </label>
                      </div>
                      <label className="field-label">
                        具体观察
                        <textarea
                          className="textarea"
                          maxLength={1000}
                          value={observation}
                          onChange={(event) => setObservation(event.target.value)}
                        />
                      </label>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        disabled={issues.length >= 30 || location.isFetching || evidence.isFetching}
                        onClick={addIssue}
                      >
                        加入问题清单
                      </button>
                    </div>
                  </details>
                  {issues.length > 0 && (
                    <ol className="acceptance-issues">
                      {issues.map((item, index) => (
                        <li key={index}>
                          <strong>
                            {categories[item.category]} · {conclusions[item.conclusion]}
                          </strong>
                          <p>{item.observation}</p>
                          <span className="hint">
                            正文片段 {item.excerpt.length} 字 · 原文片段{' '}
                            {item.evidence_selections.length} 条
                          </span>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() =>
                              setIssues((items) => items.filter((_, i) => i !== index))
                            }
                          >
                            移除问题 {index + 1}
                          </button>
                        </li>
                      ))}
                    </ol>
                  )}
                  <label className="field-label">
                    总体人工结论
                    <select
                      className="input"
                      value={conclusion}
                      onChange={(event) =>
                        setConclusion(event.target.value as AcceptanceConclusion)
                      }
                    >
                      {Object.entries(conclusions).map(([key, label]) => (
                        <option key={key} value={key}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    补充说明
                    <textarea
                      className="textarea"
                      maxLength={1000}
                      value={note}
                      onChange={(event) => setNote(event.target.value)}
                    />
                  </label>
                  <button type="button" className="btn btn-primary" onClick={submit}>
                    {save.isPending ? '正在保存…' : '保存人工验收记录'}
                  </button>
                </fieldset>
              ) : (
                <p className="hint">当前身份可查看已保存记录。</p>
              )}
            </>
          )}
          {(error || save.error) && (
            <p className="error-text" role="alert">
              {error || message(save.error)}
            </p>
          )}
          {save.isSuccess && (
            <p role="status">记录已保存，结论：{conclusions[save.data.conclusion]}。</p>
          )}
          <section className="stack" aria-label="历史验收记录">
            <h3>已保存的版本记录</h3>
            {records.isError && <p role="alert">{message(records.error)}</p>}
            {history.length === 0 && !records.isLoading && (
              <p className="hint">尚未登记人工验收。</p>
            )}
            <ul className="acceptance-history">
              {history.map((item) => (
                <li key={item.id}>
                  <button
                    className="btn btn-ghost"
                    type="button"
                    onClick={() => setSelectedRecord(item.id)}
                  >
                    {item.phase === 'recovery' ? '恢复观察' : '首次登记'} ·{' '}
                    {conclusions[item.conclusion]} · {item.document_version.slice(0, 12)}
                    <span className="hint">{item.created_at}</span>
                  </button>
                </li>
              ))}
            </ul>
            {records.hasNextPage && (
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => void records.fetchNextPage()}
                disabled={records.isFetchingNextPage}
              >
                更多记录
              </button>
            )}
          </section>
          {record.isError && <p role="alert">{message(record.error)}</p>}
          {record.data && (
            <section className="acceptance-saved stack" aria-label="选中的验收记录">
              <h3>
                {record.data.phase === 'recovery' ? '恢复观察' : '首次登记'} ·{' '}
                {conclusions[record.data.conclusion]}
              </h3>
              <p>
                记录版本 <code>{record.data.document_version.slice(0, 12)}</code>；首次实际结果：
                {statusText(record.data.first_attempt_status)}；本次观察：
                {statusText(record.data.observed_status)}
              </p>
              {record.data.parent_record_id && (
                <p className="hint">关联先前记录 {record.data.parent_record_id.slice(0, 12)}</p>
              )}
              <p>{record.data.note}</p>
              {record.data.issues.map((item, index) => (
                <article key={index}>
                  <strong>
                    {categories[item.category]} · {conclusions[item.conclusion]}
                  </strong>
                  <p>{item.observation}</p>
                  {item.excerpt && (
                    <blockquote className="acceptance-excerpt">{item.excerpt}</blockquote>
                  )}
                  {item.evidence?.map(
                    (selected, position) =>
                      selected.excerpt && (
                        <blockquote key={position} className="acceptance-excerpt">
                          {selected.excerpt}
                        </blockquote>
                      ),
                  )}
                </article>
              ))}
              <button
                type="button"
                className="btn btn-secondary"
                disabled={downloading}
                onClick={() => void download()}
              >
                {downloading ? '正在下载…' : '下载最小问题包'}
              </button>
            </section>
          )}
        </div>
      )}
    </details>
  )
}
