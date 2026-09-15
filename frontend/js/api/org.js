/**
 * 组织架构与权限接口封装
 *
 * 覆盖 8.8 接口清单中的第 2~6 个接口：
 *   2. `GET  /api/org/departments`              —— 部门树
 *   3. `POST /api/org/users`                    —— 用户新增
 *   4. `PUT  /api/org/users/{id}`               —— 用户编辑（承载启停用 / 重置密码语义）
 *   5. `GET  /api/org/roles`                    —— 角色列表（含已分配权限）
 *   6. `POST /api/org/roles/{id}/permissions`   —— 角色权限分配（全量覆盖）
 *
 * 明确不在本文件内的方法（8 章没有这些接口，页面用「待接口确认」占位）：
 *   - 用户列表查询、用户删除
 *   - 部门新增 / 编辑 / 删除、部门成员查询
 *   - 角色新增 / 编辑 / 删除
 */

import { get, post, put } from './client.js';

/* ------------------------------------------------------------------ 部门 */

/**
 * 部门树形列表（8.3）。
 * @returns {Promise<Array<{id:number,parent_id:number|null,name:string,leader_id:number|null,sort_order:number,children:Array}>>}
 */
export function listDepartments() {
  return get('/api/org/departments');
}

/* ------------------------------------------------------------------ 用户 */

/**
 * 新增用户（8.3 POST）。
 * @param {object} payload { username, display_name, department_id, role_ids, status, password }
 */
export function createUser(payload) {
  return post('/api/org/users', payload);
}

/**
 * 编辑用户（8.3 PUT）。
 *
 * 后端按「未传即不改」处理（见 org.py 的 update_user 哨兵实现），因此：
 *   - 启停用：只传 { status }
 *   - 重置密码：只传 { password }
 *   - 基本信息：只传 { display_name, department_id, role_ids, status }
 * 用同一个接口是文档 8.3 明确的语义承载方式，不是自造端点。
 *
 * @param {number} userId 目标用户 id
 * @param {object} payload 需要变更的字段子集
 */
export function updateUser(userId, payload) {
  return put(`/api/org/users/${userId}`, payload);
}

/* ------------------------------------------------------------------ 角色 */

/**
 * 角色列表（8.3 GET）。
 * @returns {Promise<Array<{id:number,role_name:string,role_code:string,description:string,
 *   permissions:Array<{permission_code:string,permission_type:string}>}>>}
 */
export function listRoles() {
  return get('/api/org/roles');
}

/**
 * 配置角色操作权限（8.3 POST，全量覆盖）。
 * @param {number} roleId 角色 id
 * @param {Array<{permission_code:string,permission_type:string}>} permissions 完整权限集合
 */
export function setRolePermissions(roleId, permissions) {
  return post(`/api/org/roles/${roleId}/permissions`, { permissions });
}
