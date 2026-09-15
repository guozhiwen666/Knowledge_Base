/**
 * AI 对话接口封装
 *
 * 覆盖 8.8 接口清单中的第 14 个接口：
 *   14. `POST /api/ai/chat/stream` —— SSE 流式鉴权问答
 *
 * 该接口不能用普通 request 封装（响应是 text/event-stream，不能整体 JSON.parse），
 * 因此这里只负责拼出完整地址，实际的流读取与帧解析在 core/sse.js。
 *
 * 明确不在本文件内的方法（8 章没有这些接口）：
 *   - 历史对话列表查询（2.9.3 要求展示，但 8 章未列出接口，页面用占位说明）
 */

import { BASE_URL } from './client.js';
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
