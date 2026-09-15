/**
 * 应用入口：注册路由、装配外壳、启动
 *
 * 对应 9.3 的路由与权限表，逐条落位：
 *   #/login                                       登录与个人中心（免鉴权）
 *   #/org/users|#/org/roles|#/org/departments     组织架构与权限管理（menu:org）
 *   #/knowledge/import|#/knowledge/units|.../:id  知识维护与导入（menu:knowledge）
 *   #/ai/chat                                     AI 对话鉴权工作台（ai:chat:access）
 *   #/dashboard                                   数据看板（menu:dashboard）
 *   #/settlement                                  知识沉淀管理（menu:settlement）
 *
 * 本文件只做「接线」，不放任何业务逻辑 —— 业务在各 modules/ 下。
 */

import { register, setBeforeEach, setNotFound, startRouter } from './core/router.js';
import { wrapWithShell } from './core/shell.js';
import { isLoggedIn } from './core/store.js';
import { el, emptyState } from './core/dom.js';

import { renderLoginOrProfile } from './modules/auth-module.js';
import { renderUserManagement, renderRoleManagement, renderDepartmentManagement } from './modules/org-module.js';
import { renderImportCenter, renderUnitList, renderUnitDetail } from './modules/knowledge-module.js';
import { renderChat } from './modules/chat-module.js';
import { renderDashboard } from './modules/dashboard-module.js';
import { renderSettlement } from './modules/settlement-module.js';

/* ---------------------------------------------------------------- 路由注册 */

// 第 1 步：登录与个人中心。免鉴权，但已登录时同一条路由渲染个人中心
// （9.3 路由表把「登录与个人中心」列为同一个路由 #/login）
register('/login', {
  title: '登录与个人中心',
  skipAuth: true,
  handler: () => renderLoginOrProfile(),
});

// 第 2 步：组织架构与权限管理三页，共用 menu:org 权限码
register('/org/users', {
  title: '用户管理',
  permission: 'menu:org',
  handler: () => renderUserManagement(),
});
register('/org/roles', {
  title: '角色管理',
  permission: 'menu:org',
  handler: () => renderRoleManagement(),
});
register('/org/departments', {
  title: '部门管理',
  permission: 'menu:org',
  handler: () => renderDepartmentManagement(),
});

// 第 3 步：知识维护与导入
register('/knowledge/import', {
  title: '知识导入中心',
  permission: 'menu:knowledge',
  handler: () => renderImportCenter(),
});
register('/knowledge/units', {
  title: '知识单元列表',
  permission: 'menu:knowledge',
  handler: () => renderUnitList(),
});
register('/knowledge/units/:id', {
  title: '知识单元详情',
  permission: 'menu:knowledge',
  hiddenInMenu: true,
  handler: ({ params }) => renderUnitDetail(params.id),
});

// 第 4 步：AI 对话工作台（9.3：ai:chat:access + 登录态）
register('/ai/chat', {
  title: 'AI 对话鉴权工作台',
  permission: 'ai:chat:access',
  handler: () => renderChat(),
});

// 第 5 步：数据看板
register('/dashboard', {
  title: '数据看板',
  permission: 'menu:dashboard',
  handler: () => renderDashboard(),
});

// 第 6 步：知识沉淀管理
register('/settlement', {
  title: '知识沉淀管理',
  permission: 'menu:settlement',
  handler: () => renderSettlement(),
});

/* ---------------------------------------------------------------- 全局接线 */

// 第 7 步：登录页不加外壳（全屏渐变卡片），其余页面统一套外壳 + 动态菜单
setBeforeEach((route, run) => {
  if (route.skipAuth && !isLoggedIn()) {
    return run();
  }
  return wrapWithShell(route, run);
});

// 第 8 步：未匹配路由的兜底
setNotFound(() =>
  el(`<div class="card">${emptyState('页面不存在，请检查地址栏 hash 路由', '404')}</div>`),
);

/* ---------------------------------------------------------------- 启动 */

// 第 9 步：启动（router 内部会根据登录态决定落到哪个路由）
startRouter();
