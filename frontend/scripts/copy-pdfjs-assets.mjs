// PDF.js 显示中文等 CID 字体需要 cMap 与标准字体数据。构建后复制到 dist/assets/pdfjs/，
// 后端已托管 /assets；开发期 PdfViewer 直接从 node_modules 读取。
import { cp } from 'node:fs/promises'

const source = new URL('../node_modules/pdfjs-dist/', import.meta.url)
const target = new URL('../dist/assets/pdfjs/', import.meta.url)
for (const folder of ['cmaps', 'standard_fonts']) {
  await cp(new URL(`${folder}/`, source), new URL(`${folder}/`, target), { recursive: true })
}
console.log('Copied PDF.js cMaps and standard fonts to dist/assets/pdfjs')
