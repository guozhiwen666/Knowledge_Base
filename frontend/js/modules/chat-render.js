/**
 * AI 对话工作台 · 渲染层
 *
 * 从 chat-module.js 拆出，负责把「一轮问答」的状态对象渲染成 HTML：
 *   用户气泡 / FAQ 命中标记 / AI 回答（Markdown）/
 *   知识引用来源卡片 / 权限缺失提示卡片
 *
 * 渲染层只读状态、不碰网络与事件，因此可以独立成文件并单独调试。
 * 所有插入模板的动态值一律经过 esc() 转义；回答正文交给 renderMarkdown()
 * （其内部先整体转义再做标记替换，保证不产生可执行的 HTML）。
 */

import { esc } from '../core/dom.js';
import { renderMarkdown } from '../core/markdown.js';

/**
 * 渲染一轮问答。
 * @param {object} turn 轮次状态，字段含义见 chat-module.js 的 send()
 * @param {number} index 轮次序号（用作 DOM 定位锚点，流式增量更新时按它定位）
 * @returns {string} HTML 片段
 */
export function renderTurnHtml(turn, index) {
  const parts = [];

  // ---- 第 1 部分：用户提问气泡 ----
  parts.push(`
    <div class="chat-msg user">
      <div class="avatar">我</div>
      <div class="bubble">${esc(turn.question)}</div>
    </div>
  `);

  // ---- 第 2 部分：FAQ 缓存命中标记（faq_hit 事件） ----
  if (turn.faqHit) {
    parts.push(renderFaqHit(turn));
  }

  // ---- 第 3 部分：AI 回答正文 ----
  parts.push(renderAnswer(turn));

  // ---- 第 4 部分：知识引用来源卡片（citation 事件） ----
  if (turn.citations && turn.citations.length) {
    parts.push(renderCitations(turn.citations));
  }

  // ---- 第 5 部分：权限缺失提示卡片（permission_notice 事件） ----
  if (turn.notice) {
    parts.push(renderPermissionNotice(turn));
  }

  return `<div class="chat-turn" data-turn="${index}">${parts.join('')}</div>`;
}

/** FAQ 命中提示：命中缓存意味着没有调用大模型，Token 消耗为 0（4.4） */
function renderFaqHit(turn) {
  return `
    <div class="chat-msg">
      <div class="avatar">AI</div>
      <div class="bubble">
        <span class="tag tag-success">命中 FAQ 缓存</span>
        <span class="mute-sm">未调用大模型直答，Token 消耗为 0</span>
        ${turn.faqQuestion ? `<div class="mute-sm mt8">缓存问题：${esc(turn.faqQuestion)}</div>` : ''}
      </div>
    </div>
  `;
}

/** AI 回答气泡：Markdown 正文 + 流式光标 + 错误提示 + 结束统计 */
function renderAnswer(turn) {
  const answerHtml = renderMarkdown(turn.answer || '');

  // 403 是「有登录态但无 AI 访问操作权限」，按 4.9 的降级路由给出定向文案
  const errorHtml = turn.error
    ? `<div class="form-error mt8">${
        turn.error.status === 403
          ? '无 AI 问答访问权限（403）：请联系管理员为你的角色分配 <code>ai:chat:access</code>。'
          : esc(turn.error.message)
      }</div>`
    : '';

  const doneHtml = turn.done
    ? `<div class="mute-sm mt8">
         总 Token：${esc(turn.totalTokens ?? '-')} · 响应耗时：${esc(turn.responseTimeMs ?? '-')} ms
       </div>`
    : '';

  return `
    <div class="chat-msg">
      <div class="avatar">AI</div>
      <div class="bubble">
        <div class="md">${answerHtml || '<span class="mute-sm">（等待回答…）</span>'}</div>
        ${turn.streaming ? '<span class="stream-caret"></span>' : ''}
        ${errorHtml}
        ${doneHtml}
      </div>
    </div>
  `;
}

/** 知识引用来源卡片：序号与 A5 组装 Context 的序号一一对应（5.5） */
function renderCitations(citations) {
  return `
    <div class="chat-msg">
      <div class="avatar">AI</div>
      <div class="bubble">
        <div class="mute-sm">知识引用来源（${citations.length} 条）</div>
        <div class="citation-list">
          ${citations
            .map(
              (item) => `
            <div class="citation-card">
              <div class="head">
                <span class="idx">${esc(item.index ?? '-')}</span>
                <strong>${esc(item.title || `知识单元 #${item.unit_id}`)}</strong>
                <span class="tag">unit_id ${esc(item.unit_id)}</span>
                ${
                  item.score !== undefined && item.score !== null
                    ? `<span class="mute-sm">相似度 ${esc(Number(item.score).toFixed(4))}</span>`
                    : ''
                }
              </div>
            </div>`,
            )
            .join('')}
        </div>
      </div>
    </div>
  `;
}

/**
 * 权限缺失提示卡片（2.9.4 第 3 条 / 9.4）。
 *
 * 文案必须明确写出「缺失访问权限」。这里**只展示被拒单元的标题与 id**，
 * 绝不展示其正文片段 —— 后端 A6 也只下发标题，前端不做任何越界补充。
 */
function renderPermissionNotice(turn) {
  const units = turn.noticeUnits || [];
  return `
    <div class="chat-msg">
      <div class="avatar">AI</div>
      <div class="bubble">
        <div class="permission-notice">
          <div class="title">权限缺失提示</div>
          <div>${esc(turn.notice)}</div>
          ${
            units.length
              ? `<ul>${units
                  .map(
                    (unit) =>
                      `<li>${esc(unit.title || `知识单元 #${unit.unit_id}`)}（unit_id ${esc(unit.unit_id)}）</li>`,
                  )
                  .join('')}</ul>`
              : ''
          }
          <div class="mute-sm mt8">
            说明：这些知识单元与你的问题相关，但你的账号缺失对应的数据权限，因此其中内容未参与本次回答（仅提示单元标题，不展示正文）。
          </div>
        </div>
      </div>
    </div>
  `;
}

/** 空对话时的引导态（9.4：空态不留白） */
export function renderEmptyChat() {
  return `
    <div class="empty-state">
      <span class="ico">[ ]</span>
      还没有对话，输入问题开始提问
      <div class="mute-sm mt8">回答将基于你有权限访问的知识单元生成；被权限拦截的来源会单独提示。</div>
    </div>`;
}
