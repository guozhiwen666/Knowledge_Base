/**
 * 用户管理 · 表单模板与取值
 *
 * 从 org-user-management.js 拆出（该文件保留页面渲染与脚本编排）。
 *
 * 三种操作模式共用一套表单结构，字段按模式裁剪：
 *   create  新增用户 -> username / password / display_name / department_id / role_ids / status
 *   edit    编辑用户 -> display_name / department_id / role_ids / status（登录名只读展示）
 *   reset   重置口令 -> new_password（留空则由后端用系统初始口令）
 *
 * **表单里不再出现「用户 ID」输入框**：列表接口（GET /api/org/users）已经提供真实主键，
 * 目标用户由调用方按行传入，用户不需要也不应该手输 ID。
 *
 * 之所以「重置口令」单独成一个模式而不是混在编辑里：它走的是独立接口
 * `POST /api/org/users/{id}/reset-password`，响应会回一次新口令明文；
 * 而编辑走 `PUT /api/org/users/{id}`，后端按「未传即不改」处理（见 users.py）。
 * 界面把动作拆开，才能保证每次只提交真正要改的字段。
 */

import { esc } from '../core/dom.js';

/**
 * 生成用户表单 HTML。
 * @param {'create'|'edit'|'reset'} mode 操作模式
 * @param {{departments: Array, roles: Array}} options 部门（已拍平带缩进）与角色选项
 * @param {object} [user] 编辑 / 重置时的目标用户（用于回填，来自列表行或详情接口）
 * @returns {string} HTML
 */
export function userFormHtml(mode, options, user = null) {
  const current = user || {};
  const currentDeptId = current.department_id === null || current.department_id === undefined ? '' : String(current.department_id);
  const currentRoleIds = new Set((current.role_ids || []).map(String));

  const deptOptions = options.departments
    .map(
      (dept) =>
        `<option value="${esc(dept.id)}" ${String(dept.id) === currentDeptId ? 'selected' : ''}>${esc(dept.name)}</option>`,
    )
    .join('');

  const roleBoxes = options.roles.length
    ? options.roles
        .map(
          (role) => `
      <label class="check-item">
        <input type="checkbox" name="role_ids" value="${esc(role.id)}"
               ${currentRoleIds.has(String(role.id)) ? 'checked' : ''} />
        <span>${esc(role.role_name || role.role_code)} <span class="mute-sm mono">#${esc(role.id)}</span></span>
      </label>`,
        )
        .join('')
    : '<span class="mute-sm">无可选角色（GET /api/org/roles 未返回数据）</span>';

  return `
    <div class="field">
      <label>目标用户</label>
      <input class="input" type="text" value="${esc(
        current.display_name ? `${current.display_name}（${current.username}）` : '新增用户',
      )}" disabled />
      <div class="field-hint">${
        mode === 'create'
          ? '新增：主键由后端生成。'
          : `用户主键 <span class="mono">${esc(current.id)}</span> 来自列表接口，无需手工输入。`
      }</div>
    </div>

    ${
      mode === 'create'
        ? `
      <div class="field">
        <label>登录名<span class="req">*</span></label>
        <input class="input" name="username" type="text" placeholder="唯一登录名，最长 64 字" />
      </div>
      <div class="field">
        <label>初始密码<span class="req">*</span></label>
        <input class="input" name="password" type="text" placeholder="初始口令，后端哈希后入库" />
      </div>`
        : ''
    }

    ${
      mode === 'reset'
        ? `
      <div class="field">
        <label>新口令</label>
        <input class="input" name="new_password" type="text" placeholder="留空则由后端生成为系统初始口令" />
        <div class="field-hint">
          提交后接口会返回一次<strong>明文口令</strong>（库里只存哈希），页面会把它展示给你。
        </div>
      </div>`
        : ''
    }

    ${
      mode === 'create' || mode === 'edit'
        ? `
      <div class="field">
        <label>显示名${mode === 'create' ? '<span class="req">*</span>' : ''}</label>
        <input class="input" name="display_name" type="text"
               value="${esc(current.display_name || '')}" placeholder="用于界面展示的名字" />
      </div>
      <div class="field">
        <label>所属部门</label>
        <select class="select" name="department_id">
          <option value="">（不归属部门）</option>
          ${deptOptions}
        </select>
      </div>
      <div class="field">
        <label>角色（多选）</label>
        <div class="perm-group-body">${roleBoxes}</div>
        ${
          mode === 'edit'
            ? '<div class="field-hint">编辑时勾选即全量覆盖该用户的角色关联；不勾选任何项会清空其角色。</div>'
            : ''
        }
      </div>
      <div class="field">
        <label>账号状态</label>
        <select class="select" name="status">
          <option value="1" ${Number(current.status) === 0 ? '' : 'selected'}>启用</option>
          <option value="0" ${Number(current.status) === 0 ? 'selected' : ''}>停用</option>
        </select>
        <div class="field-hint">停用后该账号无法登录（后端在认证阶段即拒绝，见 6.4）。</div>
      </div>`
        : ''
    }
  `;
}

/**
 * 从表单容器读取提交载荷。未出现在当前模式里的字段一律不出现在结果中，
 * 保证「只提交要改的字段」—— 后端按未传即不改处理。
 *
 * @param {HTMLElement} body 弹窗内容容器
 * @param {'create'|'edit'|'reset'} mode
 */
export function readUserForm(body, mode) {
  const get = (name) => body.querySelector(`[name="${name}"]`);
  const payload = {};

  if (mode === 'reset') {
    payload.new_password = get('new_password').value;
    return payload;
  }

  const roleIds = Array.from(body.querySelectorAll('input[name="role_ids"]:checked')).map((box) =>
    Number(box.value),
  );
  const deptRaw = get('department_id') ? get('department_id').value : '';

  if (mode === 'create') {
    payload.username = get('username').value.trim();
    payload.password = get('password').value;
    payload.display_name = get('display_name').value.trim();
  }

  if (mode === 'edit') {
    payload.display_name = get('display_name').value.trim();
  }

  if (mode === 'create' || mode === 'edit') {
    payload.role_ids = roleIds;
    // 空字符串表示「不归属部门」，按 null 提交（后端 department_id 可空）
    payload.department_id = deptRaw === '' ? null : Number(deptRaw);
    payload.status = Number(get('status').value);
  }

  return payload;
}
