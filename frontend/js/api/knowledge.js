/**
 * 知识单元与数据权限接口封装
 *
 * 覆盖 8.8 接口清单中的第 7~13 个接口：
 *   7.  `POST   /api/knowledge/import`                    —— 单文件/多文件批量导入
 *   8.  `GET    /api/knowledge/units`                     —— 知识单元分页列表
 *   9.  `GET    /api/knowledge/units/{id}`                —— 知识单元详情 + 已配数据权限
 *   10. `PUT    /api/knowledge/units/{id}`                —— 知识单元更新
 *   11. `POST   /api/knowledge/units/{id}/permissions`    —— 数据权限全量覆盖配置
 *   12. `DELETE /api/knowledge/units`                     —— 批量删除
 *   13. `POST   /api/knowledge/check-permissions`         —— 四维鉴权（AI 链路核心接口）
 *
 * 并封装第 14 章 #1 / #2 / #3 补齐的三个接口：
 *   `POST /api/knowledge/units`                手工新建知识单元
 *   `PUT  /api/knowledge/units/{id}/status`    状态流转（版本管理确认为「仅状态流转」）
 *   `GET  /api/knowledge/import/progress`      解析任务进度轮询
 */

import { del, get, post, put, upload } from './client.js';

/* ------------------------------------------------------------------ 导入 */

/**
 * 批量导入（8.4 POST，multipart/form-data）。
 *
 * 请求字段：`files`（可多个）、`category`（可选默认分类）。
 * 响应字段：`{ accepted: [{file_name, task_id}], rejected: [{file_name, reason}] }`
 *
 * **解析已改为后台异步**：本请求只做接收与扩展名校验，因此响应很快返回、
 * 且只给出任务标识；入库结果（unit_id / unit_code / chunk_count）要从
 * `getImportProgress()` 轮询取得。rejected 的 reason 为 `unsupported_format`。
 *
 * @param {File[]} files 待导入文件
 * @param {string} category 统一分类，可为空
 * @param {(percent:number)=>void} onProgress 上传字节进度（0~100）
 */
export function importDocuments(files, category, onProgress) {
  // 第 1 步：组装 multipart 表单。字段名严格按 8.4 —— 多个文件共用 'files' 键
  const formData = new FormData();
  files.forEach((file) => formData.append('files', file, file.name));
  if (category) formData.append('category', category);

  // 第 2 步：交给带进度的上传分支
  return upload('/api/knowledge/import', formData, onProgress);
}

/**
 * 解析任务进度查询（第 14 章 #3）。
 *
 * @param {string[]} taskIds 导入响应里 accepted[].task_id
 * @returns {Promise<{items:Array, missing_task_ids:string[], all_finished:boolean}>}
 *   items 元素：{ task_id, file_name, status, stage, percent,
 *                 unit_id, unit_code, chunk_count, reason }
 *   status ∈ queued | running | completed | failed（后两者为终态）
 *   stage  ∈ queued | starting | parsing | chunking | embedding | indexing | completed | failed
 *   percent 为 0~100
 */
export function getImportProgress(taskIds) {
  const ids = (Array.isArray(taskIds) ? taskIds : [taskIds]).filter(Boolean);
  return get('/api/knowledge/import/progress', { task_ids: ids.join(',') });
}

/* ------------------------------------------------------------------ 列表 */

/**
 * 知识单元分页查询（8.4 GET）。
 * @param {object} params { title, category, status, page, page_size }
 * @returns {Promise<{total:number, items:Array}>}
 *   items 元素：{ id, unit_code, title, category, file_type,
 *                permission_summary, creator_id, updated_at, status }
 */
export function listUnits(params = {}) {
  const { title, category, status, page = 1, page_size = 10 } = params;
  return get('/api/knowledge/units', { title, category, status, page, page_size });
}

/**
 * 手工新建知识单元（第 14 章 #2）。
 *
 * 正文非空时后端会同步切片并写入向量库（否则新建出来的单元检索不到），
 * 因此该接口依赖 MinIO / Milvus 就绪，未就绪时返回 503。
 *
 * @param {object} payload { title, content?, category?, summary?, status? }
 * @returns {Promise<{id:number, unit_code:string, title:string,
 *   category:string|null, status:string, created_at:string}>}
 */
export function createUnit(payload) {
  return post('/api/knowledge/units', payload);
}

/* ------------------------------------------------------------------ 详情 */

/**
 * 知识单元详情（8.4 GET），响应额外带 `permissions[{target_type, target_id, target_name}]`。
 */
export function getUnit(unitId) {
  return get(`/api/knowledge/units/${unitId}`);
}

/**
 * 更新知识单元（8.4 PUT）。
 * 后端只接收 title / content / category / summary；`tags` 与 `attachments`
 * 在 2.9.7 的表结构里没有对应列，后端刻意不接收（见 knowledge.py 模块说明），
 * 因此前端也不提交这两个字段，避免出现「接口答应了却存不下来」的假成功。
 *
 * @param {number} unitId
 * @param {object} payload { title, content, category, summary }
 */
export function updateUnit(unitId, payload) {
  return put(`/api/knowledge/units/${unitId}`, payload);
}

/**
 * 知识单元状态流转（第 14 章 #1：版本管理确认为「仅状态流转」）。
 *
 * @param {number} unitId
 * @param {string} status 后端按白名单校验，目前只接受 `active`，其余取值返回 422
 * @returns {Promise<{id:number, status:string, updated_at:string}>}
 */
export function updateUnitStatus(unitId, status) {
  return put(`/api/knowledge/units/${unitId}/status`, { status });
}


/**
 * 配置知识单元的数据权限（8.4 POST，全量覆盖）。
 *
 * @param {number} unitId
 * @param {Array<{target_type:'global'|'department'|'role'|'user', target_id:number}>} permissions
 *   global 的 target_id 固定为 0（6.2 四维模型）
 */
export function setUnitPermissions(unitId, permissions) {
  return post(`/api/knowledge/units/${unitId}/permissions`, { permissions });
}

/**
 * 批量删除知识单元（8.4 DELETE）。
 * @param {number[]} unitIds
 */
export function deleteUnits(unitIds) {
  return del('/api/knowledge/units', { unit_ids: unitIds });
}

/* ------------------------------------------------------ 数据权限校验接口 */

/**
 * 校验用户对一组知识单元的访问权限（8.4 POST，AI 链路核心接口）。
 *
 * 前端仅用于「权限配置结果自检」的只读演示；7.1 分层职责明确前端不含权限判定逻辑，
 * 真正的鉴权由后端在问答链路内部完成。
 *
 * @param {number} userId
 * @param {number[]} unitIds
 * @returns {Promise<{authorized_unit_ids:number[], unauthorized_unit_ids:number[]}>}
 */
export function checkPermissions(userId, unitIds) {
  return post('/api/knowledge/check-permissions', { user_id: userId, unit_ids: unitIds });
}
