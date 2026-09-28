import { AppIcon } from './AppIcon'
import { formatLabel } from '../lib/workbench'
import type { TaskContract, TaskTemplate } from '../types'

interface Props {
  template: TaskTemplate
  contract: TaskContract | undefined
  loading: boolean
  error: unknown
  uploadedDataset?: string | null
}

/**
 * 「系统将如何理解这次任务」：在提交前展示解析出的论文、数据规模、必答章节与约束。
 * 论文类任务没识别到链接时给出明确提示——这比跑完才发现评审对象不对要好得多。
 */
export default function ContractPreview({
  template,
  contract,
  loading,
  error,
  uploadedDataset,
}: Props) {
  const needsPaper = template.input_kind === 'paper'
  const needsData = template.input_kind === 'dataset'
  return (
    <section className="home-card contract" aria-live="polite" aria-busy={loading}>
      <div className="home-card-head">
        <span className="home-card-title">
          <AppIcon name="scan-search" size={15} aria-hidden="true" />
          任务理解预览
        </span>
        {loading && <AppIcon name="loader" size={14} className="spin" aria-label="正在解析" />}
      </div>
      {error ? (
        <p className="hint">暂时无法预览任务契约，提交时仍会按模板处理。</p>
      ) : !contract ? (
        <p className="hint">输入内容后，这里会显示系统识别出的研究对象与交付要求。</p>
      ) : (
        <dl className="contract-grid">
          {needsPaper && (
            <div className="contract-row">
              <dt>指定论文</dt>
              <dd>
                {contract.papers.length ? (
                  <ul className="contract-list">
                    {contract.papers.map((paper) => (
                      <li key={paper.url}>
                        <span className="contract-tag">{paper.kind}</span>
                        <span>{paper.value}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="contract-warning">
                    <AppIcon name="alert" size={13} aria-hidden="true" />
                    未识别到 arXiv / DOI / 链接，将改为按主题检索
                  </span>
                )}
              </dd>
            </div>
          )}
          {needsData && (
            <div className="contract-row">
              <dt>数据</dt>
              <dd>
                {uploadedDataset ? (
                  `使用上传文件 ${uploadedDataset}`
                ) : contract.dataset_csv ? (
                  `约 ${contract.dataset_rows ?? 0} 行数据`
                ) : (
                  <span className="contract-warning">
                    <AppIcon name="alert" size={13} aria-hidden="true" />
                    未检测到表格数据，将用合成示例演示分析流程
                  </span>
                )}
              </dd>
            </div>
          )}
          {contract.focus && (
            <div className="contract-row">
              <dt>关注点</dt>
              <dd>{contract.focus}</dd>
            </div>
          )}
          {contract.required_sections.length > 0 && (
            <div className="contract-row">
              <dt>必答章节</dt>
              <dd>
                <ol className="contract-sections">
                  {contract.required_sections.map((section) => (
                    <li key={section}>{section}</li>
                  ))}
                </ol>
              </dd>
            </div>
          )}
          <div className="contract-row">
            <dt>交付格式</dt>
            <dd className="contract-formats">
              {contract.deliverables.map((format) => (
                <span key={format} className="contract-tag">
                  {formatLabel(format)}
                </span>
              ))}
            </dd>
          </div>
          {contract.constraints.length > 0 && (
            <div className="contract-row">
              <dt>约束</dt>
              <dd>
                <ul className="contract-list contract-constraints">
                  {contract.constraints.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </dd>
            </div>
          )}
        </dl>
      )}
    </section>
  )
}
