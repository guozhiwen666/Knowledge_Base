/**
 * 模块一：认证与权限控制（对应 9.1 第一个模块 / 页面 2.9.3-1 登录与个人中心页）
 *
 * 职责：
 *   1. 用户名口令登录（8.2 `POST /api/auth/login`），把 access_token / user_info /
 *      permissions 写入 sessionStorage（store.js）；
 *   2. 已登录时同路由渲染「个人中心」：身份、所属部门、拥有角色、权限码清单；
 *   3. 登录后的落地页按权限码决定（9.3 路由守卫）。
 *
 * 关于「拥有角色」的展示口径：8.2 的 user_info 只返回 `role_ids`，不含角色名，
 * 因此这里用 `GET /api/org/roles`（8.3）反查角色名 —— 该接口存在，不算自造端点。
 * 但若当前账号没有 menu:org 权限，后端会返回 403，此时降级为展示角色 ID 并给出说明。
 */

import { login } from '../api/auth.js';
import { listRoles } from '../api/org.js';
import { setSession, getUser, displayName, getPermissions, clearSession } from '../core/store.js';
import { post } from '../api/client.js';
import { navigate, homePath } from '../core/router.js';
import { el, esc, $, toast, fmtNumber } from '../core/dom.js';

/* ------------------------------------------------------------ 路由入口 */

/**
 * #/login 的页面渲染：未登录 → 登录表单；已登录 → 个人中心。
 * 返回 Promise，由 router 的 renderInto 负责挂载（等待期间有 loading 占位）。
 */
export async function renderLoginOrProfile() {
  const user = getUser();
  return user ? renderProfile() : renderLoginForm();
}

/* ------------------------------------------------------------ 登录表单 */

/** 渲染登录表单（免外壳，全屏渐变） */
export function renderLoginForm() {
  const wrap = el(`
    <div class="login-wrap">
      <div class="login-card">
        <h1>知识库管理平台</h1>
        <p class="page-desc">请使用平台账号登录</p>
        <div class="form-error hidden" data-role="error"></div>
        <form data-role="form" autocomplete="off">
          <div class="field">
            <label>用户名<span class="req">*</span></label>
            <input class="input" name="username" type="text" placeholder="请输入用户名" required />
          </div>
          <div class="field">
            <label>密码<span class="req">*</span></label>
            <input class="input" name="password" type="password" placeholder="请输入密码" required />
          </div>
          <button class="btn btn-primary btn-block" type="submit" data-role="submit">登 录</button>
        </form>
        <p class="field-hint mt16">登录态存放于 sessionStorage，关闭标签页即失效。</p>
      </div>
    </div>
  `);

  const form = $('[data-role="form"]', wrap);
  const errorBox = $('[data-role="error"]', wrap);
  const submitBtn = $('[data-role="submit"]', wrap);

  // 第 1 步：提交登录
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorBox.classList.add('hidden');

    const username = form.username.value.trim();
    const password = form.password.value;
    // 第 2 步：前端只做「非空」这一层校验，账号密码正确性完全由后端裁定
    if (!username || !password) {
      showError(errorBox, '请输入用户名和密码');
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = '登录中…';
    try {
      // 第 3 步：调 8.2 登录接口，响应为 {access_token, user_info, permissions}
      const data = await login(username, password);

      // 第 4 步：写会话（Token 进 sessionStorage）
      setSession(data || {});

      // 第 5 步：按权限码决定落地页（9.3 动态菜单 + 路由守卫）
      toast(`欢迎，${displayName() || username}`, 'success');
      const target = homePath();
      window.location.hash = target;
    } catch (error) {
      showError(errorBox, error.message || '登录失败');
      submitBtn.disabled = false;
      submitBtn.textContent = '登 录';
      return;
    }
    submitBtn.disabled = false;
    submitBtn.textContent = '登 录';
  });

  return wrap;
}

/** 显示表单错误（后端返回的 message 原样展示，便于定位） */
function showError(box, message) {
  box.textContent = message;
  box.classList.remove('hidden');
}

/* ------------------------------------------------------------ 个人中心 */

/** 渲染个人中心：身份 / 部门 / 角色 / 权限码 */
export async function renderProfile() {
  const user = getUser() || {};
  const codes = getPermissions();

  // 第 1 步：尝试用 GET /api/org/roles 把 role_ids 翻成角色名。
  // 无 menu:org 权限时会 403，因此这里 best-effort，失败就降级展示 ID。
  let roleNames = null;
  let roleLookupFailed = false;
  try {
    const roles = await listRoles();
    const map = new Map((roles || []).map((role) => [role.id, role.role_name || role.role_code]));
    roleNames = (user.role_ids || []).map((id) => map.get(id) || `角色 #${id}`);
  } catch (error) {
    roleLookupFailed = true;
  }

  const container = el(`
    <div>
      <h2 class="page-title">个人中心</h2>
      <p class="page-desc">当前登录账号的身份、所属部门、拥有角色与权限码</p>

      <div class="card">
        <div class="card-title">账号信息</div>
        <table class="data">
          <tbody>
            <tr><th style="width:140px">显示名</th><td data-role="display_name"></td></tr>
            <tr><th>登录名</th><td data-role="username"></td></tr>
            <tr><th>用户 ID</th><td data-role="user_id"></td></tr>
            <tr><th>所属部门</th><td data-role="department"></td></tr>
            <tr><th>拥有角色</th><td data-role="roles"></td></tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-title">
          <span>操作权限码</span>
          <span class="mute-sm">共 ${fmtNumber(codes.length)} 项 · 驱动菜单与按钮显隐</span>
        </div>
        <div data-role="codes"></div>
      </div>

      <div class="card">
        <div class="card-title">会话操作</div>
        <button class="btn btn-danger" type="button" data-role="logout">退出登录</button>
        <p class="field-hint">2.9.8 未提供登出接口，退出为纯前端行为：清空 sessionStorage 并回到登录页。</p>
      </div>
    </div>
  `);

  // 第 2 步：填充字段
  $('[data-role="display_name"]', container).textContent = displayName() || '-';
  $('[data-role="username"]', container).textContent = user.username || '-';
  $('[data-role="user_id"]', container).textContent = user.id ?? '-';

  const dept = $('[data-role="department"]', container);
  dept.textContent = user.department_name
    ? `${user.department_name}（部门 ID：${user.department_id ?? '-'}）`
    : `未归属部门（department_id：${user.department_id ?? '-'}）`;

  const roleCell = $('[data-role="roles"]', container);
  if (roleNames && roleNames.length) {
    roleCell.innerHTML = roleNames.map((name) => `<span class="tag tag-primary">${esc(name)}</span>`).join(' ');
  } else if (roleLookupFailed) {
    roleCell.innerHTML = `
      <span class="mute-sm">角色名暂不可见，展示角色 ID：</span>
      ${(user.role_ids || []).map((id) => `<span class="tag">#${esc(id)}</span>`).join(' ') || '<span class="mute-sm">无</span>'}
      <div class="mute-sm mt8">说明：角色名来自 <code>GET /api/org/roles</code>，当前账号无 <code>menu:org</code> 权限时该接口返回 403，故降级为角色 ID。</div>
    `;
  } else {
    roleCell.innerHTML = '<span class="mute-sm">该账号未分配角色</span>';
  }

  // 第 3 步：权限码清单
  const codesBox = $('[data-role="codes"]', container);
  codesBox.innerHTML = codes.length
    ? `<div class="row">${codes.map((code) => `<span class="tag mono">${esc(code)}</span>`).join('')}</div>`
    : '<div class="mute-sm">该账号没有任何权限码，请联系管理员在角色管理中分配。</div>';

  // 第 4 步：退出登录（先通知后端吊销令牌，再清本地会话）
  $('[data-role="logout"]', container).addEventListener('click', async () => {
    try {
      await post('/api/auth/logout');
    } catch {
      /* 后端未实现登出或网络异常：前端照常清本地会话 */
    }
    clearSession();
    toast('已退出登录', 'success');
    navigate('/login');
  });

  return container;
}
