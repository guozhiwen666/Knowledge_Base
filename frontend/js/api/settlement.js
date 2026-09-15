/**
 * 知识沉淀接口封装
 *
 * 覆盖 8.8 接口清单中的第 19~21 个接口：
 *   19. `GET  /api/settlement/faqs/recommendations`   —— 待审核 FAQ 推荐列表
 *   20. `POST /api/settlement/faqs/{id}/review`       —— FAQ 审核（approve / reject）
 *   21. `GET  /api/settlement/knowledge-gaps`         —— 知识缺口列表
 *
 * 字段名与 backend/services/knowledge_precipitation_and_mining_service/settlement.py
 * 的 list_recommendations / review_faq / list_knowledge_gaps 逐项对齐。
 *
 * 明确不在本文件内的方法（8 章没有这些接口）：
 *   - 已发布 FAQ 库查询 / 下线（2.9.3 要求展示，接口未列出）
 *   - 知识缺口「一键创建关联知识单元补全」（2.9.3 要求，接口未列出）
 */

import { get, post } from './client.js';

/**
 * 待审核 FAQ 推荐列表（8.7 GET）。
 * @returns {Promise<Array<{id:number, question:string, frequency:number,
 *   related_unit_id:number|null, suggested_answer:string|null}>>}
 *   frequency 为推荐频次（后端由 qa_access_logs 现算）；
 *   suggested_answer 挖掘阶段不生成，通常为 null，由审核界面人工填写。
 */
export function listFaqRecommendations() {
  return get('/api/settlement/faqs/recommendations');
}

/**
 * 审核 FAQ 推荐项（8.7 POST）。
 * @param {number} faqId
 * @param {'approve'|'reject'} action
 * @param {string} [editedAnswer] 管理员编辑后的标准答案；approve 时非空则覆盖原答案
 */
export function reviewFaq(faqId, action, editedAnswer) {
  // 第 1 步：按 8.7 组装请求体，edited_answer 为空时不提交该字段
  const body = { action };
  if (editedAnswer) body.edited_answer = editedAnswer;
  return post(`/api/settlement/faqs/${faqId}/review`, body);
}

/**
 * 知识缺口列表（8.7 GET）。
 * @returns {Promise<Array<{id:number, question_pattern:string, ask_count:number,
 *   last_asked_at:string|null, status:string}>>}
 *   status 取值：unresolved / resolved / ignored（models/enums.py 的 KnowledgeGapStatus）
 */
export function listKnowledgeGaps() {
  return get('/api/settlement/knowledge-gaps');
}
