/**
 * AI 对话接口封装
 *
 * 覆盖 8.8 接口清单中的第 14 个接口：
 *   14. `POST /api/ai/chat/stream` —— SSE 流式鉴权问答
 *
 * 该接口不能用普通 request 封装（响应是 text/event-stream，不能整体 JSON.parse），
 * 因此这里只负责拼出完整地址，实际的流读取与帧解析在 core/sse.js。
 *
 * 并封装第 14 章 #4 补齐的两个历史对话接口：
 *   `GET /api/ai/conversations`                 历史会话列表（分页 + 关键词）
 *   `GET /api/ai/conversations/{session_id}`    会话内问答明细
 *
 * 会话归属：两个接口都只返回**当前用户自己发起**的会话；别人的会话按「不存在」
 * 处理并返回 404（后端刻意用 404 而非 403，避免通过状态码探测会话是否存在）。
 */

import { BASE_URL, get } from './client.js';
import { streamSSE } from '../core/sse.js';

/** 流式问答的完整地址 */
export function chatStreamUrl() {
  return `${BASE_URL}/api/ai/chat/stream`;
}

/**
 * 发起一次流式问答（8.5）。
 *
 * @param {object} opts
 *   question   用户提问（8.5 请求字段）
 *   sessionId  会话标识，可为空；为空时服务端会新建并通过 `session` 事件下发（4.7）
 *   onEvent    (eventName, data) => void，事件名见 4.8：
 *              session / faq_hit / citation / permission_notice / delta / done / error
 *   onClose    流正常结束
 *   onError    HTTP 或网络异常
 * @returns {{abort: Function}} 中断句柄（页面切走必须调用，否则连接会挂着）
 */
export function chatStream({ question, sessionId, onEvent, onClose, onError }) {
  // 第 1 步：请求字段严格按 8.5，只提交 question 与 session_id
  const body = { question };
  if (sessionId) body.session_id = sessionId;

  // 第 2 步：交给通用 SSE 客户端
  return streamSSE({ url: chatStreamUrl(), body, onEvent, onClose, onError });
}

/**
 * 历史会话列表（第 14 章 #4）。
 *
 * 数据源是 qa_access_logs 按 session_id 的聚合（11.1），
 * 每个会话取最后一轮的提问作为摘要 —— 列表里只放一个 session_id 对使用者没有意义。
 *
 * @param {object} params { keyword, page, page_size }
 * @returns {Promise<{total:number, items:Array, page:number, page_size:number}>}
 *   items 元素：{ session_id, last_question, round_count, last_asked_at }
 */
export function listConversations(params = {}) {
  const { keyword, page = 1, page_size = 20 } = params;
  return get('/api/ai/conversations', { keyword, page, page_size });
}

/**
 * 会话内问答明细（第 14 章 #4），按时间正序。
 *
 * @param {string} sessionId
 * @returns {Promise<{session_id:string, total:number, items:Array}>}
 *   items 元素：{ id, question, answer, cited_units:[{unit_id,title}],
 *                 total_tokens, response_time_ms, created_at }
 *   会话不属于当前用户时抛 404 的 ApiError。
 */
export function getConversation(sessionId) {
  return get(`/api/ai/conversations/${encodeURIComponent(sessionId)}`);
}
