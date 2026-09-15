/**
 * 模块二：组织架构管理
 * （对应 9.1 第二个模块 / 页面 2.9.3-2 组织架构与权限管理页）
 *
 * 本文件是三个 Tab 的路由入口与「角色管理」Tab 的实现，按职责拆成三份：
 *   org-module.js            本文件 —— Tab 容器 + 角色管理
 *   org-user-management.js   用户管理（表单较重）
 *   org-department.js        部门管理（递归树）
 *   org-role-permissions.js  角色权限树组件
 *
 * 角色管理接口落位：
 *   `GET /api/org/roles`                     角色列表（含已分配权限）
 *   `POST /api/org/roles/{id}/permissions`   权限分配，全量覆盖式保存
 *
 * **明确缺失、页面用占位说明的部分**：角色新增 / 编辑 / 删除接口（8 章未列出）。
 */

import { listRoles, setRolePermissions } from '../api/org.js';
import { hasPermission } from '../core/store.js';
import { navigate, currentPathname } from '../core/router.js';
import { el, esc, $, toast, openModal, pendingBlock, emptyState, loadingState, fmtNumber } from '../core/dom.js';
import { renderUserTab } from './org-user-management.js';
import { renderDepartmentTab } from './org-department.js';
import { renderPermissionTree, bindPermissionTree, collectPermissions } from './org-role-permissions.js';

/* ---------------------------------------------------------------- Tab 容器 */

const TABS = [
  { key: 'users', label: '用户管理', path: '/org/users' },
  { key: 'roles', label: '角色管理', path: '/org/roles' },
  { key: 'departments', label: '部门管理', path: '/org/departments' },
];

/**
 * 渲染带 Tab 的页面骨架，内容区由 loader 异步填充。
 * @param {string} activeKey 当前 Tab
 * @param {() => Promise<HTMLElement>} loader 内容加载函数
 */
function renderTabPage(activeKey, loader) {
  const container = el(`
    <div>
      <h2 class="page-title">组织架构与权限管理</h2>
      <p class="page-desc">用户 / 角色 / 部门三部分；角色权限分配为全量覆盖式保存</p>
      <div class="tabs">
        ${TABS.map(
          (tab) =>
            `<div class="tab ${tab.key === activeKey ? 'active' : ''}" data-path="${esc(tab.path)}">${esc(tab.label)}</div>`,
        ).join('')}
      </div>
      <div data-role="content">${loadingState()}</div>
    </div>
  `);

  // Tab 点击 → 换 hash，由路由重新渲染整页（保证地址栏与内容一致）
  $('.tabs', container).addEventListener('click', (event) => {
    const tab = event.target.closest('.tab');
    if (tab && tab.dataset.path !== currentPathname()) navigate(tab.dataset.path);
  });

  const content = $('[data-role="content"]', container);
  loader()
    .then((node) => content.replaceChildren(node))
    .catch((error) => {
      content.replaceChildren(
        el(`<div class="card"><div class="empty-state">加载失败：${esc(error.message)}</div></div>`),
      );
    });

  return container;
}

/* ---------------------------------------------------------------- 路由入口 */

export function renderUserManagement() {
  return renderTabPage('users', async () => renderUserTab());
}

export function renderRoleManagement() {
  return renderTabPage('roles', async () => renderRoleTab());
}

export function renderDepartmentManagement() {
  return renderTabPage('departments', async () => renderDepartmentTab());
}

/* ---------------------------------------------------------------- 角色管理 */

/** 角色管理 Tab */
async function renderRoleTab() {
  const roles = await listRoles();
  // 写操作需要 menu:org；页面上的保存按钮与后端声明的权限码保持一致
  const canWrite = hasPermission('menu:org');

  const container = el(`
    <div>
      ${
        canWrite
          ? ''
          : `<div class="card">${pendingBlock('角色权限保存', '当前账号缺少 menu:org 权限，仅可查看角色列表。', 'menu:org')}</div>`
      }

      <div class="card">
        <div class="card-title">
          <span>角色列表</span>
          <span class="mute-sm">共 ${fmtNumber((roles || []).length)} 个角色 · 数据来源 GET /api/org/roles</span>
        </div>
        <div data-role="role-list"></div>
      </div>

      <div class="card">
        <div class="card-title">角色增删改</div>
        ${pendingBlock(
          '角色新增 / 编辑 / 删除',
          '2.9.3 要求角色管理能力，但文档 8 章只列出了角色列表查询与权限分配两个接口，没有角色的新增、编辑、删除接口。此处不做实现，避免自造端点。',
          'POST/PUT/DELETE /api/org/roles（8 章未列出）',
        )}
      </div>
    </div>
  `);

  const listBox = $('[data-role="role-list"]', container);

  // 第 1 步：空态处理（列表不能留白）
  if (!roles || !roles.length) {
    listBox.innerHTML = emptyState('暂无角色数据');
    return container;
  }

  // 第 2 步：渲染表格。permissions 是 8.3 返回的 {permission_code, permission_type} 列表
  listBox.innerHTML = `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th style="width:64px">ID</th>
            <th>角色名称</th>
            <th>角色编码</th>
            <th>描述</th>
            <th>已有权限</th>
            <th style="width:120px">操作</th>
          </tr>
        </thead>
        <tbody>
          ${roles
            .map((role) => {
              const perms = role.permissions || [];
              const preview = perms
                .slice(0, 3)
                .map((p) => `<span class="tag mono">${esc(p.permission_code)}</span>`)
                .join(' ');
              const more = perms.length > 3 ? `<span class="mute-sm">等 ${fmtNumber(perms.length)} 项</span>` : '';
              return `
                <tr>
                  <td>${esc(role.id)}</td>
                  <td>${esc(role.role_name || '-')}</td>
                  <td class="mono">${esc(role.role_code || '-')}</td>
                  <td class="mute-sm">${esc(role.description || '-')}</td>
                  <td class="mute-sm">${perms.length ? `${preview} ${more}` : '<span class="mute-sm">未分配</span>'}</td>
                  <td class="actions">
                    <button class="btn btn-sm ${canWrite ? 'btn-primary' : ''}" type="button"
                            data-role-id="${esc(role.id)}" ${canWrite ? '' : 'disabled'}>配置权限</button>
                  </td>
                </tr>`;
            })
            .join('')}
        </tbody>
      </table>
    </div>
  `;

  // 第 3 步：配置权限 —— 打开权限树弹窗，保存后重跑当前路由刷新列表
  listBox.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-role-id]');
    if (!btn) return;
    openRolePermissionModal(roles.find((role) => String(role.id) === btn.dataset.roleId), () => {
      navigate('/org/roles');
    });
  });

  return container;
}

/** 角色权限配置弹窗 */
export function openRolePermissionModal(role, onSaved) {
  if (!role) return;
  openModal({
    title: `配置权限 · ${role.role_name || role.role_code}`,
    size: 'lg',
    bodyHtml: `
      <div class="form-error hidden" data-role="error"></div>
      <p class="page-desc">
        勾选后点击保存，将<strong>全量覆盖</strong>该角色的权限（8.3：先删后插语义）。
        保存后重新登录即可在动态菜单与按钮显隐上生效。
      </p>
      <div data-role="tree">${renderPermissionTree(role.permissions || [])}</div>
    `,
    okText: '保存权限',
    onMount: (body) => bindPermissionTree($('[data-role="tree"]', body)),
    onOk: async (body) => {
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');
      const permissions = collectPermissions($('[data-role="tree"]', body));

      // 允许提交空数组：这正是「收回该角色全部权限」的合法操作
      try {
        await setRolePermissions(role.id, permissions);
        toast(`已保存 ${permissions.length} 项权限`, 'success');
        if (onSaved) onSaved(role);
        return true;
      } catch (error) {
        errorBox.textContent = error.message || '保存失败';
        errorBox.classList.remove('hidden');
        return false;
      }
    },
  });
}
