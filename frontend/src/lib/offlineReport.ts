export function offlineReport(
  markdown: string,
  options: { runId?: string; version?: string; supportFailed?: boolean },
) {
  const metadata = {
    artifact: 'offline-body-only',
    run_id: options.runId ?? null,
    content_version: options.version ?? null,
    verification: options.supportFailed ? 'failed' : 'not_verified_in_this_copy',
    missing: [
      'evidence_appendix',
      'source_snapshots',
      'structured_tables_and_charts',
      'delivery_manifest',
      'file_hash_verification',
    ],
  }
  return `<!-- deep-research-offline ${JSON.stringify(metadata)} -->\n\n> 离线正文副本（降级导出）\n> 内容版本：${options.version ?? '未知；尚未形成服务端快照'}\n> 核验状态：${options.supportFailed ? '待核验草稿：正文结论依据尚未通过核验。' : '本副本未核验完整性，不能替代已验收交付物。'}\n> 缺失范围：证据附录、来源快照、结构化表格与图形、交付清单及文件哈希核验。\n\n${markdown}`
}
