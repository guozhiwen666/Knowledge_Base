/**
 * 组织架构 · 下拉选项取数（部门 / 角色 / 用户）
 *
 * 部门树要拍平成「带缩进的下拉项」（原生 select 无法表达树），用户表单、部门表单、
 * 权限弹窗三处都要用到同一份规则；角色与用户选项同理。放在一个文件里，
 * 才能保证各处的缩进层级与排序口径一致。
 *
 * 缓存策略：部门与角色在一次页面停留内变化不大，缓存后重复打开弹窗不再发请求；
 * 部门 / 角色的写操作成功后调用 invalidateOptions() 让下一处重新拉取。
 */

import { listDepartments, listRoles, listUsers } from '../api/org.js';

/** 选项缓存；null 表示未加载 */
let cache = null;

/**
 * 把部门树拍平成带层级缩进的下拉项。
 * @param {Array} nodes 部门树（节点含 children）
 * @param {number} depth 递归深度，用于生成缩进
 * @param {Array} out 累积结果
 * @returns {Array<{id:number, name:string, depth:number}>} name 已带全角空格缩进
 */
export function flattenDepartments(nodes, depth = 0, out = []) {
  (nodes || []).forEach((node) => {
    out.push({ id: node.id, name: `${'　'.repeat(depth)}${node.name}`, depth });
    flattenDepartments(node.children, depth + 1, out);
  });
  return out;
}

/**
 * 拉取部门（已拍平）与角色选项，带缓存。
 * 两个接口并行拉取，任一失败则该组降级为空列表 —— 弹窗仍可用，不必整页报错。
 *
 * @param {boolean} force 为 true 时忽略缓存重新拉取
 * @returns {Promise<{departments:Array, roles:Array}>}
 */
export async function loadOptions(force = false) {
  if (cache && !force) return cache;

  const [departments, roles] = await Promise.all([
    listDepartments().catch(() => []),
    listRoles().catch(() => []),
  ]);

  cache = { departments: flattenDepartments(departments), roles: roles || [] };
  return cache;
}

/** 部门或角色变更后清缓存（下一处使用时重新拉取） */
export function invalidateOptions() {
  cache = null;
}

/**
 * 取用户选项，用于「负责人」这类需要选人的下拉。
 *
 * 只取第一页 100 条：接口上限就是 100，且部门负责人这类场景不需要翻页选择。
 * 取不到时返回空数组，让调用方降级为「暂无人员可选」，而不是让弹窗打不开。
 *
 * @returns {Promise<Array<{id:number, username:string, display_name:string, department_name:string}>>}
 */
export async function loadUserOptions() {
  try {
    const data = await listUsers({ page: 1, page_size: 100 });
    return (data && data.items) || [];
  } catch {
    return [];
  }
}
