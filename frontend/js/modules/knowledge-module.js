/**
 * 模块三：知识维护与批量导入（路由入口）
 * （对应 9.1 第三个模块 / 页面 2.9.3-3 知识维护与导入页）
 *
 * 本文件只做路由转发，把三块内容分给三个文件，保证每个文件都短小可读：
 *   knowledge-import.js       导入中心（拖拽 + 并发上传 + 进度）
 *   knowledge-unit-list.js   知识单元列表（筛选 / 分页 / 增删改入口）
 *   knowledge-detail.js      知识单元详情与编辑
 *   permission-dialog.js     数据权限配置弹窗（四组实体同一弹窗）
 *
 * 9.3 路由表对应关系：
 *   #/knowledge/import          -> renderImportCenter
 *   #/knowledge/units           -> renderUnitList
 *   #/knowledge/units/:id       -> renderUnitDetail
 */

import { renderImportCenter as renderImportCenterImpl } from './knowledge-import.js';
import { renderUnitList as renderUnitListImpl } from './knowledge-unit-list.js';
import { renderUnitDetail as renderUnitDetailImpl } from './knowledge-detail.js';

/** #/knowledge/import —— 导入中心 */
export function renderImportCenter() {
  return renderImportCenterImpl();
}

/** #/knowledge/units —— 知识单元列表 */
export function renderUnitList() {
  return renderUnitListImpl();
}

/** #/knowledge/units/:id —— 知识单元详情与编辑 */
export function renderUnitDetail(unitId) {
  return renderUnitDetailImpl(unitId);
}
