/**
 * Hash 路由与权限守卫（对应 9.3 的路由表）
 *
 * 设计要点：
 *   1. 用 hash 路由而非 history 路由，纯静态站点无需服务端 rewrite；
 *   2. 每条路由声明菜单权限码（menu:org / menu:knowledge / ...），
 *      守卫按登录返回的 permissions 判断，不持有权限则提示并回退首页；
 *   3. 路由处理器返回一个 DOM 节点（同步）或 Promise（异步拉数据），
 *      在等待期间先挂载 loading 占位，避免空白闪屏（9.4）。
 */

import { isLoggedIn, hasPermission, getPermissions } from './store.js';
import { el, esc, toast } from './dom.js';

/** 路由表：path 为 hash 路径（# 之后的字符串），支持 :param 占位 */
const routes = [];
let notFoundHandler = null;
let beforeEach = null;

/**
 * 注册路由。
 * @param {string} path   形如 '/knowledge/units/:id'
 * @param {object} opts   { title, permission, handler(ctx), hiddenInMenu }
 *   handler 返回 HTMLElement 或 Promise<HTMLElement>
 */
export function register(path, opts) {
  routes.push({ path, ...opts });
}

/** 注册兜底处理器（未匹配路由） */
export function setNotFound(handler) {
  notFoundHandler = handler;
}

/** 注册全局前置钩子（用于渲染外壳、更新菜单高亮等） */
export function setBeforeEach(fn) {
  beforeEach = fn;
}

/** 全部已注册路由（菜单渲染据此生成，只取未标记 hiddenInMenu 的） */
export function getRoutes() {
  return routes.slice();
}

/** 当前 hash 路径（含查询串）；空 hash 视作根 */
export function currentPath() {
  const raw = window.location.hash.replace(/^#/, '');
  return raw || '/';
}

/** 跳转到指定 hash 路径 */
export function navigate(path) {
  if (currentPath() === path) {
    // 相同路径手动触发一次渲染，保证点击菜单能刷新页面数据
    handleRoute();
    return;
  }
  window.location.hash = path;
}

/** 仅取路径部分，丢掉 ?a=b（查询串由页面自己解析） */
export function currentPathname() {
  return currentPath().split('?')[0];
}

/** 解析查询串为对象 */
export function currentQuery() {
  const raw = currentPath().split('?')[1] || '';
  const query = {};
  for (const pair of raw.split('&')) {
    if (!pair) continue;
    const [key, value = ''] = pair.split('=');
    query[decodeURIComponent(key)] = decodeURIComponent(value);
  }
  return query;
}

/** 把路径按 :param 匹配出参数对象；不匹配返回 null */
function matchRoute(pathname) {
  const target = pathname.split('/').filter(Boolean);
  for (const route of routes) {
    const pattern = route.path.split('/').filter(Boolean);
    if (pattern.length !== target.length) continue;
    const params = {};
    let matched = true;
    for (let i = 0; i < pattern.length; i += 1) {
      if (pattern[i].startsWith(':')) {
        params[pattern[i].slice(1)] = decodeURIComponent(target[i]);
      } else if (pattern[i] !== target[i]) {
        matched = false;
        break;
      }
    }
    if (matched) return { route, params };
  }
  return null;
}

/** 首页：第一个当前用户有权限的菜单路由 */
export function homePath() {
  const first = routes.find(
    (route) => !route.hiddenInMenu && route.skipAuth !== true && hasPermission(route.permission),
  );
  return first ? first.path : '/login';
}

/**
 * 执行一次路由渲染。核心流程：
 *   匹配 → 登录态校验 → 菜单权限校验 → 渲染。
 */
export async function handleRoute() {
  const pathname = currentPathname();
  const app = document.getElementById('app');
  if (!app) return;

  const matched = matchRoute(pathname);
  if (!matched) {
    await renderInto(app, notFoundHandler ? notFoundHandler() : elPendingNotFound());
    return;
  }

  const { route, params } = matched;

  // 第 1 步：免鉴权页面（登录页）直接渲染。
  // 注意 #/login 同时承载「个人中心」（9.3 路由表：登录与个人中心为同一路由），
  // 因此已登录时不重定向，由 handler 自己决定渲染登录表单还是个人中心。
  if (route.skipAuth) {
    await renderInto(app, beforeWrap(route, () => route.handler({ params }), app));
    return;
  }

  // 第 2 步：未登录一律重定向登录页（9.3 路由守卫第一条）
  if (!isLoggedIn()) {
    window.location.hash = '/login';
    return;
  }

  // 第 3 步：菜单权限校验，不通过则提示并回退到有权限的首页
  if (!hasPermission(route.permission)) {
    toast(`无访问权限：需要 ${route.permission}`, 'warn');
    const fallback = homePath();
    if (fallback && fallback !== pathname) {
      window.location.hash = fallback;
    } else {
      await renderInto(app, elNoPermission(route.permission));
    }
    return;
  }

  // 第 4 步：正常渲染
  await renderInto(app, beforeWrap(route, () => route.handler({ params }), app));
}

/** 渲染前统一走全局钩子（装配外壳菜单、面包屑） */
function beforeWrap(route, run, app) {
  return typeof beforeEach === 'function' ? beforeEach(route, run, app) : run();
}

/**
 * 把 handler 的返回值渲染进容器。
 * 若 handler 返回 Promise，先挂载 loading（保证 <100ms 内有内容，不白屏），
 * 等数据回来再替换。
 */
async function renderInto(container, value) {
  const result = await value;
  if (result instanceof Node) {
    container.replaceChildren(result);
    return;
  }
  // handler 返回的是 Promise<HTMLElement> 时，等待期间先渲染占位
  if (result && typeof result.then === 'function') {
    container.replaceChildren(el('<div class="loading-state"><span class="spinner"></span>加载中…</div>'));
    container.replaceChildren(await result);
  }
}

function elPendingNotFound() {
  return el('<div class="card"><div class="empty-state"><span class="ico">404</span>页面不存在</div></div>');
}

function elNoPermission(permission) {
  return el(`
    <div class="card">
      <div class="empty-state">
        <span class="ico">[!]</span>
        当前账号没有访问该页面的权限（需要 <code>${esc(permission) || '未知'}</code>）
        <div class="mute-sm mt8">当前权限码：${esc(getPermissions().join('、')) || '无'}</div>
      </div>
    </div>
  `);
}

/** 启动路由：绑定 hashchange 并立即执行一次 */
export function startRouter() {
  window.addEventListener('hashchange', () => {
    handleRoute();
  });
  if (!window.location.hash) {
    window.location.hash = isLoggedIn() ? homePath() : '/login';
  } else {
    handleRoute();
  }
}
