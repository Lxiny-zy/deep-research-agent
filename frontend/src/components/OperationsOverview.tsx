import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getOperations } from '../api/operations'
import { AppIcon } from './AppIcon'

const SCENARIOS: Record<string, string> = {
  autoResearch: '课题调研', litReview: '文献综述', peerReview: '同行评审', paperRead: '论文精读',
  dataAnalysis: '数据分析', slides: '幻灯片', mindmap: '思维导图', qa: '学术问答',
  bound_qa: '材料关联问答', other: '其他 / 历史流程',
}
const REASONS: Record<string, string> = {
  prose_evidence: '结论证据', node_evidence: '导图证据', requested_content: '内容覆盖',
  file_readability: '文件可读取性', consistency: '内容一致性', analysis: '统计分析',
  review: '评审检查', slides: '幻灯片检查', length: '内容长度', structure: '结构检查',
  unspecified: '未记录具体类别', other: '其他检查',
}
const number = (value: number | null | undefined) => value == null ? '未知' : value.toLocaleString()
const seconds = (value: number | null | undefined) => value == null ? '未知' : `${value.toFixed(1)} 秒`

function OperationsContent({ allowWorkspace }: { allowWorkspace: boolean }) {
  const [days, setDays] = useState(7)
  const [scope, setScope] = useState<'mine' | 'workspace'>('mine')
  const query = useQuery({
    queryKey: ['operations-overview', days, scope],
    queryFn: ({ signal }) => getOperations(days, scope, signal),
    staleTime: 30_000,
  })
  const data = query.data
  return (
    <div className="operations-body stack">
      <div className="operations-controls">
        <label>时间范围
          <select className="input" value={days} onChange={(event) => setDays(Number(event.target.value))}>
            <option value={1}>最近 24 小时</option><option value={7}>最近 7 天</option><option value={30}>最近 30 天</option>
          </select>
        </label>
        {allowWorkspace && <label>统计对象
          <select className="input" value={scope} onChange={(event) => setScope(event.target.value as 'mine' | 'workspace')}>
            <option value="mine">当前身份</option><option value="workspace">整个工作区</option>
          </select>
        </label>}
        <button className="btn btn-secondary btn-sm" type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新统计</button>
      </div>
      {query.isPending && <p role="status">正在读取运行记录…</p>}
      {query.error && <p className="error-text" role="alert">{query.error.message}{data ? '；以下保留上次取回的记录。' : ''}</p>}
      {data && <>
        <p className="hint">按任务创建时间（UTC）汇总，截至 {new Date(data.as_of).toLocaleString()}。系统完成状态不代表人工科研质量验收。</p>
        {data.coverage.truncated && <p className="error-text" role="alert">记录超过读取上限，当前不是所选时间范围的全量统计。</p>}
        <dl className="operations-totals">
          <div><dt>研究任务</dt><dd>{data.coverage.research_records}</dd></div>
          <div><dt>问答轮次</dt><dd>{data.coverage.qa_records}</dd></div>
          <div><dt>已记录模型请求</dt><dd>{data.model_calls.recorded_attempts}</dd></div>
          <div><dt>请求重试</dt><dd>{data.model_calls.retries}</dd></div>
        </dl>
        {data.scenarios.length ? <div className="operations-table-wrap" tabIndex={0} aria-label="各场景运行统计，可横向滚动">
          <table className="operations-table">
            <caption>场景执行情况</caption>
            <thead><tr><th>场景</th><th>任务数</th><th>系统完成</th><th>首次交付通过</th><th>继续 / 恢复</th><th>待复核</th><th>降级回答</th><th>失败</th></tr></thead>
            <tbody>{data.scenarios.map((row) => <tr key={row.scenario}>
              <th scope="row">{SCENARIOS[row.scenario] ?? '其他场景'}</th>
              <td>{row.total}</td><td>{row.statuses.done ?? 0}</td><td>{row.initial_done}</td>
              <td>{(row.origins.continuation ?? 0) + (row.origins.recovered ?? 0)}</td>
              <td>{row.statuses.needs_review ?? 0}</td><td>{row.statuses.fallback ?? 0}</td><td>{row.statuses.error ?? 0}</td>
            </tr>)}</tbody>
          </table>
        </div> : <p className="hint">这个时间范围内没有可归入统计的记录。</p>}
        <p className="hint">首次交付按第一次保存的检查结果计数，不包含显式继续或执行重启；{data.coverage.unknown_research_first_results} 项研究缺少首次记录，不补算为通过。费用未知：当前没有核实的计价配置。</p>
        <div className="operations-table-wrap" tabIndex={0} aria-label="已知模型用量，可横向滚动">
          <table className="operations-table">
            <caption>提供方返回的用量</caption>
            <thead><tr><th>字段</th><th>已知部分合计</th><th>已返回请求</th><th>未返回请求</th></tr></thead>
            <tbody>{[['input_tokens', '输入'], ['output_tokens', '输出'], ['reasoning_tokens', '推理'], ['total_tokens', '总量']].map(([key, label]) => {
              const usage = data.model_calls.usage[key]
              return <tr key={key}><th scope="row">{label}</th><td>{number(usage?.known_total)}</td><td>{usage?.reported_calls ?? 0}</td><td>{usage?.unknown_calls ?? 0}</td></tr>
            })}</tbody>
          </table>
        </div>
        <p className="hint">模型请求耗时 P95：{seconds(data.model_calls.p95_seconds)}（{data.model_calls.duration_observations} 条）；已结束研究耗时 P95：{seconds(data.research_duration.p95_seconds)}（{data.research_duration.observations} 条）。</p>
        {Object.keys(data.needs_review_reasons).length > 0 && <ul className="operations-reasons" aria-label="待复核原因">
          {Object.entries(data.needs_review_reasons).map(([reason, count]) => <li key={reason}>{REASONS[reason] ?? '其他检查'}：{count} 项</li>)}
        </ul>}
        {data.rendering && <p className="hint">渲染记录 {data.rendering.records} 项：排队 {data.rendering.statuses.pending ?? 0}、运行 {data.rendering.statuses.running ?? 0}、失败 {data.rendering.statuses.error ?? 0}；重试 {data.rendering.retried} 项，停滞记录 {data.rendering.stalled} 项。最久等待：{seconds(data.rendering.oldest_pending_seconds)}。</p>}
        {data.daily.length > 0 && <details>
          <summary>按日查看执行趋势</summary>
          <div className="operations-table-wrap" tabIndex={0} aria-label="每日执行情况，可横向滚动">
            <table className="operations-table"><thead><tr><th>创建日期（UTC）</th><th>系统完成</th><th>待复核</th><th>降级回答</th><th>失败</th><th>取消</th></tr></thead>
              <tbody>{data.daily.map((row) => <tr key={row.date}><th scope="row">{row.date}</th>{['done', 'needs_review', 'fallback', 'error', 'cancelled'].map((status) => <td key={status}>{row.statuses[status] ?? 0}</td>)}</tr>)}</tbody>
            </table>
          </div>
        </details>}
        <ul className="hint operations-limitations">{data.limitations.map((text) => <li key={text}>{text}</li>)}</ul>
      </>}
    </div>
  )
}

export default function OperationsOverview({ allowWorkspace = false }: { allowWorkspace?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <details className="panel operations-overview" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary><AppIcon name="chart" size={16} aria-hidden="true" />运行与请求统计</summary>
      {open && <OperationsContent allowWorkspace={allowWorkspace} />}
    </details>
  )
}
