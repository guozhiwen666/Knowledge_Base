/**
 * 模块二：组织架构管理
 * （对应 9.1 第二个模块 / 页面 2.9.3-2 组织架构与权限管理页）
 *
 * 本文件是三个 Tab 的路由入口与「角色管理」Tab 的实现，按职责拆成多份：
 *   org-module.js            本文件 —— Tab 容器 + 角色管理（列表 / 增删改入口）
 *   org-role-form.js         角色表单弹窗
 *   org-user-management.js   用户管理（列表 + 行内操作）
 *   org-user-form.js         用户表单模板与取值
 *   org-user-table.js        用户表格与分页模板
 *   org-department.js        部门管理（递归树 + 成员）
 *   org-department-form.js   部门表单弹窗
 *   org-role-permissions.js  角色权限树组件
 *   org-options.js           部门 / 角色 / 用户下拉选项
 *
 * 角色管理接口落位：
 *   `GET    /api/org/roles`                  角色列表（含已分配权限）
 *   `POST   /api/org/roles`                  角色新增（第 14 章 #5）
 *   `PUT    /api/org/roles/{id}`             角色编辑（第 14 章 #5）
 *   `DELETE /api/org/roles/{id}`             角色删除（第 14 章 #5，仍被使用时 422）
 *   `POST   /api/org/roles/{id}/permissions` 权限分配，全量覆盖式保存
 */

import { listRoles, deleteRole, setRolePermissions } from '../api/org.js';
import { hasPermission } from '../core/store.js';
import { navigate, currentPathname } from '../core/router.js';
import { el, esc, $, toast, confirmDialog, openModal, emptyState, loadingState, fmtNumber } from '../core/dom.js';
import { renderUserTab } from './org-user-management.js';
import { renderDepartmentTab } from './org-department.js';
import { renderPermissionTree, bindPermissionTree, collectPermissions } from './org-role-permissions.js';
import { openRoleForm } from './org-role-form.js';
import { invalidateOptions } from './org-options.js';

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
      <div class="form-error hidden" data-role="error"></div>

      <div class="card">
        <div class="card-title">
          <span>角色列表</span>
          <div class="row">
            <span class="mute-sm">共 ${fmtNumber((roles || []).length)} 个角色 · 数据来源 GET /api/org/roles</span>
            <button class="btn btn-sm btn-primary" type="button" data-role="create-role"
                    ${canWrite ? '' : 'disabled title="需要 menu:org 权限"'}>新增角色</button>
          </div>
        </div>
        <div data-role="role-list"></div>
        <div class="field-hint mt8">
          删除角色时若仍有用户使用，后端会返回 422 并说明原因，页面会原样展示该原因。
        </div>
      </div>

      <div class="card">
        <div class="card-title">角色权限分配说明</div>
        <div class="field-hint">
          权限分配是<strong>全量覆盖</strong>式保存（8.3：先删后插语义）：弹窗里勾了什么，
          保存后该角色就拥有什么。保存后重新登录即可在动态菜单与按钮显隐上生效。
        </div>
      </div>
    </div>
  `);

  const listBox = $('[data-role="role-list"]', container);
  const errorBox = $('[data-role="error"]', container);

  // 第 1 步：新增角色（成功后清选项缓存并重跑当前路由刷新列表）
  $('[data-role="create-role"]', container).addEventListener('click', () => {
    if (!canWrite) return;
    openRoleForm({
      mode: 'create',
      onDone: () => {
        invalidateOptions();
        navigate('/org/roles');
      },
    });
  });

  // 第 2 步：空态处理（列表不能留白）
  if (!roles || !roles.length) {
    listBox.innerHTML = emptyState('暂无角色数据（点击右上角「新增角色」创建第一个角色）');
    return container;
  }

  // 第 3 步：渲染表格。permissions 是 8.3 返回的 {permission_code, permission_type} 列表
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
            <th style="width:210px">操作</th>
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
                    <button class="btn-link" type="button" data-act="perm" data-role-id="${esc(role.id)}"
                            ${canWrite ? '' : 'disabled title="需要 menu:org 权限"'}>配置权限</button>
                    <button class="btn-link" type="button" data-act="edit" data-role-id="${esc(role.id)}"
                            ${canWrite ? '' : 'disabled title="需要 menu:org 权限"'}>编辑</button>
                    <button class="btn-link" type="button" data-act="del" data-role-id="${esc(role.id)}"
                            ${canWrite ? '' : 'disabled title="需要 menu:org 权限"'}>删除</button>
                  </td>
                </tr>`;
            })
            .join('')}
        </tbody>
      </table>
    </div>
  `;

  // 第 4 步：行内操作 —— 配置权限 / 编辑 / 删除
  listBox.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-act]');
    if (!btn || btn.disabled) return;
    const role = roles.find((item) => String(item.id) === btn.dataset.roleId);
    if (!role) return;
    errorBox.classList.add('hidden');

    if (btn.dataset.act === 'perm') {
      // 保存后重跑当前路由刷新列表（角色权限影响菜单显隐，重新登录才全站生效）
      openRolePermissionModal(role, () => navigate('/org/roles'));
      return;
    }

    if (btn.dataset.act === 'edit') {
      // 角色名/编码会影响别处的下拉选项，改完清缓存
      openRoleForm({
        mode: 'edit',
        role,
        onDone: () => {
          invalidateOptions();
          navigate('/org/roles');
        },
      });
      return;
    }

    if (btn.dataset.act === 'del') {
      const ok = await confirmDialog(
        `确认删除角色「${role.role_name || role.role_code}」？仍被用户使用的角色无法删除。`,
        '删除',
      );
      if (!ok) return;
      try {
        await deleteRole(role.id);
        // 角色被删掉后，用户表单里的角色选项与权限弹窗的选项都要重新拉取
        invalidateOptions();
        toast('删除成功', 'success');
        navigate('/org/roles');
      } catch (error) {
        // 422：后端 message 说明「仍有多少用户使用该角色」，原样展示
        errorBox.textContent = `删除角色「${role.role_name || role.role_code}」失败：${error.message || '未知原因'}`;
        errorBox.classList.remove('hidden');
        toast(error.message || '删除失败', 'error');
      }
    }
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
