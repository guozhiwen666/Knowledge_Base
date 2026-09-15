/**
 * 组织架构管理模块 · 部门管理 Tab
 *
 * 从 org-module.js 拆出，避免单文件过长。
 *
 * 数据来源：`GET /api/org/departments`（8.3），返回嵌套 children 的部门树，
 * 节点字段为 id / parent_id / name / leader_id / sort_order。
 *
 * **明确缺失、页面用占位说明的部分**：
 *   部门的新增 / 编辑 / 删除接口，以及「按部门查成员」接口都不在 8 章。
 *   5.2 说明成员由 users.department_id 反查，但对外没有对应查询接口，
 *   因此本页只做只读树展示，负责人来自节点自带的 leader_id。
 */

import { listDepartments } from '../api/org.js';
import { el, esc, $, pendingBlock, emptyState, fmtNumber } from '../core/dom.js';

/** 渲染部门管理 Tab 内容 */
export async function renderDepartmentTab() {
  const tree = await listDepartments();

  const container = el(`
    <div>
      <div class="card">
        <div class="card-title">
          <span>部门树</span>
          <span class="mute-sm">数据来源 GET /api/org/departments</span>
        </div>
        <div class="row mb16">
          <button class="btn btn-sm" type="button" data-role="expand-all">全部展开</button>
          <button class="btn btn-sm" type="button" data-role="collapse-all">全部收起</button>
        </div>
        <div class="tree" data-role="tree"></div>
      </div>

      <div class="card">
        <div class="card-title">部门增删改与成员关联</div>
        ${pendingBlock(
          '部门新增 / 编辑 / 删除 与成员列表',
          '5.2 明确成员由 users.department_id 反查，而 8 章既没有部门写接口，也没有按部门查成员的接口。因此本页只做只读树展示：节点上的负责人来自部门树自带的 leader_id，成员明细无法获取。',
          '部门写接口、按部门查成员接口（8 章未列出）',
        )}
      </div>
    </div>
  `);

  const treeBox = $('[data-role="tree"]', container);

  // 第 1 步：空态 —— 没有部门时不能留一片空白（9.4）
  if (!tree || !tree.length) {
    treeBox.innerHTML = emptyState('暂无部门数据');
    return container;
  }

  // 第 2 步：递归渲染树。默认展开第一棵，避免深层部门一进来就铺满屏
  treeBox.innerHTML = tree.map((node, index) => renderDeptNode(node, index === 0)).join('');

  // 第 3 步：节点折叠 / 展开
  treeBox.addEventListener('click', (event) => {
    const row = event.target.closest('.tree-row');
    if (!row) return;
    const children = row.nextElementSibling;
    if (!children || !children.classList.contains('tree-children')) return;
    children.classList.toggle('hidden');
    const caret = row.querySelector('.caret');
    if (caret) caret.textContent = children.classList.contains('hidden') ? '+' : '−';
  });

  // 第 4 步：全部展开 / 收起
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

  return container;
}

/** 递归渲染一个部门节点（8.3 的节点字段） */
function renderDeptNode(node, expanded) {
  const children = node.children || [];
  const hasChildren = children.length > 0;
  const leaderText =
    node.leader_id === null || node.leader_id === undefined
      ? '<span class="mute-sm">未设置负责人</span>'
      : `<span class="mute-sm">负责人 ID：${esc(node.leader_id)}</span>`;

  return `
    <div class="tree-node">
      <div class="tree-row">
        <span class="caret ${hasChildren ? '' : 'leaf'}">${hasChildren ? (expanded ? '−' : '+') : ''}</span>
        <span>${esc(node.name || '-')}</span>
        <span class="tag mono">#${esc(node.id)}</span>
        ${leaderText}
        ${hasChildren ? `<span class="tree-count">${fmtNumber(children.length)} 个子部门</span>` : ''}
      </div>
      ${
        hasChildren
          ? `<div class="tree-children ${expanded ? '' : 'hidden'}">
               ${children.map((child) => renderDeptNode(child, false)).join('')}
             </div>`
          : ''
      }
    </div>
  `;
}
