/**
 * 用户管理 · 表单模板与取值
 *
 * 从 org-user-management.js 拆出（该文件保留脚本编排与页面渲染）。
 *
 * 四种操作模式共用一套表单结构，字段按模式裁剪：
 *   create   新增用户   -> username / password / display_name / department_id / role_ids / status
 *   edit     编辑用户   -> display_name / department_id / role_ids / status
 *   password 重置密码   -> password
 *   status   启停用     -> status
 *
 * 之所以「重置密码」与「启停用」各自单独成一个模式而不是混在编辑里：
 * 后端 update_user 按「未传即不改」处理（见 services/organization_structure_service/org.py），
 * 只传 status 就只改状态、只传 role_ids 就只改角色，互不影响。
 * 界面把动作拆开，才能保证每次只提交真正要改的字段。
 */

import { esc } from '../core/dom.js';

/**
 * 生成用户表单 HTML。
 * @param {'create'|'edit'|'password'|'status'} mode 操作模式
 * @param {{departments: Array, roles: Array}} options 部门（已拍平带缩进）与角色选项
 * @returns {string} HTML
 */
export function userFormHtml(mode, options) {
  const deptOptions = options.departments
    .map((dept) => `<option value="${esc(dept.id)}">${esc(dept.name)}</option>`)
    .join('');

  const roleBoxes = options.roles.length
    ? options.roles
        .map(
          (role) => `
      <label class="check-item">
        <input type="checkbox" name="role_ids" value="${esc(role.id)}" />
        <span>${esc(role.role_name || role.role_code)} <span class="mute-sm mono">#${esc(role.id)}</span></span>
      </label>`,
        )
        .join('')
    : '<span class="mute-sm">无可选角色（GET /api/org/roles 未返回数据）</span>';

  return `
    <div class="field">
      <label>用户 ID${mode === 'create' ? '' : '<span class="req">*</span>'}</label>
      <input class="input" name="user_id" type="number" min="1" placeholder="请输入目标用户的主键 ID"
             ${mode === 'create' ? 'disabled' : ''} />
      ${
        mode === 'create'
          ? '<div class="field-hint">新增时无需填写，由后端生成。</div>'
          : '<div class="field-hint">缺失 <code>GET /api/org/users</code>，无法列出用户，请手工填写目标用户 ID。</div>'
      }
    </div>

    ${
      mode === 'create'
        ? `
      <div class="field">
        <label>登录名<span class="req">*</span></label>
        <input class="input" name="username" type="text" placeholder="唯一登录名" />
      </div>
      <div class="field">
        <label>初始密码<span class="req">*</span></label>
        <input class="input" name="password" type="text" placeholder="初始口令，后端哈希后入库" />
      </div>`
        : ''
    }

    ${
      mode === 'password'
        ? `
      <div class="field">
        <label>新密码<span class="req">*</span></label>
        <input class="input" name="password" type="text" placeholder="留空则由后端重置为初始密码" />
        <div class="field-hint">对应 8.3：重置密码走 <code>PUT /api/org/users/{id}</code> 的密码语义。</div>
      </div>`
        : ''
    }

    ${
      mode === 'status'
        ? `
      <div class="field">
        <label>账号状态<span class="req">*</span></label>
        <select class="select" name="status">
          <option value="1">启用</option>
          <option value="0">停用</option>
        </select>
        <div class="field-hint">停用后该账号无法登录（后端在认证阶段即拒绝，见 6.4）。</div>
      </div>`
        : ''
    }

    ${
      mode === 'create' || mode === 'edit'
        ? `
      <div class="field">
        <label>显示名${mode === 'create' ? '<span class="req">*</span>' : ''}</label>
        <input class="input" name="display_name" type="text" placeholder="用于界面展示的名字" />
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
        ${mode === 'edit' ? '<div class="field-hint">编辑时勾选即全量覆盖该用户的角色关联；不勾选任何项会清空其角色。</div>' : ''}
      </div>
      <div class="field">
        <label>账号状态</label>
        <select class="select" name="status">
          <option value="1">启用</option>
          <option value="0">停用</option>
        </select>
      </div>`
        : ''
    }
  `;
}

/**
 * 从表单容器读取提交载荷。
 *
 * 返回对象里的 `_userId` 是内部字段，提交前必须删掉（接口路径需要它，请求体不需要）。
 * 未出现在当前模式里的字段一律不出现在结果中，保证「只提交要改的字段」。
 *
 * @param {HTMLElement} body 弹窗内容容器
 * @param {'create'|'edit'|'password'|'status'} mode
 */
export function readUserForm(body, mode) {
  const get = (name) => body.querySelector(`[name="${name}"]`);
  const roleIds = Array.from(body.querySelectorAll('input[name="role_ids"]:checked')).map((box) =>
    Number(box.value),
  );
  const deptRaw = get('department_id') ? get('department_id').value : '';

  const payload = {};

  if (mode !== 'create') {
    const raw = get('user_id').value.trim();
    payload._userId = raw ? Number(raw) : null;
  }

  if (mode === 'create') {
    payload.username = get('username').value.trim();
    payload.password = get('password').value;
    payload.display_name = get('display_name').value.trim();
  }

  if (mode === 'edit') {
    payload.display_name = get('display_name').value.trim();
    payload.role_ids = roleIds;
  }

  if (mode === 'password') {
    payload.password = get('password').value;
  }

  if (mode === 'status') {
    payload.status = Number(get('status').value);
  }

  if (mode === 'create' || mode === 'edit') {
    // 空字符串表示「不归属部门」，按 null 提交（后端 department_id 可空）
    payload.department_id = deptRaw === '' ? null : Number(deptRaw);
    payload.status = Number(get('status').value);
  }

  return payload;
}
