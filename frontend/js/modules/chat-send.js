/**
 * AI 对话工作台 · 一次提问的生命周期
 *
 * 从 chat-module.js 拆出。负责「落一轮状态 → 起 SSE → 收尾」这条链，
 * 页面文件只提供回调，不关心事件细节。
 *
 * 分工：
 *   chat-stream.js  单个 SSE 事件 → 轮次状态（纯数据转换）
 *   chat-send.js    一次提问的完整流程与流的中断管理（本文件）
 *   chat-module.js  页面状态、DOM 渲染与交互
 *
 * 「当前进行中的流」放在模块级：它是全局单例性质的东西（同一时刻只允许一条流），
 * 放在页面状态里反而容易被重渲染忘掉中断，造成连接挂在后台。
 */

import { chatStream } from '../api/ai.js';
import { applyStreamEvent } from './chat-stream.js';

/** 当前进行中的流（页面切走或用户点停止都必须中断） */
let activeStream = null;

// 路由切走时中断流：模块只加载一次，这里注册一次全局监听即可
window.addEventListener('hashchange', () => {
  if (activeStream) {
    activeStream.abort();
    activeStream = null;
  }
});

/** 中断当前流（无流时安全返回） */
export function stopActiveStream() {
  if (!activeStream) return;
  activeStream.abort();
  activeStream = null;
}

/**
 * 发起一轮提问。
 *
 * @param {object} opts
 *   session      目标会话对象（{ id, turns }），新轮次会被就地追加
 *   question     提问文本
 *   onState      (pending:boolean) => void 生成状态变化（页面据此切换按钮与提示）
 *   onPatch      (turn) => void 增量重渲染单轮（每个 delta 都调用）
 *   onSessionId  (id:string) => void 服务端为新建会话下发的 session_id（4.7）
 *   onSettled    (turn) => void 本轮结束（成功或失败）后的收尾
 * @returns {object} 新落的轮次对象
 */
export function startTurn({ session, question, onState, onPatch, onSessionId, onSettled }) {
  // 第 1 步：落一轮新问答，先渲染成「等待中」
  const turn = {
    question,
    answer: '',
    citations: [],
    notice: '',
    noticeUnits: [],
    faqHit: false,
    streaming: true,
    done: false,
    error: null,
  };
  session.turns.push(turn);
  if (onState) onState(true);

  // 第 2 步：发起 SSE，事件处理严格按 4.8 的七种事件分派
  activeStream = chatStream({
    question,
    sessionId: session.id,

    onEvent: (eventName, data) => {
      // 事件 → 状态的全部映射逻辑在 chat-stream.js
      const effect = applyStreamEvent(turn, eventName, data);
      // session 事件会改动会话标识，需要同步刷新左侧列表与右上角标签
      if (effect.sessionId && onSessionId) onSessionId(effect.sessionId);
      if (onPatch) onPatch(turn);
    },

    onClose: () => {
      // 流正常结束：收尾光标、按钮状态与错误兜底
      turn.streaming = false;
      activeStream = null;
      if (!turn.answer && !turn.error) {
        turn.error = { message: '连接已结束，未收到回答内容', status: 0 };
      }
      if (onState) onState(false);
      if (onPatch) onPatch(turn);
      if (onSettled) onSettled(turn);
    },

    onError: (error) => {
      // code 用于区分 401 / 403（4.9 的异常路由），渲染时据此给不同文案
      turn.streaming = false;
      turn.error = { message: error.message || '流式请求失败', status: error.status || 0 };
      activeStream = null;
      if (onState) onState(false);
      if (onPatch) onPatch(turn);
      if (onSettled) onSettled(turn);
    },
  });

  return turn;
}
