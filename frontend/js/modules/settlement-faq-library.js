/**
 * 知识沉淀 · 已发布 FAQ 库
 *
 * 从 settlement-module.js 拆出（该文件保留页面外壳、推荐审核与缺口两块）。
 *
 * 接口（第 14 章 #6 补齐）：
 *   `GET  /api/settlement/faqs`               分页查询，按 hit_count 降序
 *   `POST /api/settlement/faqs/{id}/offline`  下线（状态落 rejected，缓存同时失效）
 *
 * 「已发布 / 全部」两个标签页对应 status 的两种取值：
 *   published（默认）与空串 —— 空串表示**不限状态**，与「不传」是两种语义，
 *   因此 api/settlement.js 里显式声明保留空参（见 listFaqs）。
 *
 * 下线要做二次确认：它会立刻让缓存失效（下线的意义就是不再命中），
 * 而且 2.9.7 的 status 只有三态，下线后落到 rejected，没有「已下线」这个独立取值。
 */

import { listFaqs, offlineFaq } from '../api/settlement.js';
import {
  el,
  esc,
  $,
  toast,
  confirmDialog,
  emptyState,
  loadingState,
  fmtDateTime,
  fmtNumber,
} from '../core/dom.js';
import { renderPagerHtml } from '../core/pager.js';

/** FAQ 状态展示映射（models/enums.py 的 FaqStatus） */
const FAQ_STATUS = {
  published: { label: '已发布', cls: 'tag-success' },
  pending_review: { label: '待审核', cls: 'tag-warn' },
  rejected: { label: '已驳回 / 已下线', cls: 'tag-danger' },
};

/** 渲染「已发布 FAQ 库」卡片（返回节点，内部自理取数与交互） */
export function renderFaqLibrary() {
  const container = el(`
    <div class="card">
      <div class="card-title">
        <span>FAQ 库</span>
        <div class="row">
          <span class="mute-sm" data-role="total"></span>
        </div>
      </div>

      <div class="row-between mb16">
        <div class="tabs" data-role="tabs">
          <div class="tab active" data-status="published">已发布</div>
          <div class="tab" data-status="">全部</div>
        </div>
        <div class="row">
          <input class="input" data-role="keyword" type="text" placeholder="按标准问题搜索" style="width:200px" />
          <button class="btn" type="button" data-role="search">查询</button>
        </div>
      </div>

      <div data-role="table">${loadingState()}</div>
      <div class="pager" data-role="pager"></div>
      <div class="field-hint mt8">
        按命中次数降序返回；「下线」会让该 FAQ 立刻不再被问答命中（缓存同步失效），
        状态落到 <code>rejected</code>（2.9.7 的三态里没有独立的「已下线」取值）。
      </div>
    </div>
  `);

  const tableBox = $('[data-role="table"]', container);
  const pagerBox = $('[data-role="pager"]', container);
  const totalBox = $('[data-role="total"]', container);
  const tabsBox = $('[data-role="tabs"]', container);

  const state = { status: 'published', keyword: '', page: 1, page_size: 10, total: 0, items: [] };

  /** 拉取 FAQ 库 */
  const load = async () => {
    tableBox.innerHTML = loadingState();
    try {
      const data = await listFaqs({
        status: state.status,
        keyword: state.keyword,
        page: state.page,
        page_size: state.page_size,
      });
      state.total = (data && data.total) || 0;
      state.items = (data && data.items) || [];
    } catch (error) {
      tableBox.innerHTML = `<div class="empty-state">加载失败：${esc(error.message)}</div>`;
      pagerBox.innerHTML = '';
      totalBox.textContent = '';
      return;
    }

    totalBox.textContent = `共 ${fmtNumber(state.total)} 条`;
    tableBox.innerHTML = state.items.length
      ? renderTable(state.items)
      : emptyState(state.status === 'published' ? '暂无已发布的 FAQ（审核通过后写入）' : '暂无 FAQ 记录');
    pagerBox.innerHTML = renderPagerHtml(state.page, state.page_size, state.total);
  };

  // 第 1 步：状态标签页
  tabsBox.addEventListener('click', (event) => {
    const tab = event.target.closest('.tab');
    if (!tab || tab.classList.contains('active')) return;
    tabsBox.querySelectorAll('.tab').forEach((node) => node.classList.remove('active'));
    tab.classList.add('active');
    state.status = tab.dataset.status; // '' 表示不限状态
    state.page = 1;
    load();
  });

  // 第 2 步：关键词查询
  const doSearch = () => {
    state.keyword = $('[data-role="keyword"]', container).value.trim();
    state.page = 1;
    load();
  };
  $('[data-role="search"]', container).addEventListener('click', doSearch);
  $('[data-role="keyword"]', container).addEventListener('keydown', (event) => {
    if (event.key === 'Enter') doSearch();
  });

  // 第 3 步：分页
  pagerBox.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-page]');
    if (!btn || btn.disabled) return;
    state.page += btn.dataset.page === 'next' ? 1 : -1;
    if (state.page < 1) state.page = 1;
    load();
  });

  // 第 4 步：下线（二次确认；失败时如实展示后端 message）
  tableBox.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-act="offline"]');
    if (!btn || btn.disabled) return;
    const faq = state.items.find((item) => String(item.id) === btn.dataset.id);
    if (!faq) return;

    const ok = await confirmDialog(
      `确认让 FAQ「${faq.question}」下线？下线后该问答不会再被命中，状态将变为 rejected。`,
      '下线',
    );
    if (!ok) return;

    btn.disabled = true;
    try {
      const data = await offlineFaq(faq.id);
      toast(`已下线，当前状态：${(data && data.status) || 'rejected'}`, 'success');
      load();
    } catch (error) {
      // 例如「该 FAQ 当前状态为 xxx，只有已发布的才能下线」—— 原样展示
      toast(error.message || '下线失败', 'error');
      btn.disabled = false;
    }
  });

  load();
  return container;
}

/** 渲染 FAQ 表格 */
function renderTable(items) {
  return `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th style="width:56px">ID</th>
            <th>标准问题</th>
            <th>答案摘要</th>
            <th style="width:110px">分类</th>
            <th style="width:110px">关联单元</th>
            <th style="width:100px">命中次数</th>
            <th style="width:110px">状态</th>
            <th style="width:140px">审核时间</th>
            <th style="width:80px">操作</th>
          </tr>
        </thead>
        <tbody>
          ${items
            .map((faq) => {
              const meta = FAQ_STATUS[faq.status] || { label: faq.status || '-', cls: 'tag' };
              return `
              <tr>
                <td>${esc(faq.id)}</td>
                <td>${esc(faq.question || '-')}</td>
                <td class="mute-sm">${faq.answer ? esc(faq.answer.slice(0, 60)) : '（无答案）'}</td>
                <td>${esc(faq.category || '-')}</td>
                <td>${
                  faq.related_unit_id
                    ? `<span class="tag mono">#${esc(faq.related_unit_id)}</span>`
                    : '<span class="mute-sm">未关联</span>'
                }</td>
                <td><span class="tag tag-primary">${esc(fmtNumber(faq.hit_count || 0))} 次</span></td>
                <td><span class="tag ${esc(meta.cls)}">${esc(meta.label)}</span></td>
                <td class="mute-sm">${esc(fmtDateTime(faq.reviewed_at))}</td>
                <td>${
                  faq.status === 'published'
                    ? `<button class="btn-link" type="button" data-act="offline" data-id="${esc(faq.id)}">下线</button>`
                    : '<span class="mute-sm">—</span>'
                }</td>
              </tr>`;
            })
            .join('')}
        </tbody>
      </table>
    </div>
  `;
}
