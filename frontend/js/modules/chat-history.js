/**
 * AI 对话工作台 · 左侧会话栏
 *
 * 从 chat-module.js 拆出，承载左侧两段会话：
 *   1. 本次浏览器会话里的**本地会话**（新建 / 切换；轮次只在前端内存里，
 *      刷新即丢失，这一点在界面上如实标注）；
 *   2. **服务端历史会话**（第 14 章 #4）—— 列表来自 `GET /api/ai/conversations`，
 *      点击后取 `GET /api/ai/conversations/{session_id}` 的问答明细。
 *
 * 本文件只负责「取数 + 生成 HTML + 字段口径转换」，不碰流式问答的状态机
 * （那部分在 chat-module.js）与渲染细节（在 chat-render.js）。
 *
 * 字段口径转换集中在 toHistoryTurn()：服务端明细是
 *   { id, question, answer, cited_units:[{unit_id,title}], total_tokens, response_time_ms, created_at }
 * 而 chat-render.js 认识的是流式问答的 turn 结构（citations / done / totalTokens ...）。
 * 两个口径不一致时只改这一处，渲染层不必知道数据来自实时还是历史。
 */

import { listConversations, getConversation } from '../api/ai.js';
import { esc, fmtDateTime, fmtNumber } from '../core/dom.js';

/** 历史会话一次取多少条（接口上限 100）；侧栏不做分页，取最近一页足够 */
export const HISTORY_PAGE_SIZE = 20;

/** 生成本地会话标识：优先 crypto.randomUUID，兜底用时间戳 + 随机串 */
export function newLocalSessionId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return `sess-${window.crypto.randomUUID()}`;
  }
  return `sess-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

/**
 * 本地会话列表 HTML。
 * @param {Array} sessions [{ id, title, turns }]
 * @param {string} activeId 当前本地会话 id
 * @param {string} viewSessionId 正在回放的历史会话 id（非空时本地列表不高亮）
 */
export function localSessionsHtml(sessions, activeId, viewSessionId) {
  if (!sessions || !sessions.length) return '<div class="mute-sm">暂无本地会话</div>';
  return sessions
    .map(
      (session) => `
    <div class="session-item ${session.id === activeId && !viewSessionId ? 'active' : ''}"
         data-session="${esc(session.id)}" title="${esc(session.title || '新会话')}">
      <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(session.title || '新会话')}</div>
      <div class="mute-sm" style="font-size:11px">${esc(fmtNumber(session.turns.length))} 轮</div>
    </div>`,
    )
    .join('');
}

/**
 * 服务端历史会话列表 HTML（最近提问摘要 + 轮数 + 时间）。
 * @param {{items:Array, loading:boolean, error:string, viewSessionId:string, total:number}} state
 */
export function historyListHtml(state) {
  if (state.loading) return '<div class="mute-sm">加载中…</div>';
  if (state.error) return `<div class="mute-sm">加载失败：${esc(state.error)}</div>`;
  if (!state.items.length) return '<div class="mute-sm">暂无历史会话（提问后会出现在这里）</div>';

  return state.items
    .map(
      (item) => `
    <div class="session-item ${item.session_id === state.viewSessionId ? 'active' : ''}"
         data-history="${esc(item.session_id)}" title="${esc(item.last_question || '')}">
      <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
        ${esc(item.last_question || '（无提问内容）')}
      </div>
      <div class="mute-sm" style="font-size:11px">
        ${esc(fmtNumber(item.round_count))} 轮 · ${esc(fmtDateTime(item.last_asked_at))}
      </div>
    </div>`,
    )
    .join('');
}

/** 历史会话回放视图顶部的说明条在 chat-render.js（渲染层的东西不放在取数层） */

/**
 * 服务端问答明细 → chat-render.js 认识的轮次结构。
 * cited_units 没有相似度分数（历史日志不存 score），因此引用卡片只显示单元标题与编号。
 */
export function toHistoryTurn(item) {
  return {
    question: item.question || '',
    answer: item.answer || '',
    citations: (item.cited_units || []).map((unit, index) => ({
      index: index + 1,
      unit_id: unit.unit_id,
      title: unit.title,
    })),
    notice: '',
    noticeUnits: [],
    faqHit: false,
    streaming: false,
    done: true,
    error: null,
    totalTokens: item.total_tokens,
    responseTimeMs: item.response_time_ms,
  };
}

/**
 * 拉取历史会话列表（第一页）。
 * @returns {Promise<{total:number, items:Array}>}
 */
export async function fetchHistoryList() {
  const data = await listConversations({ page: 1, page_size: HISTORY_PAGE_SIZE });
  return { total: (data && data.total) || 0, items: (data && data.items) || [] };
}

/**
 * 拉取某个会话的全部问答轮。
 * 会话不属于当前用户时后端返回 404（刻意不用 403，避免探测会话是否存在），
 * 这里把错误原样抛给调用方去提示。
 * @returns {Promise<{total:number, turns:Array}>}
 */
export async function fetchConversationTurns(sessionId) {
  const data = await getConversation(sessionId);
  return {
    total: (data && data.total) || 0,
    turns: ((data && data.items) || []).map(toHistoryTurn),
  };
}
