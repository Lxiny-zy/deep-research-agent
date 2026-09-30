import { AppIcon } from './AppIcon'
import { formatLabel } from '../lib/workbench'
import type { DatasetSheetProfile, TaskContract, TaskTemplate } from '../types'

interface Props {
  template: TaskTemplate
  contract: TaskContract | undefined
  loading: boolean
  error: unknown
  /** 上传的数据文件；pending 表示多工作表文件尚未选定 */
  uploadedDataset?: {
    filename: string
    sheet: DatasetSheetProfile | null
    pending: boolean
  } | null
  /** 用户明确选择用示例数据演示 */
  demoData?: boolean
  /** 已解析完成、会随任务提交的附件数 */
  attachmentCount?: number
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
  demoData = false,
  attachmentCount = 0,
}: Props) {
  const needsPaper = template.input_kind === 'paper'
  const needsData = template.input_kind === 'dataset'
  return (
    <section className="home-card contract" aria-live="polite" aria-busy={loading}>
      <div className="home-card-head">
        <span className="home-card-title">
          <AppIcon name="scan-search" size={15} aria-hidden="true" />
          任务确认
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
                ) : attachmentCount > 0 ? (
                  `使用上传的 ${attachmentCount} 个文件，不做开放检索`
                ) : contract.pasted_paper_chars ? (
                  `使用粘贴的论文文本（约 ${contract.pasted_paper_chars} 字），不做开放检索`
                ) : (
                  <span className="contract-warning">
                    <AppIcon name="alert" size={13} aria-hidden="true" />
                    还没有论文：请粘贴 arXiv / DOI / 论文链接或论文文本，或上传论文文件
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
                  uploadedDataset.pending ? (
                    <span className="contract-warning">
                      <AppIcon name="alert" size={13} aria-hidden="true" />
                      {`${uploadedDataset.filename} 有多张工作表，请先选择要分析的一张`}
                    </span>
                  ) : (
                    `使用上传文件 ${uploadedDataset.filename}${
                      uploadedDataset.sheet?.name
                        ? `（工作表「${uploadedDataset.sheet.name}」）`
                        : ''
                    }${
                      uploadedDataset.sheet
                        ? `，${uploadedDataset.sheet.rows} 行 × ${uploadedDataset.sheet.columns.length} 列，完整使用不截断`
                        : ''
                    }`
                  )
                ) : contract.dataset_error ? (
                  <span className="contract-warning">
                    <AppIcon name="alert" size={13} aria-hidden="true" />
                    {contract.dataset_error}
                  </span>
                ) : contract.dataset_profile ? (
                  `粘贴的数据：${contract.dataset_profile.rows} 行 × ${contract.dataset_profile.columns.length} 列，完整使用不截断`
                ) : demoData ? (
                  '暂无数据，将用示例数据演示分析流程（结论不代表真实实验）'
                ) : (
                  <span className="contract-warning">
                    <AppIcon name="alert" size={13} aria-hidden="true" />
                    还没有数据：请上传 CSV / TSV / XLSX 或粘贴表格；也可勾选示例数据演示
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
