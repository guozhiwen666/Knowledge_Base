/**
 * 认证接口封装
 *
 * 覆盖文档 8.8 接口清单中的第 1 个接口：
 *   1. `POST /api/auth/login` —— 登录
 *
 * 全部封装函数都只是「路径 + 方法 + 字段名」的映射，不含业务判断。
 */

import { post } from './client.js';

/**
 * 登录（8.2）。
 * @param {string} username 登录名
 * @param {string} password 口令明文（仅经 HTTPS 传输，前端不落地存储）
 * @returns {Promise<{access_token:string, user_info:object, permissions:string[]}>}
 *   user_info: { id, username, display_name, department_id, department_name, role_ids }
 *   permissions: 权限码数组，驱动动态菜单与按钮级显隐（6.1）
 */
export function login(username, password) {
  return post('/api/auth/login', { username, password });
}
