/**
 * 用户管理 · 重置口令弹窗
 *
 * 从 org-user-management.js 拆出（该文件保留列表、筛选、分页与行内操作编排）。
 *
 * 接口：`POST /api/org/users/{id}/reset-password`（第 14 章 #5）
 *   请求：{ new_password? } —— 不传则由后端生成为系统初始口令
 *   响应：{ user_id, new_password } —— **明文只返回这一次**，库里存的是 PBKDF2 哈希
 *
 * 因此本文件有两个弹窗：先收新口令（可留空），成功后立刻弹第二个把明文展示出来。
 * 明文弹窗刻意去掉「取消」按钮：这是「看一眼就必须记下」的信息，
 * 误点关闭后管理员自己也拿不到了。
 */

import { resetUserPassword } from '../api/org.js';
import { esc, openModal, $ } from '../core/dom.js';
import { userFormHtml, readUserForm } from './org-user-form.js';

/**
 * 打开重置口令弹窗。
 * @param {object} user 目标用户（来自列表行，含 id / username / display_name）
 */
export function openResetPasswordModal(user) {
  openModal({
    title: `重置口令 · ${user.display_name || user.username}`,
    bodyHtml: `<div class="form-error hidden" data-role="error"></div>${userFormHtml('reset', { departments: [], roles: [] }, user)}`,
    okText: '重置',
    onOk: async (body) => {
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');
      const { new_password: newPassword } = readUserForm(body, 'reset');
      try {
        const data = await resetUserPassword(user.id, newPassword);
        showPasswordResult(user, (data && data.new_password) || '');
        return true;
      } catch (error) {
        errorBox.textContent = error.message || '重置口令失败';
        errorBox.classList.remove('hidden');
        return false;
      }
    },
  });
}

/** 展示接口返回的明文口令 */
function showPasswordResult(user, password) {
  const modal = openModal({
    title: '口令已重置',
    okText: '我已记录',
    bodyHtml: `
      <div class="review-panel">
        <div class="row-between mb8">
          <strong>${esc(user.display_name || user.username)}</strong>
          <span class="tag mono">${esc(user.username || '')}</span>
        </div>
        <p class="page-desc">新口令（明文只返回这一次，库里存的是哈希）：</p>
        <p class="mono" style="font-size:18px;word-break:break-all">${esc(password || '（后端未返回口令）')}</p>
      </div>
      <div class="field-hint mt16">
        请立即转达给该用户，并提醒其尽快修改；关闭本弹窗后无法再次查看。
      </div>
    `,
  });

  const cancel = modal.body.parentElement.querySelector('[data-role="cancel"]');
  if (cancel) cancel.remove();
}
