/**
 * 知识沉淀接口封装
 *
 * 覆盖 8.8 接口清单中的第 19~21 个接口：
 *   19. `GET  /api/settlement/faqs/recommendations`   —— 待审核 FAQ 推荐列表
 *   20. `POST /api/settlement/faqs/{id}/review`       —— FAQ 审核（approve / reject）
 *   21. `GET  /api/settlement/knowledge-gaps`         —— 知识缺口列表
 *
 * 并封装第 14 章 #6 / #7 补齐的四个接口：
 *   `GET  /api/settlement/faqs`                         已发布 FAQ 库（分页）
 *   `POST /api/settlement/faqs/{id}/offline`            已发布 FAQ 下线
 *   `POST /api/settlement/knowledge-gaps/{id}/resolve`  缺口补全（关联已有单元 / 一键建档）
 *   `POST /api/settlement/knowledge-gaps/{id}/ignore`   缺口忽略
 *
 * 字段名与 backend/api/settlement.py、services/FAQ_cache_ervice/faq_library.py 逐项对齐。
 */

import { get, post } from './client.js';

/* ------------------------------------------------------------------- FAQ */

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
 * FAQ 库分页查询（第 14 章 #6），按命中次数（hit_count）降序。
 *
 * status 的两种「空」语义要分清：传空串 `''` 表示**不限状态**（前端「全部」标签页），
 * 不传时后端默认只看 `published`。client 默认丢弃空串参数，因此这里用第三个参数
 * 显式声明保留 `status`。
 *
 * @param {object} params { status, keyword, page, page_size }
 * @returns {Promise<{total:number, items:Array, page:number, page_size:number}>}
 *   items 元素：{ id, question, answer, category, related_unit_id, source_type,
 *                 status, hit_count, reviewer_id, reviewed_at, created_at }
 */
export function listFaqs(params = {}) {
  const { status = 'published', keyword, page = 1, page_size = 20 } = params;
  return get('/api/settlement/faqs', { status, keyword, page, page_size }, ['status']);
}

/**
 * 已发布 FAQ 下线（第 14 章 #6）。
 *
 * 状态落到 `rejected` —— 2.9.7 的 faqs.status 只有 pending_review / published /
 * rejected 三态，没有独立的「已下线」取值；后端同时让 FAQ 缓存失效。
 *
 * @param {number} faqId
 * @returns {Promise<{id:number, question:string, status:string, reviewed_at:string}>}
 */
export function offlineFaq(faqId) {
  return post(`/api/settlement/faqs/${faqId}/offline`, {});
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

/* ------------------------------------------------------------------- 缺口 */

/**
 * 知识缺口列表（8.7 GET）。
 * @returns {Promise<Array<{id:number, question_pattern:string, ask_count:number,
 *   last_asked_at:string|null, status:string,
 *   resolved_unit_id:number|null, sample_questions:string[]}>>}
 *   status 取值：unresolved / resolved / ignored（models/enums.py 的 KnowledgeGapStatus）
 */
export function listKnowledgeGaps() {
  return get('/api/settlement/knowledge-gaps');
}

/**
 * 补全知识缺口（第 14 章 #7 / 11.4）。
 *
 * 两种用法二选一：
 *   传 `unit_id`         → 直接关联已有知识单元；
 *   不传 `unit_id`       → **一键建档**：用 `title`（缺省为缺口的问题模式）作标题、
 *                          `content`（缺省为样本提问拼出的骨架）作正文，新建单元后关联。
 * 建档需要向量化，MinIO / Milvus 未就绪时后端返回 503。
 *
 * @param {number} gapId
 * @param {object} [payload] { unit_id?, title?, content?, category? }
 * @returns {Promise<{id:number, status:string, resolved_unit_id:number,
 *   created_unit_code:string|null}>} created_unit_code 仅一键建档时有值
 */
export function resolveGap(gapId, payload = {}) {
  return post(`/api/settlement/knowledge-gaps/${gapId}/resolve`, payload);
}

/**
 * 忽略知识缺口（第 14 章 #7）。记录保留但不再纳入未解决清单。
 * @param {number} gapId
 * @returns {Promise<{id:number, status:string}>}
 */
export function ignoreGap(gapId) {
  return post(`/api/settlement/knowledge-gaps/${gapId}/ignore`, {});
}
