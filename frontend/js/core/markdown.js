/**
 * 极简 Markdown → HTML（9.4 明确要求「不要引第三方库」）
 *
 * 支持范围刻意很小，只覆盖 AI 回答里真正会出现的几种结构：
 *   1. 标题（# ## ###）
 *   2. 代码块（``` 围栏）
 *   3. 无序列表（- / * / +）与有序列表（1.）
 *   4. 粗体（**x**）、行内代码（`x`）
 *   5. 换行（单个 \n 转 <br>）
 *
 * 安全前提：**先整体 HTML 转义，再做 Markdown 标记替换**。
 * 顺序不能反 —— 若先替换再转义，用户注入的 <script> 会被原样保留；
 * 先转义则所有尖括号都变成了 &lt;/&gt;，后续我们只生成自己认识的安全标签。
 */

/** HTML 转义（与 dom.js 保持一致，此处独立实现避免循环依赖） */
function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 行内元素：粗体 + 行内代码（顺序：先代码后粗体，避免 `` 内内容被粗体规则吞掉） */
function renderInline(escapedLine) {
  return escapedLine
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

/**
 * 把 Markdown 文本转成 HTML 字符串。
 * @param {string} markdown 原始文本（流式渲染时传当前已累积的全部文本）
 * @returns {string} 安全 HTML
 */
export function renderMarkdown(markdown) {
  if (!markdown) return '';

  // 第 1 步：整体转义 —— 这一步之后输入里的 HTML 已全部失效
  const escaped = escapeHtml(markdown);

  // 第 2 步：按行扫描，代码块需要跨行状态，因此不能用纯正则一把梭
  const lines = escaped.split('\n');
  const out = [];
  let inCode = false;
  let listType = ''; // 'ul' | 'ol' | ''

  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = '';
    }
  };

  for (const line of lines) {
    // 第 3 步：代码块围栏切换
    if (line.trim().startsWith('```')) {
      closeList();
      if (inCode) {
        out.push('</code></pre>');
        inCode = false;
      } else {
        out.push('<pre><code>');
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      // 代码块内容原样保留（已转义），不做任何行内替换
      out.push(`${line}\n`);
      continue;
    }

    // 第 4 步：标题（### 起，最多支持到三级）
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      continue;
    }

    // 第 5 步：列表项（无序 / 有序）
    const unordered = /^\s*[-*+]\s+(.*)$/.exec(line);
    const ordered = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (unordered || ordered) {
      const want = unordered ? 'ul' : 'ol';
      if (listType !== want) {
        closeList();
        out.push(`<${want}>`);
        listType = want;
      }
      out.push(`<li>${renderInline((unordered || ordered)[1])}</li>`);
      continue;
    }

    // 第 6 步：普通行 —— 列表要收尾，空行只产出换行
    closeList();
    if (line.trim() === '') {
      out.push('<br>');
    } else {
      out.push(`<p>${renderInline(line)}</p>`);
    }
  }

  // 第 7 步：收尾未闭合的代码块 / 列表（流式渲染时文本可能被截断在半截）
  if (inCode) out.push('</code></pre>');
  closeList();

  return out.join('');
}
