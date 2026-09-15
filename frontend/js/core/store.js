/**
 * 会话与权限状态（对应 9.1「认证与权限控制模块」的状态部分）
 *
 * 职责：
 *   1. Token 存 sessionStorage（2.9.5 明确要求 sessionStorage，不用 localStorage）；
 *   2. 缓存登录返回的 user_info 与 permissions，供动态菜单与按钮级显隐读取；
 *   3. 提供 hasPermission / requirePermission 两个判断入口。
 *
 * 刻意不做的事：不在前端做任何数据权限判定。6.2/6.3 的可见性判定只由后端
 * 数据权限引擎裁定，前端拿到什么就展示什么。
 */

const TOKEN_KEY = 'kb_token';
const USER_KEY = 'kb_user';
const PERM_KEY = 'kb_permissions';

// 内存态：避免每次都读 sessionStorage（同步 API，高频读取会拖慢渲染）
let token = sessionStorage.getItem(TOKEN_KEY) || '';
let user = safeParse(sessionStorage.getItem(USER_KEY));
// 权限码为 null 时按空数组处理，避免调用方到处判空
let permissions = safeParse(sessionStorage.getItem(PERM_KEY)) || [];

/** 宽松解析 JSON：坏数据不抛异常，返回 null 让调用方走兜底分支 */
function safeParse(raw) {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** 取当前 Token；空串表示未登录 */
export function getToken() {
  return token;
}

/** 取当前登录用户信息（user_info），未登录返回 null */
export function getUser() {
  return user;
}

/** 取当前用户的全部权限码（6.1 的操作权限码集合） */
export function getPermissions() {
  return permissions;
}

/** 是否已登录：只判 Token 是否存在，Token 是否过期由后端 401 来判定 */
export function isLoggedIn() {
  return Boolean(token);
}

/** 写入登录态（登录成功后调用） */
export function setSession({ access_token, user_info, permissions: perms }) {
  token = access_token || '';
  user = user_info || null;
  permissions = Array.isArray(perms) ? perms : [];

  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(USER_KEY, JSON.stringify(user));
  sessionStorage.setItem(PERM_KEY, JSON.stringify(permissions));
}

/** 清空登录态（登出、收到 401 时调用） */
export function clearSession() {
  token = '';
  user = null;
  permissions = [];
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
  sessionStorage.removeItem(PERM_KEY);
}

/**
 * 判断是否持有某个权限码（6.1：多角色 OR，后端已合并好，这里只做包含判断）。
 * 不传 code 时视为"不要求权限"，直接放行。
 */
export function hasPermission(code) {
  if (!code) return true;
  return permissions.includes(code);
}

/**
 * 过滤出一批权限码里当前用户真正持有的那些（用于菜单/按钮渲染）。
 */
export function filterPermissions(codes) {
  return (codes || []).filter((code) => hasPermission(code));
}

/** 展示名：优先 display_name，兜底 username */
export function displayName() {
  if (!user) return '';
  return user.display_name || user.username || '';
}
