/**
 * 应用外壳（顶栏 + 侧边菜单 + 内容区）与动态菜单
 *
 * 对应 9.1「认证与权限控制模块」里的动态菜单部分与 9.3 的路由权限表。
 *
 * 关键点：
 *   1. 菜单项与权限码一一对应，渲染时按登录返回的 permissions 过滤（hasPermission）；
 *   2. 外壳**同步返回**，内容区先挂载 loading，页面数据回来后再替换 ——
 *      这样 <100ms 内屏幕就有骨架，不会出现空白闪屏（9.4）；
 *   3. 页面渲染异常（比如后端没起）在外壳内落成错误卡片，不整页白屏。
 */

import { el, esc, $, loadingState, toast } from './dom.js';
import { hasPermission, getUser, displayName, clearSession, getPermissions } from './store.js';
import { post } from '../api/client.js';
import { currentPathname, navigate, homePath } from './router.js';

/** 菜单定义：分组 → 条目，每条带路由路径与所需权限码（与 9.3 路由表一致） */
const MENUS = [
  {
    group: '个人',
    items: [{ label: '登录与个人中心', path: '/login', permission: '', skipAuth: true }],
  },
  {
    group: '组织与权限',
    items: [
      { label: '用户管理', path: '/org/users', permission: 'menu:org' },
      { label: '角色管理', path: '/org/roles', permission: 'menu:org' },
      { label: '部门管理', path: '/org/departments', permission: 'menu:org' },
    ],
  },
  {
    group: '知识',
    items: [
      { label: '知识导入中心', path: '/knowledge/import', permission: 'menu:knowledge' },
      { label: '知识单元列表', path: '/knowledge/units', permission: 'menu:knowledge' },
    ],
  },
  {
    group: '智能问答',
    items: [{ label: 'AI 对话工作台', path: '/ai/chat', permission: 'ai:chat:access' }],
  },
  {
    group: '分析与沉淀',
    items: [
      { label: '数据看板', path: '/dashboard', permission: 'menu:dashboard' },
      { label: '知识沉淀管理', path: '/settlement', permission: 'menu:settlement' },
    ],
  },
];

/** 过滤出当前用户可见的菜单分组（无权限的分组整体不显示） */
function visibleMenus() {
  return MENUS.map((group) => ({
    ...group,
    items: group.items.filter((item) => item.skipAuth || hasPermission(item.permission)),
  })).filter((group) => group.items.length > 0);
}

/** 当前页面标题：用于顶栏面包屑，取路径最后一个非参数片段 */
function crumbText(pathname) {
  for (const group of MENUS) {
    const hit = group.items.find((item) => item.path === pathname);
    if (hit) return hit.label;
  }
  if (pathname.startsWith('/knowledge/units/')) return '知识单元详情';
  return pathname;
}

/** 渲染侧边菜单 HTML */
function sidebarHtml() {
  const active = currentPathname();
  return visibleMenus()
    .map(
      (group) => `
        <div class="nav-group-title">${esc(group.group)}</div>
        ${group.items
          .map(
            (item) => `
          <div class="nav-item ${active === item.path || active.startsWith(`${item.path}/`) ? 'active' : ''}"
               data-path="${esc(item.path)}">
            ${esc(item.label)}
          </div>`,
          )
          .join('')}
      `,
    )
    .join('');
}

/**
 * 退出登录：先通知后端吊销当前令牌（使 JWT 立即失效），再清本地会话。
 * 后端无登出接口时（未部署 P1-7 加固）请求会 404，前端忽略即可，不影响退出。
 */
async function logout() {
  try {
    await post('/api/auth/logout');
  } catch {
    /* 后端未实现登出或网络异常：前端照常清本地会话 */
  }
  clearSession();
  toast('已退出登录', 'success');
  window.location.hash = '/login';
}

/**
 * 用外壳包裹页面内容。
 *
 * @param {object} route 当前路由
 * @param {Function} run 页面 handler
 * @returns {HTMLElement} 外壳节点（同步返回，内容异步填充）
 */
export function wrapWithShell(route, run) {
  const user = getUser() || {};
  const shell = el(`
    <div class="shell">
      <div class="shell-brand"><span class="logo"></span><span>知识库管理平台</span></div>
      <div class="shell-header">
        <div class="crumb">${esc(crumbText(currentPathname()))}</div>
        <div class="row">
          <span class="mute-sm">${esc(displayName())}${user.department_name ? ` · ${esc(user.department_name)}` : ''}</span>
          <button class="btn btn-sm" type="button" data-role="logout">退出登录</button>
        </div>
      </div>
      <div class="shell-sidebar">${sidebarHtml()}</div>
      <div class="shell-main">${loadingState()}</div>
    </div>
  `);

  // 第 1 步：菜单点击 —— 用事件委托，避免每次渲染重复绑定
  shell.querySelector('.shell-sidebar').addEventListener('click', (event) => {
    const item = event.target.closest('.nav-item');
    if (item && item.dataset.path) navigate(item.dataset.path);
  });

  // 第 2 步：退出登录
  shell.querySelector('[data-role="logout"]').addEventListener('click', logout);

  // 第 3 步：异步执行页面 handler 并填充内容区；失败落成错误卡片
  const main = $('.shell-main', shell);
  Promise.resolve()
    .then(() => run())
    .then((content) => {
      if (!main.isConnected) return; // 页面已切走，丢弃结果
      if (content instanceof Node) main.replaceChildren(content);
    })
    .catch((error) => {
      if (!main.isConnected) return;
      main.replaceChildren(errorCard(error));
      console.error('[page error]', error);
    });

  return shell;
}

/** 页面级错误卡片：把后端/网络错误如实展示出来，方便定位 */
export function errorCard(error) {
  const message = (error && error.message) || '未知错误';
  const status = error && error.status ? `HTTP ${error.status}` : '';
  return el(`
    <div class="card">
      <div class="empty-state">
        <span class="ico">[x]</span>
        <div><strong>页面数据加载失败</strong></div>
        <div class="mt8">${esc(message)}</div>
        ${status ? `<div class="mute-sm mt8">${esc(status)}</div>` : ''}
      </div>
    </div>
  `);
}

/** 顶栏可用的权限码清单（供调试查看，渲染在个人中心页） */
export function permissionList() {
  return getPermissions();
}

/** 跳回首页（无权限时的回退目标） */
export function goHome() {
  navigate(homePath());
}
