/**
 * 角色管理 · 角色表单弹窗
 *
 * 从 org-module.js 拆出（该文件保留 Tab 容器、角色列表与权限配置入口）。
 *
 * 接口（第 14 章 #5 补齐）：
 *   `POST /api/org/roles`       新增（role_name / role_code 必填，description 可选）
 *   `PUT  /api/org/roles/{id}`  编辑（字段全可选，未传即不改）
 *
 * 两处 422 要如实显示后端 message：
 *   1. 角色编码重复（role_code 有唯一约束）；
 *   2. 编辑时传了非法取值（如超长）。
 *
 * 编辑角色编码的提示：后端注释说明 admin_bypass 之类的名单按 role_code 引用，
 * 改动编码前需确认相关配置同步 —— 这一点在界面上如实标注。
 */

import { createRole, updateRole } from '../api/org.js';
import { esc, $, toast, openModal } from '../core/dom.js';

/**
 * 打开角色表单弹窗。
 * @param {object} opts
 *   mode   'create' | 'edit'
 *   role   编辑时的角色 { id, role_name, role_code, description }
 *   onDone 保存成功回调
 */
export function openRoleForm({ mode, role, onDone }) {
  const current = role || {};

  openModal({
    title: mode === 'edit' ? `编辑角色 · ${current.role_name || ''}` : '新增角色',
    bodyHtml: `
      <div class="form-error hidden" data-role="error"></div>

      <div class="field">
        <label>角色名称<span class="req">*</span></label>
        <input class="input" data-role="role-name" type="text" maxlength="64"
               value="${esc(current.role_name || '')}" placeholder="展示用名称，如「知识管理员」" />
      </div>

      <div class="field">
        <label>角色编码<span class="req">*</span></label>
        <input class="input mono" data-role="role-code" type="text" maxlength="64"
               value="${esc(current.role_code || '')}" placeholder="唯一编码，如 kb_admin" />
        <div class="field-hint">
          编码唯一，重复会返回 422；后台的绕过名单按编码引用，已有角色改编码前请确认相关配置同步。
        </div>
      </div>

      <div class="field">
        <label>描述</label>
        <textarea class="textarea" data-role="description" maxlength="255"
                  placeholder="可留空，最长 255 字">${esc(current.description || '')}</textarea>
      </div>

      <div class="field-hint">
        新建的角色默认没有任何权限，需要在列表里点「配置权限」给它分配权限码（全量覆盖式保存）。
      </div>
    `,
    okText: '保存',
    onOk: async (body) => {
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');

      // 第 1 步：前端只拦两个必填项，长度与唯一性交给后端裁定
      const roleName = $('[data-role="role-name"]', body).value.trim();
      const roleCode = $('[data-role="role-code"]', body).value.trim();
      if (!roleName || !roleCode) {
        errorBox.textContent = '角色名称与角色编码均为必填';
        errorBox.classList.remove('hidden');
        return false;
      }

      const payload = {
        role_name: roleName,
        role_code: roleCode,
        description: $('[data-role="description"]', body).value.trim() || null,
      };

      try {
        if (mode === 'edit') {
          await updateRole(current.id, payload);
          toast(`角色「${roleName}」已更新`, 'success');
        } else {
          await createRole(payload);
          toast(`角色「${roleName}」已创建`, 'success');
        }
        if (onDone) onDone();
        return true;
      } catch (error) {
        // 422（编码重复 / 超长）原样展示后端 message
        errorBox.textContent = error.message || '保存失败';
        errorBox.classList.remove('hidden');
        return false;
      }
    },
  });
}
