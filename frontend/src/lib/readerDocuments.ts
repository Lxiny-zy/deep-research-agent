import type { ReaderDocument } from '../types'

/** 证据来自哪份论文：上传文件按附件编号对应，论文链接按网址对应。 */
export function documentForEvidence(
  url: string,
  documents: ReaderDocument[],
): ReaderDocument | undefined {
  const attachment = /\/attachments\/([0-9a-f]{24})/.exec(url)
  if (attachment) return documents.find((item) => item.id === `att-${attachment[1]}`)
  const base = url.split('#')[0]
  return documents.find((item) => item.url && base.startsWith(item.url))
}
