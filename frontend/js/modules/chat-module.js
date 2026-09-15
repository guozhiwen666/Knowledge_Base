/**
 * 模块五：AI 对话鉴权工作台
 * （对应 9.1 第五个模块 / 页面 2.9.3-4 AI 对话鉴权工作台）
 *
 * 本文件负责「状态与网络」，渲染拆在 chat-render.js，事件映射拆在 chat-stream.js。
 *
 * 需求落位：提问输入框；历史对话列表；SSE 流式 Markdown；知识引用来源卡片；
 * 权限缺失提示卡片。七个 SSE 事件的口径见 chat-stream.js 的注释与 4.8。
 *
 * **明确缺失、页面用占位说明的部分**：
 *   服务端历史对话列表（4.7 说明应由 qa_access_logs 按 session_id 反查，
 *   但该查询接口未在文档 8 章列出）。当前实现为当前浏览器会话内的多轮上下文，
 *   刷新页面即丢失，页面上如实标注了这一点。
 */

import { chatStream } from '../api/ai.js';
import { getUser, displayName } from '../core/store.js';
import { el, esc, $, toast } from '../core/dom.js';
import { renderTurnHtml, renderEmptyChat } from './chat-render.js';
import { applyStreamEvent } from './chat-stream.js';

/** 当前进行中的流（页面切走时必须中断，否则连接会一直挂着） */
let activeStream = null;

// 页面切走时中断流：模块只加载一次，这里注册一次全局监听即可
window.addEventListener('hashchange', () => {
  if (activeStream) {
    activeStream.abort();
    activeStream = null;
  }
});

/** 生成会话标识：优先用 crypto.randomUUID，兜底用时间戳 + 随机串 */
function newSessionId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return `sess-${window.crypto.randomUUID()}`;
  }
  return `sess-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

/** 渲染 AI 对话工作台 */
export function renderChat() {
  // ---- 会话内状态：多个会话各自保存自己的轮次 ----
  const state = {
    sessions: [], // [{ id, title, turns: [] }]
    activeId: null,
    pending: false,
  };

  const container = el(`
    <div class="chat-layout">
      <div class="chat-side">
        <button class="btn btn-primary btn-block btn-sm" type="button" data-role="new-session">新建会话</button>
        <div class="nav-group-title" style="padding-left:2px">本次浏览器会话</div>
        <div data-role="session-list"></div>
        <div class="pending-block mt16" style="font-size:12px;padding:9px 10px">
          <span class="pending-title">服务端历史对话</span>
          会话仅保存在当前页面内，刷新即丢失。
          <div class="mute-sm mt8">缺失数据源：按 session_id 反查会话记录的接口（8 章未列出）</div>
        </div>
      </div>

      <div class="chat-main">
        <div class="chat-stream" data-role="stream"></div>
        <div class="chat-composer">
          <div class="row-between mb8">
            <span class="mute-sm" data-role="hint">
              当前登录：${esc(displayName() || '未登录')} · 提问将经过数据权限过滤（6.3）
            </span>
            <span class="mute-sm" data-role="session-tag"></span>
          </div>
          <textarea class="textarea" data-role="input"
                    placeholder="输入你的问题，例如：差旅报销的标准是什么？（Ctrl + Enter 发送）"></textarea>
          <div class="row mt8">
            <button class="btn btn-primary" type="button" data-role="send">发送提问</button>
            <button class="btn" type="button" data-role="stop" disabled>停止生成</button>
            <span class="mute-sm" data-role="status"></span>
          </div>
        </div>
      </div>
    </div>
  `);

  const streamBox = $('[data-role="stream"]', container);
  const sessionList = $('[data-role="session-list"]', container);
  const inputBox = $('[data-role="input"]', container);
  const sendBtn = $('[data-role="send"]', container);
  const stopBtn = $('[data-role="stop"]', container);
  const statusBox = $('[data-role="status"]', container);
  const sessionTag = $('[data-role="session-tag"]', container);

  /* ------------------------------------------------------- 渲染 */

  const renderSessions = () => {
    if (!state.sessions.length) {
      sessionList.innerHTML = '<div class="mute-sm">暂无会话</div>';
      return;
    }
    sessionList.innerHTML = state.sessions
      .map(
        (session) => `
      <div class="session-item ${session.id === state.activeId ? 'active' : ''}" data-session="${esc(session.id)}">
        ${esc(session.title || '新会话')}
      </div>`,
      )
      .join('');
  };

  const renderStream = () => {
    const session = activeSession();
    if (!session || !session.turns.length) {
      streamBox.innerHTML = renderEmptyChat();
      return;
    }
    streamBox.innerHTML = session.turns.map((turn, index) => renderTurnHtml(turn, index)).join('');
  };

  /** 会话切换与新建时统一走这里：左侧列表 + 右侧对话流 + 会话标识一起刷新 */
  const renderAll = () => {
    renderSessions();
    renderStream();
    sessionTag.textContent = state.activeId ? `session_id：${state.activeId}` : '';
  };

  /* ------------------------------------------------------- 会话管理 */

  const activeSession = () => state.sessions.find((session) => session.id === state.activeId) || null;

  const createSession = () => {
    const session = { id: newSessionId(), title: '新会话', turns: [] };
    state.sessions.unshift(session);
    state.activeId = session.id;
    renderAll();
    return session;
  };

  sessionList.addEventListener('click', (event) => {
    const item = event.target.closest('[data-session]');
    if (!item) return;
    if (state.pending) {
      toast('当前回答生成中，请先停止再切换会话', 'warn');
      return;
    }
    state.activeId = item.dataset.session;
    renderAll();
  });

  $('[data-role="new-session"]', container).addEventListener('click', () => {
    if (state.pending) {
      toast('当前回答生成中，请先停止', 'warn');
      return;
    }
    createSession();
  });

  /* ------------------------------------------------------- 流式接收 */

  /**
   * 只更新最后一条 AI 回答的 DOM。
   * 每个 delta 都重建整棵列表会让长对话明显卡顿，因此按 data-turn 定位单轮替换。
   */
  const patchStreamingTurn = (turn) => {
    const session = activeSession();
    if (!session) return;
    const index = session.turns.indexOf(turn);
    if (index === -1) return;
    const node = streamBox.querySelector(`[data-turn="${index}"]`);
    if (node) node.outerHTML = renderTurnHtml(turn, index);
    else renderStream();
    // 保持滚动条贴底，流式输出时用户不需要手动拖
    streamBox.scrollTop = streamBox.scrollHeight;
  };

  const send = () => {
    const question = inputBox.value.trim();
    // 第 1 步：空提问不发；未登录不发（9.3：AI 工作台需登录态）
    if (!question) {
      toast('请输入问题内容', 'warn');
      return;
    }
    if (!getUser()) {
      toast('请先登录', 'warn');
      return;
    }
    if (state.pending) return;

    // 第 2 步：确保有会话
    const session = activeSession() || createSession();
    if (session.title === '新会话') session.title = question.slice(0, 18);

    // 第 3 步：落一轮新问答，先渲染成「等待中」
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
    state.pending = true;
    inputBox.value = '';
    sendBtn.disabled = true;
    stopBtn.disabled = false;
    statusBox.textContent = '正在生成…';
    renderAll();

    // 第 4 步：发起 SSE，事件处理严格按 4.8 的七种事件分派
    activeStream = chatStream({
      question,
      sessionId: session.id,

      onEvent: (eventName, data) => {
        // 事件 → 状态的全部映射逻辑在 chat-stream.js（纯数据转换，可独立推演）
        const effect = applyStreamEvent(turn, eventName, data);
        // session 事件会改动会话标识，需要同步刷新左侧列表与右上角标签
        if (effect.sessionId) {
          session.id = effect.sessionId;
          state.activeId = effect.sessionId;
          renderAll();
        }
        patchStreamingTurn(turn);
      },

      onClose: () => {
        // 流正常结束：收尾光标与按钮状态
        turn.streaming = false;
        state.pending = false;
        activeStream = null;
        sendBtn.disabled = false;
        stopBtn.disabled = true;
        statusBox.textContent = turn.done ? '已完成' : '已结束';
        // 流被中断且没有任何内容时，给一个明确提示而不是留空气泡
        if (!turn.answer && !turn.error) {
          turn.error = { message: '连接已结束，未收到回答内容', status: 0 };
        }
        patchStreamingTurn(turn);
      },

      onError: (error) => {
        turn.streaming = false;
        turn.error = { message: error.message || '流式请求失败', status: error.status || 0 };
        state.pending = false;
        activeStream = null;
        sendBtn.disabled = false;
        stopBtn.disabled = true;
        statusBox.textContent = '请求失败';
        patchStreamingTurn(turn);
      },
    });
  };

  /* ------------------------------------------------------- 交互绑定 */

  sendBtn.addEventListener('click', send);

  stopBtn.addEventListener('click', () => {
    if (activeStream) {
      activeStream.abort();
      activeStream = null;
    }
    state.pending = false;
    sendBtn.disabled = false;
    stopBtn.disabled = true;
    statusBox.textContent = '已手动停止';
    const session = activeSession();
    const last = session && session.turns[session.turns.length - 1];
    if (last) {
      last.streaming = false;
      if (!last.answer) last.error = { message: '已手动停止生成', status: 0 };
      patchStreamingTurn(last);
    }
  });

  inputBox.addEventListener('keydown', (event) => {
    // Ctrl + Enter 发送（Enter 保留为换行，长问题编辑更顺手）
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send();
    }
  });

  // 初始化一个空会话
  createSession();
  return container;
}
