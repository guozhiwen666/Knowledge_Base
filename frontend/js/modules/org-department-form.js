/**
 * 部门管理 · 部门表单弹窗
 *
 * 从 org-department.js 拆出（该文件保留部门树、节点操作与成员列表）。
 *
 * 接口（第 14 章 #5 补齐）：
 *   `POST /api/org/departments`       新增（name 必填，parent_id / leader_id / sort_order 可选）
 *   `PUT  /api/org/departments/{id}`  编辑（字段全可选，未传即不改）
 *
 * 两处 422 要如实显示后端 message，不做通用文案替换：
 *   1. 父部门不存在（新增 / 编辑都会）；
 *   2. 把部门挂到自己或自己的下级 —— 后端拒绝，避免形成环。
 */

import { createDepartment, updateDepartment } from '../api/org.js';
import { esc, $, toast, openModal, fmtNumber } from '../core/dom.js';
import { loadUserOptions } from './org-options.js';

/**
 * 打开部门表单弹窗。
 *
 * @param {object} opts
 *   mode        'create' | 'edit'
 *   node        编辑时的部门节点 { id, name, parent_id, leader_id, sort_order }
 *   parentId    新增时的上级部门 id；null 表示建顶级部门
 *   departments 已拍平的部门选项（来自 loadOptions，name 带缩进）
 *   onDone      保存成功回调
 */
export async function openDepartmentForm({ mode, node, parentId = null, departments = [], onDone }) {
  // 第 1 步：负责人下拉需要一个用户列表。取不到就降级为「暂无人员可选」，不阻塞表单
  const users = await loadUserOptions();

  const deptOptions = departments
    .map((dept) => `<option value="${esc(dept.id)}">${esc(dept.name)}</option>`)
    .join('');

  // 第 2 步：编辑时按节点回填；新增子部门时上级已定（可在下拉里改）
  const current = node || {};
  const currentParentId = mode === 'edit' ? current.parent_id : parentId;
  const userOptions = users
    .map(
      (user) =>
        `<option value="${esc(user.id)}">${esc(user.display_name || user.username)}（${esc(user.username)}）</option>`,
    )
    .join('');

  const userSelect = users.length
    ? `<select class="select" data-role="leader">
         <option value="">（不设置负责人）</option>
         ${userOptions}
       </select>`
    : `<input class="input" data-role="leader" type="number" min="1"
              placeholder="暂无人员可选，可直接填写用户 ID（GET /api/org/users 无数据）" />`;

  openModal({
    title: mode === 'edit' ? `编辑部门 · ${current.name || ''}` : '新增部门',
    bodyHtml: `
      <div class="form-error hidden" data-role="error"></div>

      <div class="field">
        <label>部门名称<span class="req">*</span></label>
        <input class="input" data-role="name" type="text" maxlength="128"
               value="${esc(current.name || '')}" placeholder="最长 128 字" />
      </div>

      <div class="field">
        <label>上级部门</label>
        <select class="select" data-role="parent">
          <option value="">（顶级部门）</option>
          ${deptOptions}
        </select>
        <div class="field-hint">
          把自己挂到自己或自己的下级会被后端拒绝（422），界面会原样显示拒绝原因。
        </div>
      </div>

      <div class="field">
        <label>负责人</label>
        ${userSelect}
        <div class="field-hint">对应 <code>leader_id</code>；只做记录，不参与数据权限判定（6.2）。</div>
      </div>

      <div class="field">
        <label>排序</label>
        <input class="input" data-role="sort" type="number" value="${esc(current.sort_order ?? 0)}" />
        <div class="field-hint">同级排序值，越小越靠前（部门树的默认序）。</div>
      </div>

      ${
        mode === 'edit'
          ? `<div class="field-hint">当前部门 ID：<span class="mono">${esc(current.id)}</span>，
               成员数不会因改名或换上级而丢失（6.4：成员关系挂在 users.department_id 上）。</div>`
          : ''
      }
    `,
    okText: '保存',
    onMount: (body) => {
      // 第 3 步：回填上级与负责人（放在挂载后，选项已就位）
      const parentBox = $('[data-role="parent"]', body);
      if (currentParentId !== null && currentParentId !== undefined) {
        parentBox.value = String(currentParentId);
      }
      const leaderBox = $('[data-role="leader"]', body);
      if (leaderBox && current.leader_id !== null && current.leader_id !== undefined) {
        leaderBox.value = String(current.leader_id);
      }
    },
    onOk: async (body) => {
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');

      // 第 4 步：前端只拦「名称非空」这一条，其余交给后端裁定
      const name = $('[data-role="name"]', body).value.trim();
      if (!name) {
        errorBox.textContent = '部门名称不能为空';
        errorBox.classList.remove('hidden');
        return false;
      }

      // 第 5 步：组装载荷。空串的上级 / 负责人按 null 提交（表示顶级 / 不设置）
      const parentRaw = $('[data-role="parent"]', body).value;
      const leaderRaw = $('[data-role="leader"]', body).value.trim();
      const sortRaw = $('[data-role="sort"]', body).value.trim();
      const payload = {
        name,
        parent_id: parentRaw === '' ? null : Number(parentRaw),
        leader_id: leaderRaw === '' ? null : Number(leaderRaw),
        sort_order: sortRaw === '' ? 0 : Number(sortRaw),
      };

      try {
        if (mode === 'edit') {
          await updateDepartment(current.id, payload);
          toast(`部门「${name}」已更新`, 'success');
        } else {
          const created = await createDepartment(payload);
          toast(`部门「${name}」已创建（ID ${fmtNumber((created && created.id) || 0)}）`, 'success');
        }
        if (onDone) onDone();
        return true;
      } catch (error) {
        // 422（父部门不存在 / 成环 / 名称空）原样展示后端 message
        errorBox.textContent = error.message || '保存失败';
        errorBox.classList.remove('hidden');
        return false;
      }
    },
  });
}
