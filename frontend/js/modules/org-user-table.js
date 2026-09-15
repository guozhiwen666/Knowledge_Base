/**
 * 用户管理 · 表格与分页模板
 *
 * 从 org-user-management.js 拆出（该文件保留筛选、取数、行内操作与弹窗编排）。
 *
 * 列口径严格对齐 `GET /api/org/users`（第 14 章 #5）的响应字段：
 *   id / username / display_name / department_id / department_name /
 *   status / role_ids / role_names / created_at
 *
 * 按钮级权限（6.1）：四个写操作都要 menu:org，无权限时 disabled + title 说明权限码。
 * 按钮不隐藏而是禁用 —— 用户需要知道按钮为什么点不了，否则会以为是页面坏了。
 */

import { esc, fmtDateTime } from '../core/dom.js';

/**
 * 渲染用户表格。
 * @param {Array} items 后端返回的 items
 * @param {boolean} canWrite 是否有 menu:org
 * @returns {string} HTML
 */
export function renderUserTableHtml(items, canWrite) {
  return `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th style="width:64px">ID</th>
            <th>登录名</th>
            <th>显示名</th>
            <th>所属部门</th>
            <th>角色</th>
            <th style="width:82px">状态</th>
            <th style="width:140px">创建时间</th>
            <th style="width:210px">操作</th>
          </tr>
        </thead>
        <tbody>${items.map((user) => renderRow(user, canWrite)).join('')}</tbody>
      </table>
    </div>
  `;
}

/** 渲染单行 */
function renderRow(user, canWrite) {
  const tip = canWrite ? '' : 'disabled title="需要 menu:org 权限"';
  const enabled = Number(user.status) === 1;
  const roleNames = user.role_names || [];

  return `
    <tr>
      <td>${esc(user.id)}</td>
      <td class="mono">${esc(user.username || '-')}</td>
      <td>${esc(user.display_name || '-')}</td>
      <td>${esc(user.department_name || '未归属部门')}</td>
      <td>
        ${
          roleNames.length
            ? roleNames.map((name) => `<span class="tag">${esc(name)}</span>`).join(' ')
            : '<span class="mute-sm">未分配角色</span>'
        }
      </td>
      <td>${
        enabled
          ? '<span class="tag tag-success">启用</span>'
          : '<span class="tag tag-danger">停用</span>'
      }</td>
      <td class="mute-sm">${esc(fmtDateTime(user.created_at))}</td>
      <td class="actions">
        <button class="btn-link" type="button" data-act="edit" data-id="${esc(user.id)}" ${tip}>编辑</button>
        <button class="btn-link" type="button" data-act="reset" data-id="${esc(user.id)}" ${tip}>重置口令</button>
        <button class="btn-link" type="button" data-act="toggle" data-id="${esc(user.id)}" ${tip}>
          ${enabled ? '停用' : '启用'}
        </button>
        <button class="btn-link" type="button" data-act="del" data-id="${esc(user.id)}" ${tip}>删除</button>
      </td>
    </tr>
  `;
}
