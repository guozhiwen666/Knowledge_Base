/**
 * 通用分页条模板（8.1 分页约定：请求 page / page_size，响应 total / items）
 *
 * 从 modules/unit-list-table.js 提到 core，供知识单元列表、用户列表、FAQ 库
 * 三处共用 —— 三处的分页交互完全一致（上一页 / 下一页），各写一份只会让
 * 「每页条数」「页码口径」有机会长得不一样。
 *
 * 约定：按钮带 `data-page="prev|next"`，由页面的容器用事件委托绑定。
 */

import { fmtNumber } from './dom.js';

/**
 * 渲染分页条。
 * @param {number} page 当前页
 * @param {number} pageSize 每页条数
 * @param {number} total 总条数
 * @param {string} [unit] 计数单位，默认「条」
 * @returns {string} HTML
 */
export function renderPagerHtml(page, pageSize, total, unit = '条') {
  const pages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  return `
    <span class="info">第 ${fmtNumber(page)} / ${fmtNumber(pages)} 页 · 每页 ${fmtNumber(pageSize)} ${unit} · 共 ${fmtNumber(total)} ${unit}</span>
    <button class="btn btn-sm" type="button" data-page="prev" ${page <= 1 ? 'disabled' : ''}>上一页</button>
    <button class="btn btn-sm" type="button" data-page="next" ${page >= pages ? 'disabled' : ''}>下一页</button>
  `;
}
