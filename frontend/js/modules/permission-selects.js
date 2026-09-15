/**
 * 数据权限配置 · 四组实体的多选组件
 *
 * 从 permission-dialog.js 拆出（该文件保留弹窗编排与提交逻辑）。
 * 四组实体（全局 / 部门 / 角色 / 人员）的可选列表各有形状：
 *   部门是树（带缩进）、角色与人员是平铺勾选、全局是一个开关（在弹窗里直接写死）。
 * 集中在这里，是为了让「勾选框的 name 与取值口径」只有一处定义 ——
 * 提交逻辑按 name 收集，改口径时不会两边不一致。
 *
 * 人员组的取值来自 `GET /api/org/users`（第 14 章 #5）；该接口要求 menu:org，
 * 拉不到时退化为只读展示已配置的人员实体（保存时按原值保留，见弹窗的提交逻辑）。
 */

import { esc, fmtNumber, $ } from '../core/dom.js';

/**
 * 渲染部门多选树（递归，带 checkbox）。
 * @param {Array} nodes 部门树
 * @param {Set<string>} checkedIds 已选部门 id 集合（字符串）
 */
export function renderDeptTree(nodes, checkedIds) {
  if (!nodes || !nodes.length) {
    return '<div class="mute-sm">暂无部门数据（GET /api/org/departments 未返回内容）</div>';
  }
  const render = (list, depth) =>
    list
      .map(
        (node) => `
      <div>
        <label class="check-item" style="padding-left:${depth * 18}px">
          <input type="checkbox" name="dept" value="${esc(node.id)}" ${checkedIds.has(String(node.id)) ? 'checked' : ''} />
          <span>${esc(node.name || '-')}<span class="mute-sm mono"> #${esc(node.id)}</span></span>
        </label>
        ${node.children && node.children.length ? render(node.children, depth + 1) : ''}
      </div>`,
      )
      .join('');
  return render(nodes, 0);
}

/**
 * 渲染角色多选（平铺）。
 * @param {Array} roles 角色列表
 * @param {Set<string>} checkedIds 已选角色 id 集合（字符串）
 */
export function renderRoleList(roles, checkedIds) {
  if (!roles || !roles.length) {
    return '<div class="mute-sm">暂无角色数据（GET /api/org/roles 未返回内容）</div>';
  }
  return `<div class="check-grid">${roles
    .map(
      (role) => `
    <label class="check-item">
      <input type="checkbox" name="role" value="${esc(role.id)}" ${checkedIds.has(String(role.id)) ? 'checked' : ''} />
      <span>${esc(role.role_name || role.role_code)}<span class="mute-sm mono"> #${esc(role.id)}</span></span>
    </label>`,
    )
    .join('')}</div>`;
}

/**
 * 渲染人员多选（平铺）。
 * @param {Array} users 用户列表（GET /api/org/users 的 items）
 * @param {Array<number>} assignedUserIds 已配置的人员实体 id
 * @param {boolean} usersLoaded 用户列表是否成功取到（false 表示缺 menu:org）
 */
export function renderUserList(users, assignedUserIds, usersLoaded) {
  // 取不到列表时如实说明原因，并告诉管理员「已配置的项不会丢」
  if (!usersLoaded) {
    const ids = assignedUserIds.map((id) => esc(id)).join('、');
    return `
      <div class="field-hint mb8">
        无法列出可选人员：<code>GET /api/org/users</code> 要求 <code>menu:org</code> 权限，当前账号没有该权限。
      </div>
      ${
        assignedUserIds.length
          ? `<div class="mute-sm">库内已配置 ${fmtNumber(assignedUserIds.length)} 个人员实体（目标 ID：${ids}），保存时按原值保留。</div>`
          : '<div class="mute-sm">该单元当前也没有配置人员实体。</div>'
      }
    `;
  }

  if (!users.length) {
    return '<div class="mute-sm">暂无用户数据（GET /api/org/users 未返回内容）</div>';
  }

  const checked = new Set(assignedUserIds.map(String));
  return `<div class="check-grid">${users
    .map(
      (user) => `
    <label class="check-item">
      <input type="checkbox" name="user" value="${esc(user.id)}" ${checked.has(String(user.id)) ? 'checked' : ''} />
      <span>${esc(user.display_name || user.username)}<span class="mute-sm mono"> #${esc(user.id)}</span></span>
    </label>`,
    )
    .join('')}</div>`;
}

/** 刷新部门 / 角色 / 人员的已选计数 */
export function refreshCounts(body) {
  const count = (name) => body.querySelectorAll(`input[name="${name}"]:checked`).length;
  const boxes = {
    dept: $('[data-role="dept-count"]', body),
    role: $('[data-role="role-count"]', body),
    user: $('[data-role="user-count"]', body),
  };
  const units = { dept: '个部门', role: '个角色', user: '个人员' };
  Object.keys(boxes).forEach((key) => {
    if (!boxes[key]) return;
    const value = count(key);
    boxes[key].textContent = value ? `已选 ${value} ${units[key]}` : '未选择';
  });
}
