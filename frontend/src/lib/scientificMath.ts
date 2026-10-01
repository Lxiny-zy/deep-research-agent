import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'

/** Preserve code while accepting the bracket delimiters commonly returned by models. */
export function normalizeMathMarkdown(source: string): string {
  let output = ''
  for (let i = 0; i < source.length; ) {
    if (i === 0 || source[i - 1] === '\n') {
      const fence = /^( {0,3})(`{3,}|~{3,})[^\n]*\n/.exec(source.slice(i))
      if (fence) {
        const closing = new RegExp(`^ {0,3}${fence[2][0]}{${fence[2].length},}[^\\S\\n]*$`, 'm')
        const rest = source.slice(i + fence[0].length)
        const match = closing.exec(rest)
        const end = match ? i + fence[0].length + match.index + match[0].length : source.length
        output += source.slice(i, end)
        i = end
        continue
      }
    }
    if (source[i] === '`') {
      const marker = /^`+/.exec(source.slice(i))![0]
      const end = source.indexOf(marker, i + marker.length)
      if (end >= 0) {
        output += source.slice(i, end + marker.length)
        i = end + marker.length
        continue
      }
    }
    if (source.startsWith('\\\\', i)) {
      output += '\\\\'
      i += 2
      continue
    }
    if (source[i] === '$' && source[i - 1] !== '\\') {
      const marker = source[i + 1] === '$' ? '$$' : '$'
      let end = source.indexOf(marker, i + marker.length)
      while (end >= 0 && source[end - 1] === '\\') end = source.indexOf(marker, end + marker.length)
      const candidate = end >= 0 ? source.slice(i + marker.length, end) : ''
      const valid =
        end >= 0 &&
        !candidate.includes('`') &&
        (marker === '$$' || !/^\d/.test(source.slice(end + 1)))
      if (valid) {
        output += source.slice(i, end + marker.length)
        i = end + marker.length
        continue
      }
      if (marker === '$' && /^\d/.test(source.slice(i + 1))) {
        output += '\\$'
        i += 1
        continue
      }
    }
    const opening = source.slice(i, i + 2)
    if (opening === '\\(' || opening === '\\[') {
      const closing = opening === '\\(' ? '\\)' : '\\]'
      let end = source.indexOf(closing, i + 2)
      while (end >= 0) {
        let back = end - 1
        while (back >= 0 && source[back] === '\\') back -= 1
        if ((end - back - 1) % 2 === 0) break
        end = source.indexOf(closing, end + 2)
      }
      if (end >= 0) {
        const marker = opening === '\\(' ? '$' : '$$'
        output += marker + source.slice(i + 2, end) + marker
        i = end + 2
        continue
      }
    }
    output += source[i++]
  }
  return output
}

type MathNode = {
  type: string
  value?: string
  lang?: string
  children?: MathNode[]
  position?: { start: { offset?: number }; end: { offset?: number } }
  data?: { hName?: string; hProperties?: { className?: string[] }; hChildren?: unknown[] }
}

function remarkMathCompatibility() {
  return (tree: unknown, file: { value: unknown }) => {
    const source = String(file.value)
    function visit(node: MathNode) {
      const start = node.position?.start.offset ?? 0
      const end = node.position?.end.offset ?? 0
      const original = source.slice(start, end)
      // A pair of prices is not a mathematical expression. Restore source
      // text when remark-math consumed the dollar before the second price.
      if (node.type === 'inlineMath' && /^\$\d/.test(original) && /^\d/.test(source.slice(end))) {
        node.type = 'text'
        node.value = original
        delete node.data
      } else if (node.type === 'inlineMath' && original.startsWith('$$')) {
        if (node.data?.hProperties)
          node.data.hProperties.className = ['language-math', 'math-display']
      } else if (
        node.type === 'code' &&
        ['math', 'latex', 'tex'].includes(node.lang ?? '') &&
        !/\\documentclass|\\begin\{document\}/.test(node.value ?? '')
      ) {
        node.data = {
          hName: 'code',
          hProperties: { className: ['language-math', 'math-display'] },
          hChildren: [{ type: 'text', value: node.value ?? '' }],
        }
      }
      node.children?.forEach(visit)
    }
    visit(tree as MathNode)
  }
}

export const mathRemarkPlugins = [remarkMath, remarkMathCompatibility]
export const mathRehypePlugins: [
  typeof rehypeKatex,
  { strict: false; trust: false; throwOnError: false; maxExpand: number },
][] = [[rehypeKatex, { strict: false, trust: false, throwOnError: false, maxExpand: 500 }]]
