/**
 * 知识单元列表 · 表格模板
 *
 * 从 knowledge-unit-list.js 拆出，只负责把列表数据渲染成表格 HTML。
 *
 * 列口径严格对齐 8.4 `GET /api/knowledge/units` 的响应字段：
 *   id / unit_code / title / category / file_type /
 *   permission_summary / creator_id / updated_at / status
 *
 * 按钮级权限（6.1）：knowledge:unit:update 控制「编辑」「权限」，
 * knowledge:unit:delete 控制「删除」。无权限时用 disabled + title 说明所需权限码，
 * 而不是直接隐藏 —— 用户需要知道按钮为什么点不了。
 */

import { esc, fmtDateTime, fmtNumber } from '../core/dom.js';
import { permissionTag } from './permission-dialog.js';

/**
 * 渲染知识单元表格。
 * @param {Array} items 后端返回的 items
 * @param {boolean} canUpdate 是否有 knowledge:unit:update
 * @param {boolean} canDelete 是否有 knowledge:unit:delete
 * @returns {string} HTML
 */
export function renderUnitTableHtml(items, canUpdate, canDelete) {
  return `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th style="width:36px"><input type="checkbox" data-role="check-all" /></th>
            <th>标题</th>
            <th>单元编号</th>
            <th>分类</th>
            <th>类型</th>
            <th>数据权限</th>
            <th>创建人</th>
            <th>更新时间</th>
            <th>状态</th>
            <th style="width:190px">操作</th>
          </tr>
        </thead>
        <tbody>
          ${items.map((unit) => renderRow(unit, canUpdate, canDelete)).join('')}
        </tbody>
      </table>
    </div>
  `;
}

/** 渲染单行 */
function renderRow(unit, canUpdate, canDelete) {
  return `
    <tr>
      <td><input type="checkbox" data-role="check-one" value="${esc(unit.id)}" /></td>
      <td>${esc(unit.title || '-')}</td>
      <td class="mono">${esc(unit.unit_code || '-')}</td>
      <td>${esc(unit.category || '-')}</td>
      <td>${unit.file_type ? `<span class="tag">${esc(unit.file_type)}</span>` : '-'}</td>
      <td>${permissionTag(unit.permission_summary)}</td>
      <td>${esc(unit.creator_id ?? '-')}</td>
      <td class="mute-sm">${esc(fmtDateTime(unit.updated_at))}</td>
      <td>${esc(unit.status || '-')}</td>
      <td class="actions">
        <button class="btn-link" type="button" data-act="detail" data-id="${esc(unit.id)}">查看</button>
        <button class="btn-link" type="button" data-act="edit" data-id="${esc(unit.id)}"
                ${canUpdate ? '' : 'disabled title="需要 knowledge:unit:update 权限"'}>编辑</button>
        <button class="btn-link" type="button" data-act="perm" data-id="${esc(unit.id)}"
                ${canUpdate ? '' : 'disabled title="需要 knowledge:unit:update 权限"'}>权限</button>
        <button class="btn-link" type="button" data-act="del" data-id="${esc(unit.id)}"
                ${canDelete ? '' : 'disabled title="需要 knowledge:unit:delete 权限"'}>删除</button>
      </td>
    </tr>
  `;
}

/**
 * 渲染分页条（8.1 分页约定：请求 page / page_size，响应 total / items）。
 * @param {number} page 当前页
 * @param {number} pageSize 每页条数
 * @param {number} total 总条数
 */
export function renderPagerHtml(page, pageSize, total) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return `
    <span class="info">第 ${fmtNumber(page)} / ${fmtNumber(pages)} 页 · 每页 ${fmtNumber(pageSize)} 条</span>
    <button class="btn btn-sm" type="button" data-page="prev" ${page <= 1 ? 'disabled' : ''}>上一页</button>
    <button class="btn btn-sm" type="button" data-page="next" ${page >= pages ? 'disabled' : ''}>下一页</button>
  `;
}
