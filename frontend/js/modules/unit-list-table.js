/**
 * 知识单元列表 · 表格模板
 *
 * 从 knowledge-unit-list.js 拆出，只负责把列表数据渲染成表格 HTML。
 *
 * 列口径严格对齐 8.4 `GET /api/knowledge/units` 的响应字段：
 *   id / unit_code / title / category / file_type /
 *   permission_summary / creator_id / updated_at / status
 *
 * 按钮级权限（6.1）：knowledge:unit:update 控制「编辑」「权限」「启用」，
 * knowledge:unit:delete 控制「删除」。无权限时用 disabled + title 说明所需权限码，
 * 而不是直接隐藏 —— 用户需要知道按钮为什么点不了。
 *
 * 分页条已提到 core/pager.js（用户列表与 FAQ 库共用），这里原样再导出，
 * 保持既有调用方（knowledge-unit-list.js）的导入路径不变。
 */

import { esc, fmtDateTime } from '../core/dom.js';
import { permissionTag } from './permission-dialog.js';

export { renderPagerHtml } from '../core/pager.js';

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
            <th style="width:230px">操作</th>
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
  // 状态流转接口（PUT /api/knowledge/units/{id}/status）目前只接受 active，
  // 因此只对「非 active」的行提供这一个方向的流转按钮
  const needActivate = String(unit.status || '') !== 'active';
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
        ${
          needActivate
            ? `<button class="btn-link" type="button" data-act="activate" data-id="${esc(unit.id)}"
                 ${canUpdate ? '' : 'disabled title="需要 knowledge:unit:update 权限"'}>置为 active</button>`
            : ''
        }
        <button class="btn-link" type="button" data-act="del" data-id="${esc(unit.id)}"
                ${canDelete ? '' : 'disabled title="需要 knowledge:unit:delete 权限"'}>删除</button>
      </td>
    </tr>
  `;
}
