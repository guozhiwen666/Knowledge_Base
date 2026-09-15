/**
 * 组织架构管理模块 · 部门管理 Tab
 *
 * 从 org-module.js 拆出，避免单文件过长。部门表单拆在 org-department-form.js。
 *
 * 接口落位（第 14 章 #5 补齐后已可读写）：
 *   `GET    /api/org/departments`                部门树（节点含 children）
 *   `POST   /api/org/departments`                新增部门
 *   `PUT    /api/org/departments/{id}`           编辑部门
 *   `DELETE /api/org/departments/{id}`           删除部门（有子部门或成员时 422）
 *   `GET    /api/org/departments/{id}/members`   按部门反查成员（5.2）
 *
 * 权限：写操作按 6.1 的 menu:org 控制；无权限时按钮 disabled + title 说明所需权限码。
 * 删除失败的 422 原因是「有子部门 / 还有成员」这类可操作信息，必须原样展示给用户。
 */

import { listDepartments, deleteDepartment, listDepartmentMembers } from '../api/org.js';
import { hasPermission } from '../core/store.js';
import { navigate } from '../core/router.js';
import { el, esc, $, toast, confirmDialog, emptyState, loadingState, fmtNumber } from '../core/dom.js';
import { loadOptions, invalidateOptions } from './org-options.js';
import { openDepartmentForm } from './org-department-form.js';

/** 渲染部门管理 Tab 内容 */
export async function renderDepartmentTab() {
  const tree = await listDepartments();
  const canWrite = hasPermission('menu:org');

  const container = el(`
    <div>
      <div class="card">
        <div class="card-title">
          <span>部门树</span>
          <div class="row">
            <span class="mute-sm">数据来源 GET /api/org/departments</span>
            <button class="btn btn-sm btn-primary" type="button" data-role="create-root"
                    ${canWrite ? '' : 'disabled title="需要 menu:org 权限"'}>新增顶级部门</button>
          </div>
        </div>
        <div class="row mb16">
          <button class="btn btn-sm" type="button" data-role="expand-all">全部展开</button>
          <button class="btn btn-sm" type="button" data-role="collapse-all">全部收起</button>
          <span class="mute-sm">点击部门名查看成员；行尾三个操作分别是新增子部门 / 编辑 / 删除</span>
        </div>
        <div class="form-error hidden" data-role="error"></div>
        <div class="tree" data-role="tree"></div>
      </div>

      <div class="card">
        <div class="card-title">
          <span>部门成员</span>
          <span class="mute-sm" data-role="member-title"></span>
        </div>
        <div data-role="members">${emptyState('点击上方任意部门节点，查看该部门的成员列表')}</div>
      </div>
    </div>
  `);

  const treeBox = $('[data-role="tree"]', container);
  const membersBox = $('[data-role="members"]', container);
  const memberTitle = $('[data-role="member-title"]', container);
  const errorBox = $('[data-role="error"]', container);

  /** 统一把后端 message 显示出来（422 的拒绝原因不能吞掉换成通用文案） */
  const showError = (message) => {
    errorBox.textContent = message;
    errorBox.classList.remove('hidden');
  };

  // 第 1 步：空态 —— 没有部门时不能留一片空白（9.4）
  if (!tree || !tree.length) {
    treeBox.innerHTML = emptyState('暂无部门数据（GET /api/org/departments 未返回内容）');
    return container;
  }

  // 第 2 步：建索引，便于按节点 id 取回节点（操作用，避免在 DOM 上挂整个对象）
  const nodeMap = new Map();
  const indexNodes = (nodes) => {
    (nodes || []).forEach((node) => {
      nodeMap.set(String(node.id), node);
      indexNodes(node.children);
    });
  };
  indexNodes(tree);

  // 第 3 步：递归渲染树。默认展开第一棵，避免深层部门一进来就铺满屏
  treeBox.innerHTML = tree.map((node, index) => renderDeptNode(node, index === 0, canWrite)).join('');

  // 第 4 步：成员列表（点击节点即拉取，5.2 的成员关系由 users.department_id 反查）
  const loadMembers = async (node) => {
    memberTitle.textContent = `部门「${node.name}」（ID ${fmtNumber(node.id)}）`;
    membersBox.innerHTML = loadingState();
    treeBox.querySelectorAll('.tree-row').forEach((row) => {
      row.classList.toggle('active', row.dataset.node === String(node.id));
    });
    try {
      const data = await listDepartmentMembers(node.id);
      const items = (data && data.items) || [];
      if (!items.length) {
        membersBox.innerHTML = emptyState('该部门暂无成员（成员关系由用户的 department_id 决定）');
        return;
      }
      membersBox.innerHTML = `
        <div class="mute-sm mb8">共 ${fmtNumber((data && data.total) || items.length)} 名成员</div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th style="width:70px">ID</th><th>登录名</th><th>显示名</th><th style="width:90px">状态</th></tr></thead>
            <tbody>
              ${items
                .map(
                  (user) => `
                <tr>
                  <td>${esc(user.id)}</td>
                  <td class="mono">${esc(user.username || '-')}</td>
                  <td>${esc(user.display_name || '-')}</td>
                  <td>${
                    Number(user.status) === 1
                      ? '<span class="tag tag-success">启用</span>'
                      : '<span class="tag tag-danger">停用</span>'
                  }</td>
                </tr>`,
                )
                .join('')}
            </tbody>
          </table>
        </div>
      `;
    } catch (error) {
      membersBox.innerHTML = `<div class="empty-state">加载失败：${esc(error.message)}</div>`;
    }
  };

  /** 部门变更后清选项缓存，再重跑当前路由刷新整棵树 */
  const refresh = () => {
    invalidateOptions();
    navigate('/org/departments');
  };

  // 第 5 步：树的交互 —— 折叠展开、行内操作、查看成员三件事共用一个委托
  treeBox.addEventListener('click', async (event) => {
    const row = event.target.closest('.tree-row');
    if (!row) return;
    const node = nodeMap.get(row.dataset.node);
    const btn = event.target.closest('[data-act]');

    // 5.1 行内操作（新增子部门 / 编辑 / 删除）
    if (btn) {
      if (!canWrite || btn.disabled || !node) return;
      errorBox.classList.add('hidden');

      if (btn.dataset.act === 'add-child') {
        const options = await loadOptions();
        openDepartmentForm({ mode: 'create', parentId: node.id, departments: options.departments, onDone: refresh });
        return;
      }

      if (btn.dataset.act === 'edit') {
        const options = await loadOptions();
        openDepartmentForm({ mode: 'edit', node, departments: options.departments, onDone: refresh });
        return;
      }

      if (btn.dataset.act === 'del') {
        const ok = await confirmDialog(
          `确认删除部门「${node.name}」？有子部门或仍有成员时后端会拒绝删除。`,
          '删除',
        );
        if (!ok) return;
        try {
          await deleteDepartment(node.id);
          toast('删除成功', 'success');
          refresh();
        } catch (error) {
          // 422：后端 message 说明是「有子部门」还是「还有成员」，原样展示
          showError(`删除部门「${node.name}」失败：${error.message || '未知原因'}`);
          toast(error.message || '删除失败', 'error');
        }
      }
      return;
    }

    // 5.2 点击行空白处 = 折叠 / 展开 + 查看成员
    const children = row.nextElementSibling;
    if (children && children.classList.contains('tree-children')) {
      children.classList.toggle('hidden');
      const caret = row.querySelector('.caret');
      if (caret) caret.textContent = children.classList.contains('hidden') ? '+' : '−';
    }
    if (node) loadMembers(node);
  });

  // 第 6 步：全部展开 / 收起
  $('[data-role="expand-all"]', container).addEventListener('click', () => {
    treeBox.querySelectorAll('.tree-children').forEach((node) => node.classList.remove('hidden'));
    treeBox.querySelectorAll('.caret').forEach((caret) => {
      if (!caret.classList.contains('leaf')) caret.textContent = '−';
    });
  });
  $('[data-role="collapse-all"]', container).addEventListener('click', () => {
    treeBox.querySelectorAll('.tree-children').forEach((node) => node.classList.add('hidden'));
    treeBox.querySelectorAll('.caret').forEach((caret) => {
      if (!caret.classList.contains('leaf')) caret.textContent = '+';
    });
  });

  // 第 7 步：新增顶级部门
  $('[data-role="create-root"]', container).addEventListener('click', async () => {
    if (!canWrite) return;
    const options = await loadOptions();
    openDepartmentForm({ mode: 'create', parentId: null, departments: options.departments, onDone: refresh });
  });

  // 默认选中第一个部门，让成员区一进来就有内容（9.4：空态不留白）
  loadMembers(tree[0]);
  return container;
}

/** 递归渲染一个部门节点（8.3 的节点字段 + 第 14 章 #5 的写操作入口） */
function renderDeptNode(node, expanded, canWrite) {
  const children = node.children || [];
  const hasChildren = children.length > 0;
  const leaderText =
    node.leader_id === null || node.leader_id === undefined
      ? '<span class="mute-sm">未设置负责人</span>'
      : `<span class="mute-sm">负责人 ID：${esc(node.leader_id)}</span>`;
  const permTip = canWrite ? '' : 'disabled title="需要 menu:org 权限"';

  return `
    <div class="tree-node">
      <div class="tree-row" data-node="${esc(node.id)}">
        <span class="caret ${hasChildren ? '' : 'leaf'}">${hasChildren ? (expanded ? '−' : '+') : ''}</span>
        <span>${esc(node.name || '-')}</span>
        <span class="tag mono">#${esc(node.id)}</span>
        ${leaderText}
        ${hasChildren ? `<span class="tree-count">${fmtNumber(children.length)} 个子部门</span>` : ''}
        <span style="margin-left:auto" class="row">
          <button class="btn-link" type="button" data-act="add-child" ${permTip}>新增子部门</button>
          <button class="btn-link" type="button" data-act="edit" ${permTip}>编辑</button>
          <button class="btn-link" type="button" data-act="del" ${permTip}>删除</button>
        </span>
      </div>
      ${
        hasChildren
          ? `<div class="tree-children ${expanded ? '' : 'hidden'}">
               ${children.map((child) => renderDeptNode(child, false, canWrite)).join('')}
             </div>`
          : ''
      }
    </div>
  `;
}
