/**
 * AI 对话工作台 · SSE 事件 → 轮次状态映射
 *
 * 从 chat-module.js 拆出。这里只做纯数据转换：把 4.8 的七种事件
 * 落到「一轮问答」的状态对象上，不碰 DOM、不碰网络。
 * 好处是这块映射可以脱离浏览器单独推演与测试。
 *
 * 事件契约（4.8）：
 *   session           {session_id}                              会话新建时
 *   faq_hit           {faq_id, question}                        命中 FAQ 缓存
 *   citation          {citations: [...]}                        A6 产出引用来源
 *   permission_notice {notice, unauthorized_units: [...]}       存在无权限召回项
 *   delta             {text}                                    A5 每个生成片段
 *   done              {total_tokens, response_time_ms}          结束
 *   error             {code, message}                           失败
 */

/**
 * 把事件应用到轮次状态对象上（原地修改）。
 *
 * @param {object} turn 一轮问答的状态对象（字段见 chat-module.js 的 send）
 * @param {string} eventName 事件名
 * @param {object} data 事件 data 的 JSON 解析结果
 * @returns {{sessionId?: string}} 需要调用方处理的副作用；
 *   目前只有 session 事件需要改动会话列表，因此单独回传
 */
export function applyStreamEvent(turn, eventName, data) {
  switch (eventName) {
    case 'session':
      // 服务端会为自己新建的会话下发 session_id（4.7），前端以服务端为准
      return data && data.session_id ? { sessionId: data.session_id } : {};

    case 'faq_hit':
      // 命中缓存意味着没有调用大模型，Token 消耗为 0（4.4）
      turn.faqHit = true;
      turn.faqQuestion = (data && data.question) || '';
      return {};

    case 'citation':
      turn.citations = (data && data.citations) || [];
      return {};

    case 'permission_notice':
      // 只要 unauthorized_unit_ids 非空后端就必须下发该事件（8.5）
      turn.notice = (data && data.notice) || '部分相关知识单元因权限限制未能提供内容。';
      turn.noticeUnits = (data && data.unauthorized_units) || [];
      return {};

    case 'delta':
      // 累积增量。data 解析失败时 sse.js 会退化成 { raw: 文本 }，这里一并兜住
      turn.answer += (data && (data.text ?? data.raw)) || '';
      return {};

    case 'done':
      turn.done = true;
      turn.totalTokens = data && data.total_tokens;
      turn.responseTimeMs = data && data.response_time_ms;
      return {};

    case 'error':
      // code 用于区分 401 / 403（4.9 的异常路由），渲染时据此给不同文案
      turn.error = {
        message: (data && data.message) || '生成失败',
        status: (data && data.code) || 0,
      };
      return {};

    default:
      // 未知事件忽略，避免服务端多发一个事件就打断渲染
      return {};
  }
}
