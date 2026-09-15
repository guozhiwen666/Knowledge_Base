/**
 * 组织架构与权限接口封装
 *
 * 覆盖 8.8 接口清单中的第 2~6 个接口：
 *   2. `GET  /api/org/departments`              —— 部门树
 *   3. `POST /api/org/users`                    —— 用户新增
 *   4. `PUT  /api/org/users/{id}`               —— 用户编辑（承载启停用语义）
 *   5. `GET  /api/org/roles`                    —— 角色列表（含已分配权限）
 *   6. `POST /api/org/roles/{id}/permissions`   —— 角色权限分配（全量覆盖）
 *
 * 并封装第 14 章 #5 补齐的 11 个接口：
 *   用户：列表（分页筛选）/ 详情 / 删除 / 重置口令
 *   部门：新增 / 编辑 / 删除 / 成员列表
 *   角色：新增 / 编辑 / 删除
 *
 * **重置口令为什么是独立接口**：8.3 原文写「重置密码走 PUT 的密码重置语义」，
 * 但 8.3 列的 PUT 请求字段里并没有 password。后端把它拆成独立路由，
 * 响应回一次新口令明文 —— 库里只有哈希，明文只在这一处出现，管理员必须拿到。
 */

import { del, get, post, put } from './client.js';

/* ------------------------------------------------------------------ 部门 */

/**
 * 部门树形列表（8.3）。
 * @returns {Promise<Array<{id:number,parent_id:number|null,name:string,leader_id:number|null,sort_order:number,children:Array}>>}
 */
export function listDepartments() {
  return get('/api/org/departments');
}

/**
 * 新增部门（第 14 章 #5）。
 * @param {object} payload { name, parent_id?, leader_id?, sort_order? }
 *   父部门不存在会返回 422，不会建出一个挂不上树的节点
 * @returns {Promise<{id:number,parent_id:number|null,name:string,leader_id:number|null,sort_order:number}>}
 */
export function createDepartment(payload) {
  return post('/api/org/departments', payload);
}

/**
 * 编辑部门（第 14 章 #5）。字段全部可选，未传即不改。
 * 把部门挂到自己或自己的下级会被后端拒绝（422）。
 * @param {number} departmentId
 * @param {object} payload { name?, parent_id?, leader_id?, sort_order? }
 */
export function updateDepartment(departmentId, payload) {
  return put(`/api/org/departments/${departmentId}`, payload);
}

/**
 * 删除部门（第 14 章 #5）。
 * **不做级联删除**：有子部门或成员时后端返回 422，message 说明原因，界面需原样提示。
 * @param {number} departmentId
 * @returns {Promise<{deleted:number}>}
 */
export function deleteDepartment(departmentId) {
  return del(`/api/org/departments/${departmentId}`);
}

/**
 * 部门成员列表（第 14 章 #5）。成员关系由 `users.department_id` 反查（5.2）。
 * @param {number} departmentId
 * @returns {Promise<{department_id:number,total:number,
 *   items:Array<{id:number,username:string,display_name:string,status:number}>}>}
 */
export function listDepartmentMembers(departmentId) {
  return get(`/api/org/departments/${departmentId}/members`);
}

/* ------------------------------------------------------------------ 用户 */

/**
 * 用户分页列表（第 14 章 #5）。
 * @param {object} params { keyword, department_id, status, page, page_size }
 *   keyword 同时匹配 username 与 display_name；status 为 1 启用 / 0 停用，
 *   传空串表示不限状态（client 会丢弃空串参数，等价于不带该条件）
 * @returns {Promise<{total:number, items:Array, page:number, page_size:number}>}
 *   items 元素：{ id, username, display_name, department_id, department_name,
 *                 status, role_ids, role_names, created_at }
 */
export function listUsers(params = {}) {
  const { keyword, department_id, status, page = 1, page_size = 20 } = params;
  return get('/api/org/users', { keyword, department_id, status, page, page_size });
}

/**
 * 用户详情（第 14 章 #5）。结构与列表项一致，供编辑表单回填。
 * @param {number} userId
 */
export function getUser(userId) {
  return get(`/api/org/users/${userId}`);
}

/**
 * 删除用户（第 14 章 #5）。会清掉其角色关联；指向该用户的单元授权保留。
 * @param {number} userId
 * @returns {Promise<{deleted:number}>}
 */
export function deleteUser(userId) {
  return del(`/api/org/users/${userId}`);
}

/**
 * 重置用户口令（第 14 章 #5）。
 * @param {number} userId
 * @param {string} [newPassword] 不传则使用系统初始口令
 * @returns {Promise<{user_id:number,new_password:string}>}
 *   new_password 为**明文且只返回这一次**，界面必须展示给管理员
 */
export function resetUserPassword(userId, newPassword) {
  const body = newPassword ? { new_password: newPassword } : {};
  return post(`/api/org/users/${userId}/reset-password`, body);
}

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
 *   - 基本信息：只传 { display_name, department_id, role_ids, status }
 * 口令不走这里（见 resetUserPassword）。
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
 * 新增角色（第 14 章 #5）。角色编码重复会返回 422。
 * @param {object} payload { role_name, role_code, description? }
 * @returns {Promise<{id:number,role_name:string,role_code:string,description:string|null}>}
 */
export function createRole(payload) {
  return post('/api/org/roles', payload);
}

/**
 * 编辑角色（第 14 章 #5）。字段全部可选，未传即不改。
 * @param {number} roleId
 * @param {object} payload { role_name?, role_code?, description? }
 */
export function updateRole(roleId, payload) {
  return put(`/api/org/roles/${roleId}`, payload);
}

/**
 * 删除角色（第 14 章 #5）。仍有用户使用该角色时后端返回 422，界面需原样提示。
 * @param {number} roleId
 * @returns {Promise<{deleted:number}>}
 */
export function deleteRole(roleId) {
  return del(`/api/org/roles/${roleId}`);
}

/**
 * 配置角色操作权限（8.3 POST，全量覆盖）。
 * @param {number} roleId 角色 id
 * @param {Array<{permission_code:string,permission_type:string}>} permissions 完整权限集合
 */
export function setRolePermissions(roleId, permissions) {
  return post(`/api/org/roles/${roleId}/permissions`, { permissions });
}
