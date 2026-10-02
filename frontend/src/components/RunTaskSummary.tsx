import { AppIcon } from './AppIcon'
import { templateIcon } from '../lib/workbench'
import type { RunTemplateInfo } from '../types'

/**
 * 运行详情里的「任务卡」：这次跑的是哪类任务、系统理解了什么、取回了哪些论文章节、
 * 评审分数或统计规模。深度研究（默认模板且无契约）的历史运行不渲染，避免重复信息。
 */
export default function RunTaskSummary({ info }: { info: RunTemplateInfo | undefined }) {
  if (!info) return null
  const { template, contract, extras, intake, analysis } = info
  if (template.key === 'autoResearch' && !contract) return null
  const failures = intake?.failures ?? []
  return (
    <section className="panel run-task-summary" aria-label="任务信息">
      <div className="run-task-head">
        <span className="run-task-icon" aria-hidden="true">
          <AppIcon name={templateIcon(template.icon)} size={18} />
        </span>
        <div className="run-task-title">
          <span className="panel-kicker">任务类型</span>
          <strong>{template.title}</strong>
        </div>
        {typeof extras.score === 'number' && (
          <span className="review-score" aria-label={`评审评分 ${extras.score} 分（满分 10 分）`}>
            <strong>{extras.score}</strong>
            <span>/10</span>
          </span>
        )}
      </div>
      <dl className="run-task-facts">
        {info.revision_source && (
          <>
            <dt>内容修订</dt>
            <dd>
              本次复用原任务资料继续修订。{' '}
              <a href={`/runs/${encodeURIComponent(info.revision_source.parent_run_id)}`}>
                查看原任务
              </a>
            </dd>
          </>
        )}
        {contract?.papers && contract.papers.length > 0 && (
          <>
            <dt>指定论文</dt>
            <dd>
              <ul className="run-task-list">
                {contract.papers.map((paper) => (
                  <li key={paper.url}>
                    <a href={paper.url} target="_blank" rel="noopener noreferrer">
                      {paper.value}
                    </a>
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {intake?.mode === 'missing' && (
          <>
            <dt>论文导入</dt>
            <dd>
              <span className="run-task-warning">
                <AppIcon name="alert" size={13} aria-hidden="true" />
                {failures[0]?.error ?? '未提供论文'}
              </span>
            </dd>
          </>
        )}
        {intake && intake.mode !== 'missing' && (
          <>
            <dt>论文导入</dt>
            <dd>
              {intake.mode === 'attachments'
                ? '以上传文件为研究对象，'
                : intake.mode === 'pasted'
                  ? '以粘贴的论文文本为研究对象，'
                  : ''}
              取得 {intake.sections.length} 个章节来源
              {intake.sections.some((section) => section.section) &&
                `（${[...new Set(intake.sections.map((s) => s.section).filter(Boolean))].join('、')}）`}
              {failures.length > 0 && (
                <span className="run-task-warning">
                  <AppIcon name="alert" size={13} aria-hidden="true" />
                  {failures.length} 篇获取失败：{failures[0].error}
                </span>
              )}
            </dd>
          </>
        )}
        {analysis && (
          <>
            <dt>数据</dt>
            <dd>
              {analysis.rows} 行 × {analysis.columns.length} 列，{analysis.tests.length}{' '}
              项显著性检验
              {analysis.synthetic
                ? '（示例数据，结论不代表真实实验）'
                : contract?.dataset_source?.filename
                  ? `，来自 ${contract.dataset_source.filename}${
                      contract.dataset_source.sheet
                        ? `（工作表「${contract.dataset_source.sheet}」）`
                        : ''
                    }，完整使用未截断`
                  : '，来自粘贴的数据，完整使用未截断'}
            </dd>
          </>
        )}
        {extras.stats && (
          <>
            <dt>导图规模</dt>
            <dd>
              {extras.stats.branches} 个分支 · {extras.stats.nodes} 个节点
            </dd>
          </>
        )}
        {contract?.focus && (
          <>
            <dt>关注点</dt>
            <dd>{contract.focus}</dd>
          </>
        )}
      </dl>
    </section>
  )
}
