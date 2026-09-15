/**
 * 模块四：数据权限配置组件（对应 9.1 第四个模块）
 *
 * 需求（9.4）：全局（开关）、部门（多选树）、角色（多选）、人员（多选）
 * 四组实体放在**同一个弹窗**里，提交结构直接对应
 * `POST /api/knowledge/units/{id}/permissions`（8.4）的 `permissions` 数组。
 *
 * 数据来源：
 *   - 部门多选树 -> `GET /api/org/departments`（8.3）
 *   - 角色多选   -> `GET /api/org/roles`（8.3）
 *   - 人员多选   -> `GET /api/org/users`（第 14 章 #5 补齐）
 *     注意该接口要求 `menu:org`：知识管理员按 2.9.2 可能没有这个菜单权限，
 *     此时人员组会退化为只读展示「库内已有的人员实体」，保存时按原值保留 ——
 *     全量覆盖式保存最怕的就是「看不见的项被静默清空」。
 *
 * 已配置权限的回填来源：`GET /api/knowledge/units/{id}` 响应里的
 * `permissions[{target_type, target_id, target_name}]`（8.4）。
 */

import { listDepartments, listRoles, listUsers } from '../api/org.js';
import { setUnitPermissions, checkPermissions } from '../api/knowledge.js';
import { getUser } from '../core/store.js';
import { esc, $, toast, openModal } from '../core/dom.js';
import { renderDeptTree, renderRoleList, renderUserList, refreshCounts } from './permission-selects.js';

/** 四维实体的中文名与说明（6.2 数据权限四维模型） */
const TARGET_META = {
  global: { label: '全局公开', desc: 'target_id 固定为 0，表示所有登录用户均可访问' },
  department: { label: '按部门', desc: '命中用户所属部门即放行（精确匹配，不继承上下级）' },
  role: { label: '按角色', desc: '命中用户任一角色即放行' },
  user: { label: '按人员', desc: '仅指定用户本人可访问' },
};

/**
 * 打开数据权限配置弹窗。
 *
 * @param {object} opts
 *   unitId      知识单元 id
 *   unitTitle   弹窗标题上显示的单元标题
 *   assigned    已配置的权限数组 [{target_type, target_id, target_name}]
 *   onSaved     保存成功回调
 */
export async function openPermissionDialog({ unitId, unitTitle, assigned = [], onSaved }) {
  // 第 1 步：并行拉取部门树、角色列表与用户列表。任一失败则降级为空列表，弹窗仍然可用
  // 用户列表要求 menu:org，无该权限时这里会失败 —— 用 usersLoaded 记住这件事，
  // 保存时据此决定「人员组」是按勾选提交还是按原值保留
  let usersLoaded = true;
  const [departments, roles, users] = await Promise.all([
    listDepartments().catch(() => []),
    listRoles().catch(() => []),
    listUsers({ page: 1, page_size: 100 })
      .then((data) => (data && data.items) || [])
      .catch(() => {
        usersLoaded = false;
        return [];
      }),
  ]);

  // 第 2 步：把已配置权限按维度拆开，用于回填
  const assignedGlobal = (assigned || []).some((item) => item.target_type === 'global');
  const assignedDeptIds = new Set(
    (assigned || []).filter((item) => item.target_type === 'department').map((item) => String(item.target_id)),
  );
  const assignedRoleIds = new Set(
    (assigned || []).filter((item) => item.target_type === 'role').map((item) => String(item.target_id)),
  );
  const assignedUserIds = (assigned || [])
    .filter((item) => item.target_type === 'user')
    .map((item) => item.target_id);

  const html = `
    <div class="form-error hidden" data-role="error"></div>
    <p class="page-desc">
      四类实体可任意组合，<strong>满足任意一种即视为可访问</strong>（OR 逻辑，6.2）。
      保存为全量覆盖，未配置任何实体时除管理员外不可访问（默认拒绝）。
    </p>

    <!-- 第 1 组：全局开关 -->
    <div class="perm-group">
      <div class="perm-group-head">
        <span>${esc(TARGET_META.global.label)} <span class="tag">global</span></span>
        <label class="check-item">
          <input type="checkbox" data-role="global" ${assignedGlobal ? 'checked' : ''} />
          <span>公开给全部登录用户</span>
        </label>
      </div>
      <div class="perm-group-body">
        <div class="field-hint">${esc(TARGET_META.global.desc)}</div>
      </div>
    </div>

    <!-- 第 2 组：部门多选树 -->
    <div class="perm-group">
      <div class="perm-group-head">
        <span>${esc(TARGET_META.department.label)} <span class="tag">department</span></span>
        <span class="mute-sm" data-role="dept-count"></span>
      </div>
      <div class="perm-group-body perm-scroll-tree" data-role="dept-tree"></div>
    </div>

    <!-- 第 3 组：角色多选 -->
    <div class="perm-group">
      <div class="perm-group-head">
        <span>${esc(TARGET_META.role.label)} <span class="tag">role</span></span>
        <span class="mute-sm" data-role="role-count"></span>
      </div>
      <div class="perm-group-body" data-role="role-list"></div>
    </div>

    <!-- 第 4 组：人员多选 -->
    <div class="perm-group">
      <div class="perm-group-head">
        <span>${esc(TARGET_META.user.label)} <span class="tag">user</span></span>
        <span class="mute-sm" data-role="user-count"></span>
      </div>
      <div class="perm-group-body" data-role="user-list"></div>
    </div>

    <!-- 保存前自检：复用 8.4 的鉴权接口，确认当前登录人自己对哪些单元可见 -->
    <div class="perm-group">
      <div class="perm-group-head">
        <span>保存后自检</span>
        <button class="btn btn-sm" type="button" data-role="self-check">用当前账号验证</button>
      </div>
      <div class="perm-group-body">
        <div class="field-hint mb8">
          调用 <code>POST /api/knowledge/check-permissions</code>（8.4），以当前登录账号的身份校验本单元是否可访问。
        </div>
        <div data-role="check-result"></div>
      </div>
    </div>
  `;

  openModal({
    title: `数据权限配置 · ${unitTitle || `知识单元 #${unitId}`}`,
    size: 'lg',
    bodyHtml: html,
    okText: '保存',
    onMount: (body) => {
      // 第 3 步：渲染部门树、角色多选与人员多选
      $('[data-role="dept-tree"]', body).innerHTML = renderDeptTree(departments, assignedDeptIds);
      $('[data-role="role-list"]', body).innerHTML = renderRoleList(roles, assignedRoleIds);
      $('[data-role="user-list"]', body).innerHTML = renderUserList(users, assignedUserIds, usersLoaded);
      refreshCounts(body);

      // 第 4 步：勾选变化时刷新计数
      body.addEventListener('change', () => refreshCounts(body));

      // 第 5 步：自检按钮
      $('[data-role="self-check"]', body).addEventListener('click', async () => {
        const box = $('[data-role="check-result"]', body);
        const me = getUser() || {};
        if (!me.id) {
          box.innerHTML = '<span class="mute-sm">无法自检：当前会话没有用户 ID。</span>';
          return;
        }
        box.innerHTML = '<span class="mute-sm">校验中…</span>';
        try {
          const result = await checkPermissions(me.id, [Number(unitId)]);
          const allowed = (result.authorized_unit_ids || []).map(String).includes(String(unitId));
          box.innerHTML = allowed
            ? '<span class="tag tag-success">当前账号可访问该知识单元</span>'
            : '<span class="tag tag-danger">当前账号不可访问该知识单元</span> <span class="mute-sm">（尚未保存的勾选不会参与判定，请先保存）</span>';
        } catch (error) {
          box.innerHTML = `<span class="mute-sm">自检失败：${esc(error.message)}</span>`;
        }
      });
    },
    onOk: async (body) => {
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');

      // 第 6 步：收集四组勾选，组装成 8.4 要求的 permissions 数组
      const permissions = [];

      const globalBox = $('[data-role="global"]', body);
      if (globalBox.checked) {
        // global 的 target_id 固定为 0（6.2 / enums.py 的 GLOBAL_TARGET_ID）
        permissions.push({ target_type: 'global', target_id: 0 });
      }

      body.querySelectorAll('input[name="dept"]:checked').forEach((box) => {
        permissions.push({ target_type: 'department', target_id: Number(box.value) });
      });
      body.querySelectorAll('input[name="role"]:checked').forEach((box) => {
        permissions.push({ target_type: 'role', target_id: Number(box.value) });
      });

      // 第 7 步：人员实体。列表没拉到时（缺 menu:org）按原值保留 ——
      // 全量覆盖式保存下，把看不见的项当成「已取消」会把既有授权清空
      if (usersLoaded) {
        body.querySelectorAll('input[name="user"]:checked').forEach((box) => {
          permissions.push({ target_type: 'user', target_id: Number(box.value) });
        });
      } else {
        assignedUserIds.forEach((id) => {
          permissions.push({ target_type: 'user', target_id: Number(id) });
        });
      }

      try {
        await setUnitPermissions(unitId, permissions);
        toast(`已保存 ${permissions.length} 条权限记录`, 'success');
        if (onSaved) onSaved();
        return true;
      } catch (error) {
        errorBox.textContent = error.message || '保存失败';
        errorBox.classList.remove('hidden');
        return false;
      }
    },
  });
}

/** 供知识列表页显示权限摘要时复用（当后端未返回 permission_summary 时的本地兜底） */
export function summarizePermissions(permissions = []) {
  if (!permissions.length) return '未配置（默认拒绝）';
  const counts = { global: 0, department: 0, role: 0, user: 0 };
  permissions.forEach((item) => {
    if (counts[item.target_type] !== undefined) counts[item.target_type] += 1;
  });
  if (counts.global) return '全局公开';
  const parts = [];
  if (counts.department) parts.push(`${counts.department} 个部门`);
  if (counts.role) parts.push(`${counts.role} 个角色`);
  if (counts.user) parts.push(`${counts.user} 个人员`);
  return parts.length ? parts.join(' / ') : '未配置（默认拒绝）';
}

/** 生成一个可用于任意容器的权限摘要标签（列表页用） */
export function permissionTag(summary) {
  const text = summary || '未配置（默认拒绝）';
  const cls = text.includes('全局') ? 'tag-success' : text.includes('未配置') ? 'tag-warn' : 'tag-primary';
  return `<span class="tag ${cls}">${esc(text)}</span>`;
}
