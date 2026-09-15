/**
 * 模块五：AI 对话鉴权工作台
 * （对应 9.1 第五个模块 / 页面 2.9.3-4 AI 对话鉴权工作台）
 *
 * 本文件只保留页面状态、DOM 渲染与交互，其余按职责拆开：
 *   chat-render.js   一轮问答 / 对话区 / 历史回放说明条的渲染
 *   chat-stream.js   单个 SSE 事件 → 轮次状态（纯数据转换）
 *   chat-send.js     一次提问的完整生命周期与流的中断管理
 *   chat-history.js  左侧会话栏的取数与字段口径转换
 *
 * 左侧两段会话的区别（界面上如实标注，不混为一谈）：
 *   本地会话 —— 本次浏览器会话内新建的会话，轮次只在前端内存里，刷新即丢失；
 *   历史对话 —— `GET /api/ai/conversations` 的真实数据（第 14 章 #4），
 *               点击后回放该会话的全部问答明细（只读，可随时切回当前会话）。
 */

import { getUser, displayName } from '../core/store.js';
import { el, esc, $, toast } from '../core/dom.js';
import { renderStreamHtml, renderTurnHtml } from './chat-render.js';
import { startTurn, stopActiveStream } from './chat-send.js';
import {
  fetchConversationTurns,
  fetchHistoryList,
  historyListHtml,
  localSessionsHtml,
  newLocalSessionId,
} from './chat-history.js';

/** 渲染 AI 对话工作台 */
export function renderChat() {
  // ---- 会话内状态：多个本地会话各自保存自己的轮次 ----
  const state = {
    sessions: [], // [{ id, title, turns: [] }]
    activeId: null,
    pending: false,
    // 服务端历史会话列表
    history: { items: [], total: 0, loading: false, error: '', viewSessionId: '' },
    // 历史会话回放视图；非空时右侧展示该会话的问答明细（只读）
    view: null,
  };

  const container = el(`
    <div class="chat-layout">
      <div class="chat-side" data-role="side">
        <button class="btn btn-primary btn-block btn-sm" type="button" data-role="new-session">新建会话</button>

        <div class="nav-group-title" style="padding-left:2px">本次浏览器会话</div>
        <div data-role="session-list"></div>
        <div class="mute-sm" style="padding-left:2px">轮次只在前端内存里，刷新页面即丢失</div>

        <div class="nav-group-title" style="padding-left:2px">
          历史对话（服务端）
          <button class="btn-link" type="button" data-role="reload-history">刷新</button>
        </div>
        <div data-role="history-list"></div>
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

  const sideBox = $('[data-role="side"]', container);
  const streamBox = $('[data-role="stream"]', container);
  const sessionList = $('[data-role="session-list"]', container);
  const historyList = $('[data-role="history-list"]', container);
  const inputBox = $('[data-role="input"]', container);
  const sendBtn = $('[data-role="send"]', container);
  const stopBtn = $('[data-role="stop"]', container);
  const statusBox = $('[data-role="status"]', container);
  const sessionTag = $('[data-role="session-tag"]', container);

  const activeSession = () => state.sessions.find((session) => session.id === state.activeId) || null;

  /** 输入区状态：历史回放与生成中都不可发送 */
  const syncComposer = () => {
    const locked = state.pending || Boolean(state.view);
    sendBtn.disabled = locked;
    inputBox.disabled = locked;
    if (state.view) statusBox.textContent = '历史会话为只读回放，点「回到当前会话」后可继续提问';
    else if (state.pending) statusBox.textContent = '正在生成…';
    else statusBox.textContent = '';
  };

  /** 左侧两段列表 + 右侧对话流 + 会话标识一起刷新 */
  const renderAll = () => {
    sessionList.innerHTML = localSessionsHtml(state.sessions, state.activeId, state.history.viewSessionId);
    historyList.innerHTML = historyListHtml(state.history);
    streamBox.innerHTML = renderStreamHtml(activeSession(), state.view);
    sessionTag.textContent = state.view
      ? `历史 session_id：${state.view.sessionId}`
      : state.activeId
        ? `session_id：${state.activeId}`
        : '';
    syncComposer();
  };

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
    else streamBox.innerHTML = renderStreamHtml(session, null);
    // 保持滚动条贴底，流式输出时用户不需要手动拖
    streamBox.scrollTop = streamBox.scrollHeight;
  };

  /* ------------------------------------------------------- 会话管理 */

  const createSession = () => {
    const session = { id: newLocalSessionId(), title: '新会话', turns: [] };
    state.sessions.unshift(session);
    state.activeId = session.id;
    state.view = null;
    state.history.viewSessionId = '';
    renderAll();
    return session;
  };

  /** 拉服务端历史会话列表（只含当前用户自己发起的会话） */
  const reloadHistory = async () => {
    state.history.loading = true;
    renderAll();
    try {
      const data = await fetchHistoryList();
      state.history.items = data.items;
      state.history.total = data.total;
      state.history.error = '';
    } catch (error) {
      state.history.items = [];
      state.history.error = error.message || '加载失败';
    }
    state.history.loading = false;
    renderAll();
  };

  /** 打开一个历史会话并回放其问答明细（只读） */
  const openHistory = async (sessionId) => {
    if (state.pending) {
      toast('当前回答生成中，请先停止再查看历史会话', 'warn');
      return;
    }
    try {
      const data = await fetchConversationTurns(sessionId);
      state.view = { sessionId, turns: data.turns, total: data.total };
      state.history.viewSessionId = sessionId;
      renderAll();
      streamBox.scrollTop = 0; // 回放从第一轮看起
    } catch (error) {
      // 会话不属于当前用户时后端返回 404（避免探测），原样提示
      toast(error.message || '加载历史会话失败', 'error');
    }
  };

  // 左侧栏统一事件委托：本地会话切换 / 历史会话回放 / 刷新历史列表
  sideBox.addEventListener('click', (event) => {
    const local = event.target.closest('[data-session]');
    if (local) {
      if (state.pending) {
        toast('当前回答生成中，请先停止再切换会话', 'warn');
        return;
      }
      state.activeId = local.dataset.session;
      state.view = null;
      state.history.viewSessionId = '';
      renderAll();
      return;
    }

    const history = event.target.closest('[data-history]');
    if (history) {
      openHistory(history.dataset.history);
      return;
    }

    if (event.target.closest('[data-role="reload-history"]')) reloadHistory();
  });

  $('[data-role="new-session"]', container).addEventListener('click', () => {
    if (state.pending) {
      toast('当前回答生成中，请先停止', 'warn');
      return;
    }
    createSession();
  });

  // 历史回放视图里的「回到当前会话」（对话区整体重渲染，用容器级委托接住按钮）
  streamBox.addEventListener('click', (event) => {
    if (!event.target.closest('[data-role="exit-history"]')) return;
    state.view = null;
    state.history.viewSessionId = '';
    renderAll();
    inputBox.focus();
  });

  /* ------------------------------------------------------- 提问 */

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
    if (state.view) {
      toast('历史会话为只读回放，请先回到当前会话', 'warn');
      return;
    }

    // 第 2 步：确保有会话，并用问题作为本地会话标题
    const session = activeSession() || createSession();
    if (session.title === '新会话') session.title = question.slice(0, 18);

    // 第 3 步：交给 chat-send.js 走完一轮（落轮次 → SSE → 收尾）
    inputBox.value = '';
    startTurn({
      session,
      question,
      onState: (pending) => {
        state.pending = pending;
        stopBtn.disabled = !pending;
        renderAll();
      },
      onPatch: patchStreamingTurn,
      onSessionId: (sessionId) => {
        session.id = sessionId;
        state.activeId = sessionId;
        renderAll();
      },
      onSettled: (turn) => {
        statusBox.textContent = turn.done ? '已完成' : '已结束';
        // 本轮已落库（11.1），刷新历史列表让它出现在左侧
        reloadHistory();
      },
    });
  };

  /* ------------------------------------------------------- 交互绑定 */

  sendBtn.addEventListener('click', send);

  stopBtn.addEventListener('click', () => {
    stopActiveStream();
    state.pending = false;
    stopBtn.disabled = true;
    const session = activeSession();
    const last = session && session.turns[session.turns.length - 1];
    if (last) {
      last.streaming = false;
      if (!last.answer) last.error = { message: '已手动停止生成', status: 0 };
      patchStreamingTurn(last);
    }
    syncComposer();
    statusBox.textContent = '已手动停止';
  });

  inputBox.addEventListener('keydown', (event) => {
    // Ctrl + Enter 发送（Enter 保留为换行，长问题编辑更顺手）
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send();
    }
  });

  // 初始化：一个空会话 + 拉一次历史对话
  createSession();
  reloadHistory();
  return container;
}
