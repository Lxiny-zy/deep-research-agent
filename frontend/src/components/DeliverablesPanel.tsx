import { useState } from 'react'
import { AppIcon, type AppIconName } from './AppIcon'
import InfoTip from './InfoTip'
import { formatLabel, humanSize } from '../lib/workbench'
import { fetchDeliverable } from '../api/client'
import { downloadBlob } from '../lib/download'
import type { DeliverableItem, DeliverableRegistry, GateResult, GateStatus } from '../types'

const GATE_LABEL: Record<string, string> = {
  citation: '引用核查',
  scholarly: '学术质量',
  revision: '写作返工',
  markdown: 'Markdown 卫生',
  structure: '章节结构',
  length: '篇幅',
  consistency: '跨格式一致性',
  slides: '幻灯片版面',
  review: '评审评分',
  pdf: 'PDF 生成',
  render: '格式生成',
  territory: '地名规范',
  analysis: '统计分析',
  structured_content: '定稿一致性',
}

const GATE_HELP: Record<string, string> = {
  citation: '正文引用编号必须全部指向已核验来源，并达到本任务的引用下限。',
  scholarly:
    '学术文体（口语化、套话句式、生产过程描述）、摘要不带引用、引用堆砌、重复来源、时效覆盖与局限说明。',
  revision: '写作者按质量检查结果返工的次数，以及返工用尽后仍未解决的问题。',
  markdown: '交付源文件不含裸 HTML、页内锚点与未闭合代码块，保证各格式渲染一致。',
  structure: '任务模板承诺的章节必须齐全；思维导图须满足分支与节点数下限。',
  length: '正文篇幅不低于任务模板的下限。',
  consistency: 'Word、PDF、HTML 与 Markdown 源的图片数量一致，PDF 可完整抽出正文。',
  slides: '每页都有要点与演讲备注，且没有文字溢出页面的风险。',
  review: '同行评审必须给出 1–10 的整数评分。',
  pdf: 'PDF 能成功生成并通过字形与末段完整性自检。',
  territory: '所有交付物中的地名称谓符合规范。',
  render: '每种承诺的交付格式都成功生成；个别格式失败时其余格式照常交付。',
  analysis: '统计结果沿用本次任务的计算记录；无法检验、配对不明确或输入已变更时明确标注。',
  structured_content: '幻灯片内容与审核后的正文一致，避免修订后仍导出旧稿。',
}

const STATUS_META: Record<GateStatus, { label: string; icon: AppIconName }> = {
  pass: { label: '通过', icon: 'check-circle' },
  warn: { label: '需关注', icon: 'alert' },
  fail: { label: '未通过', icon: 'circle-x' },
}

/** 交付整体结论的措辞：需关注 = 已交付但未完全达到质量要求（部分完成） */
const OVERALL_LABEL: Record<GateStatus, string> = {
  pass: '自动检查通过',
  warn: '部分完成 · 有待改进项',
  fail: '质量验收未通过',
}

function splitIssues(issues: string[]): { hard: string[]; advice: string[] } {
  const advice = issues.filter((issue) => issue.startsWith('（建议）'))
  return {
    hard: issues.filter((issue) => !issue.startsWith('（建议）')),
    advice: advice.map((issue) => issue.replace(/^（建议）/, '')),
  }
}

function QualitySummary({ gates }: { gates: GateResult[] }) {
  const passed = gates.filter((gate) => gate.status === 'pass').length
  const citation = gates.find((gate) => gate.name === 'citation')
  const revision = gates.find((gate) => gate.name === 'revision')
  const used = Number(citation?.metrics?.used ?? NaN)
  const required = Number(citation?.metrics?.required ?? NaN)
  const revisions = Number(revision?.metrics?.revisions ?? NaN)
  return (
    <dl className="run-quality" aria-label="质量概览">
      <div>
        <dt>验收门</dt>
        <dd>
          {passed}/{gates.length} 通过
        </dd>
      </div>
      {Number.isFinite(used) && (
        <div className={Number.isFinite(required) && used < required ? 'is-short' : ''}>
          <dt>已核验引用</dt>
          <dd>
            {used}
            {Number.isFinite(required) && required > 0 && ` / 要求 ${required}`}
          </dd>
        </div>
      )}
      {Number.isFinite(revisions) && (
        <div>
          <dt>写作返工</dt>
          <dd>{revisions} 次</dd>
        </div>
      )}
    </dl>
  )
}

const FORMAT_ICON: Record<string, AppIconName> = {
  pdf: 'file',
  docx: 'file',
  html: 'eye',
  md: 'braces',
  pptx: 'presentation',
  png: 'chart',
  xlsx: 'database',
}

function GateRow({ gate }: { gate: GateResult }) {
  const meta = STATUS_META[gate.status]
  const label = GATE_LABEL[gate.name] ?? gate.name
  const { hard, advice } = splitIssues(gate.issues)
  return (
    <li className={`run-gate is-${gate.status}`}>
      <div className="run-gate-head">
        <AppIcon name={meta.icon} size={15} aria-hidden="true" />
        <span className="run-gate-name">
          {label}
          {GATE_HELP[gate.name] && <InfoTip text={GATE_HELP[gate.name]} label={`${label}说明`} />}
        </span>
        <span className="run-gate-status">{meta.label}</span>
      </div>
      {hard.length > 0 && (
        <ul className="run-gate-issues">
          {hard.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
      {advice.length > 0 && (
        <ul className="run-gate-issues is-advice" aria-label={`${label}改进建议`}>
          {advice.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </li>
  )
}

/**
 * 把取回的交付物放进预览窗口。
 *
 * blob URL 继承本站源，却带不上服务端的 CSP 头；HTML 若直接用 blob 打开，其中的脚本
 * （思维导图的折叠脚本，或将来某次转义疏漏）就能读到本站存储里的 API Key。
 * 所以 HTML 放进不带 allow-same-origin 的沙箱 iframe：脚本可以跑，但处在不透明源里。
 * PDF / PNG 交给浏览器内置查看器，不涉及页面脚本。
 */
function showPreview(popup: Window, item: DeliverableItem, blob: Blob) {
  if (item.mime_type.startsWith('text/html')) {
    void blob.text().then((html) => {
      const doc = popup.document
      doc.title = item.title
      doc.body.style.margin = '0'
      const frame = doc.createElement('iframe')
      frame.setAttribute('sandbox', 'allow-scripts')
      frame.setAttribute('title', item.title)
      frame.style.cssText = 'border:0;position:fixed;inset:0;width:100%;height:100%'
      frame.srcdoc = html
      doc.body.appendChild(frame)
    })
    return
  }
  const url = URL.createObjectURL(new Blob([blob], { type: item.mime_type }))
  popup.location.href = url
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

interface Props {
  runId: string
  registry: DeliverableRegistry | undefined
  loading: boolean
  error: unknown
}

/**
 * 交付物面板：按角色列出每个交付文件（大小、验收状态、下载），附验收门逐项结论。
 * 只对浏览器能原生打开的格式（自包含 HTML / PDF / PNG）提供预览，其余只提供下载。
 * 自包含 HTML 本身不含脚本（思维导图的折叠脚本除外，且不访问网络）。
 */
export default function DeliverablesPanel({ runId, registry, loading, error }: Props) {
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  async function open(item: DeliverableItem, preview: boolean) {
    setBusy(item.name)
    setActionError(null)
    // 预览窗口必须在点击的同步阶段打开：等交付物取回（PDF 可能要生成数秒）后再开，
    // 已超出浏览器的用户激活窗口，弹窗会被静默拦截。
    const popup = preview ? window.open('', '_blank') : null
    if (popup) popup.opener = null
    try {
      if (preview && !popup) throw new Error('浏览器拦截了预览窗口，请允许本站弹出窗口后重试')
      const file = registry?.content_version
        ? await fetchDeliverable(runId, item.name, undefined, registry.content_version)
        : await fetchDeliverable(runId, item.name)
      if (popup) {
        showPreview(popup, item, file.blob)
      } else {
        downloadBlob(file.filename, file.blob)
      }
    } catch (cause) {
      popup?.close()
      setActionError(cause instanceof Error ? cause.message : '下载失败')
    } finally {
      setBusy(null)
    }
  }

  if (loading) {
    return (
      <section className="panel run-deliverables" aria-busy="true">
        <p className="hint run-inline-status">
          <AppIcon name="loader" size={14} className="spin" aria-hidden="true" />
          正在生成交付物并运行验收门…
        </p>
      </section>
    )
  }
  if (error || !registry) {
    return (
      <section className="panel run-deliverables">
        <p className="hint">
          交付物暂不可用：{error instanceof Error ? error.message : '研究尚未完成'}
        </p>
      </section>
    )
  }
  const overall = STATUS_META[registry.status]
  const primary = registry.items.find((item) => item.name === registry.primary)
  const others = registry.items.filter((item) => item.name !== registry.primary)
  const previewable = (item: DeliverableItem) => ['html', 'pdf', 'png'].includes(item.format)
  const failing = registry.gates.filter((gate) => gate.status !== 'pass').length

  return (
    <section className="panel run-deliverables" aria-labelledby="deliverables-title">
      <div className="panel-header">
        <div>
          <h2 className="panel-title" id="deliverables-title">
            交付物
          </h2>
          <span className="hint">同一份定稿生成的全部格式，均已经过验收检查</span>
        </div>
        <span className={`run-verdict is-${registry.status}`}>
          <AppIcon name={overall.icon} size={14} aria-hidden="true" />
          {OVERALL_LABEL[registry.status]}
        </span>
      </div>
      <QualitySummary gates={registry.gates} />
      {primary && (
        <div className="run-primary-file">
          <span className="run-file-icon is-large" aria-hidden="true">
            <AppIcon name={FORMAT_ICON[primary.format] ?? 'file'} size={22} />
          </span>
          <div className="run-primary-meta">
            <span className="run-primary-label">主交付物</span>
            <strong>{primary.title}</strong>
            <span className="hint">
              {formatLabel(primary.format)} · {humanSize(primary.size)}
            </span>
          </div>
          <div className="run-file-actions">
            {previewable(primary) && (
              <button
                type="button"
                className="btn btn-sm"
                disabled={busy !== null}
                onClick={() => open(primary, true)}
              >
                <AppIcon name="eye" size={14} aria-hidden="true" /> 预览
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy !== null}
              onClick={() => open(primary, false)}
            >
              <AppIcon
                name={busy === primary.name ? 'loader' : 'download'}
                size={14}
                className={busy === primary.name ? 'spin' : ''}
                aria-hidden="true"
              />
              下载
            </button>
          </div>
        </div>
      )}
      {others.length > 0 && (
        <ul className="run-file-list">
          {others.map((item) => (
            <li key={item.name} className={`run-file-row is-${item.status}`}>
              <span className="run-file-icon" aria-hidden="true">
                <AppIcon name={FORMAT_ICON[item.format] ?? 'file'} size={16} />
              </span>
              <span className="run-file-meta">
                <span className="run-file-title">{item.title}</span>
                <span className="hint">
                  {formatLabel(item.format)} · {humanSize(item.size)}
                  {item.status !== 'pass' && ` · ${STATUS_META[item.status].label}`}
                </span>
              </span>
              <span className="run-file-actions">
                {previewable(item) && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm icon-button"
                    aria-label={`预览 ${item.title}`}
                    title="预览"
                    disabled={busy !== null}
                    onClick={() => open(item, true)}
                  >
                    <AppIcon name="eye" size={14} aria-hidden="true" />
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-ghost btn-sm icon-button"
                  aria-label={`下载 ${item.title}`}
                  title="下载"
                  disabled={busy !== null}
                  onClick={() => open(item, false)}
                >
                  <AppIcon
                    name={busy === item.name ? 'loader' : 'download'}
                    size={14}
                    className={busy === item.name ? 'spin' : ''}
                    aria-hidden="true"
                  />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {actionError && (
        <div className="alert error" role="alert">
          <AppIcon name="circle-x" size={14} aria-hidden="true" /> {actionError}
        </div>
      )}
      <details className="run-gates" open={registry.status !== 'pass'}>
        <summary>
          <AppIcon name="chevron-right" size={14} aria-hidden="true" />
          验收门明细（{registry.gates.length} 项）
          {failing > 0 && <span className="badge warning">{failing} 项需处理</span>}
        </summary>
        <ul className="run-gate-list">
          {registry.gates.map((gate) => (
            <GateRow key={gate.name} gate={gate} />
          ))}
        </ul>
      </details>
    </section>
  )
}
