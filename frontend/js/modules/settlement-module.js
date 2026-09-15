/**
 * 模块七：知识沉淀与 FAQ 挖掘
 * （对应 9.1 第七个模块 / 页面 2.9.3-6 知识沉淀管理页）
 *
 * 三块内容的接口落位：
 *   1. FAQ 推荐与审核   -> `GET /api/settlement/faqs/recommendations` + `POST /api/settlement/faqs/{id}/review`
 *   2. FAQ 库           -> `GET /api/settlement/faqs` + `POST /api/settlement/faqs/{id}/offline`
 *                          （拆在 settlement-faq-library.js）
 *   3. 知识缺口         -> `GET /api/settlement/knowledge-gaps`
 *                          + `POST /api/settlement/knowledge-gaps/{id}/resolve|ignore`
 *                          （操作弹窗拆在 settlement-gap-actions.js）
 *
 * 审核弹窗拆在 settlement-review.js。三块各自独立加载，互不阻塞。
 *
 * 缺口的两个新字段直接用上了：
 *   `resolved_unit_id`  —— 已补全时对应的知识单元，列表里直接展示「由哪个单元补全」；
 *   `sample_questions`  —— 样本提问，展示前两条并在补全弹窗里列全。
 * 已 resolved / ignored 的行不再显示「一键建档」与「忽略」按钮（11.4 的状态语义）。
 */

import { listFaqRecommendations, listKnowledgeGaps } from '../api/settlement.js';
import {
  el,
  esc,
  $,
  emptyState,
  loadingState,
  fmtNumber,
  fmtDateTime,
} from '../core/dom.js';
import { openReviewModal } from './settlement-review.js';
import { renderFaqLibrary } from './settlement-faq-library.js';
import { openGapResolveModal, confirmIgnoreGap } from './settlement-gap-actions.js';

/** 知识缺口状态的展示映射（models/enums.py 的 KnowledgeGapStatus） */
const GAP_STATUS = {
  unresolved: { label: '未解决', cls: 'tag-warn' },
  resolved: { label: '已补全', cls: 'tag-success' },
  ignored: { label: '已忽略', cls: 'tag' },
};

/** 渲染知识沉淀管理页 */
export function renderSettlement() {
  const container = el(`
    <div>
      <h2 class="page-title">知识沉淀管理</h2>
      <p class="page-desc">FAQ 自动推荐与审核、FAQ 库、知识缺口补全</p>

      <div class="card">
        <div class="card-title">
          <span>FAQ 推荐与审核</span>
          <div class="row">
            <span class="mute-sm" data-role="rec-count"></span>
            <button class="btn btn-sm" type="button" data-role="reload-recs">刷新</button>
          </div>
        </div>
        <div data-role="recs">${loadingState()}</div>
      </div>

      <div data-role="faq-library">${loadingState()}</div>

      <div class="card">
        <div class="card-title">
          <span>知识缺口列表</span>
          <div class="row">
            <span class="mute-sm" data-role="gap-count"></span>
            <button class="btn btn-sm" type="button" data-role="reload-gaps">刷新</button>
          </div>
        </div>
        <div data-role="gaps">${loadingState()}</div>
        <div class="field-hint mt8">
          「一键建档」会新建一个知识单元并关联本缺口（标题缺省用问题模式，正文缺省由后端拼出骨架）；
          「忽略」保留记录但不再纳入未解决清单。已补全 / 已忽略的缺口不再显示这两个按钮。
        </div>
      </div>
    </div>
  `);

  // 三块独立加载，互不阻塞
  const recsCache = { items: [] };
  const gapsCache = { items: [] };
  const recsBox = $('[data-role="recs"]', container);
  const gapsBox = $('[data-role="gaps"]', container);
  const reloadRecs = () => loadRecommendations(recsBox, $('[data-role="rec-count"]', container), recsCache);
  const reloadGaps = () => loadGaps(gapsBox, $('[data-role="gap-count"]', container), gapsCache);

  // 事件只绑一次：列表是整体重渲染的，把监听挂在每次重渲染里会重复叠加
  recsBox.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-act="review"]');
    if (!btn) return;
    const target = recsCache.items.find((item) => String(item.id) === btn.dataset.id);
    if (target) openReviewModal(target, reloadRecs);
  });
  gapsBox.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-act]');
    if (!btn || btn.disabled) return;
    const gap = gapsCache.items.find((item) => String(item.id) === btn.dataset.id);
    if (!gap) return;
    if (btn.dataset.act === 'resolve') openGapResolveModal(gap, reloadGaps);
    if (btn.dataset.act === 'ignore') confirmIgnoreGap(gap, reloadGaps);
  });

  $('[data-role="faq-library"]', container).replaceChildren(renderFaqLibrary());
  reloadRecs();
  reloadGaps();

  $('[data-role="reload-recs"]', container).addEventListener('click', reloadRecs);
  $('[data-role="reload-gaps"]', container).addEventListener('click', reloadGaps);

  return container;
}

/* ------------------------------------------------------ FAQ 推荐列表 */

async function loadRecommendations(host, countBox, cache) {
  host.innerHTML = loadingState();

  let items;
  try {
    items = await listFaqRecommendations();
  } catch (error) {
    host.innerHTML = `<div class="empty-state">加载失败：${esc(error.message)}</div>`;
    return;
  }
  cache.items = items || [];
  countBox.textContent = `共 ${fmtNumber(cache.items.length)} 条待审核`;

  // 空态：没有待审核项时给出明确文案，而不是留白
  if (!cache.items.length) {
    host.innerHTML = emptyState('暂无待审核的 FAQ 推荐项（沉淀引擎按频次阈值挖掘后写入）');
    return;
  }

  // 渲染表格：频次来自 8.7 的 frequency，关联知识单元来自 related_unit_id
  host.innerHTML = `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th style="width:56px">ID</th>
            <th>推荐问题</th>
            <th style="width:100px">推荐频次</th>
            <th style="width:120px">关联知识单元</th>
            <th>建议答案</th>
            <th style="width:90px">操作</th>
          </tr>
        </thead>
        <tbody>
          ${cache.items
            .map(
              (item) => `
            <tr>
              <td>${esc(item.id)}</td>
              <td>${esc(item.question || '-')}</td>
              <td>
                ${
                  item.frequency
                    ? `<span class="tag tag-primary">${esc(fmtNumber(item.frequency))} 次</span>`
                    : '<span class="mute-sm">0</span>'
                }
              </td>
              <td>
                ${
                  item.related_unit_id
                    ? `<span class="tag mono">#${esc(item.related_unit_id)}</span>`
                    : '<span class="mute-sm">未关联</span>'
                }
              </td>
              <td class="mute-sm">
                ${item.suggested_answer ? esc(item.suggested_answer.slice(0, 60)) : '<span class="mute-sm">暂无（审核时填写）</span>'}
              </td>
              <td class="actions">
                <button class="btn-link" type="button" data-act="review" data-id="${esc(item.id)}">审核</button>
              </td>
            </tr>`,
            )
            .join('')}
        </tbody>
      </table>
    </div>
    <div class="field-hint mt8">
      频次为后端按规范化问题现算（faqs 表没有频次列，见 settlement.py 的说明）；
      审核通过后该问答对会写入 FAQ 缓存，后续相同问题将直接命中缓存。
    </div>
  `;
}

/* ------------------------------------------------------ 知识缺口列表 */

async function loadGaps(host, countBox, cache) {
  host.innerHTML = loadingState();

  let items;
  try {
    items = await listKnowledgeGaps();
  } catch (error) {
    host.innerHTML = `<div class="empty-state">加载失败：${esc(error.message)}</div>`;
    return;
  }
  cache.items = items || [];
  const pending = cache.items.filter((gap) => gap.status === 'unresolved').length;
  countBox.textContent = `共 ${fmtNumber(cache.items.length)} 条，其中未解决 ${fmtNumber(pending)} 条`;

  if (!cache.items.length) {
    host.innerHTML = emptyState('暂无知识缺口记录（召回相似度低于阈值或无可支撑单元时写入）');
    return;
  }

  // 后端已按 ask_count 降序返回（settlement.py 的 list_knowledge_gaps）
  host.innerHTML = `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th style="width:56px">ID</th>
            <th>问题模式</th>
            <th style="width:100px">提问频次</th>
            <th>样本提问</th>
            <th style="width:120px">补全单元</th>
            <th style="width:150px">最近提问时间</th>
            <th style="width:110px">状态</th>
            <th style="width:130px">操作</th>
          </tr>
        </thead>
        <tbody>
          ${cache.items
            .map((gap) => {
              const meta = GAP_STATUS[gap.status] || { label: gap.status || '-', cls: 'tag' };
              const samples = gap.sample_questions || [];
              const unresolved = gap.status === 'unresolved';
              return `
              <tr>
                <td>${esc(gap.id)}</td>
                <td>${esc(gap.question_pattern || '-')}</td>
                <td><span class="tag tag-warn">${esc(fmtNumber(gap.ask_count))} 次</span></td>
                <td class="mute-sm">${
                  samples.length
                    ? `${esc(samples.slice(0, 2).join(' / '))}${samples.length > 2 ? ` 等 ${fmtNumber(samples.length)} 条` : ''}`
                    : '-'
                }</td>
                <td>${
                  gap.resolved_unit_id
                    ? `<span class="tag mono">#${esc(gap.resolved_unit_id)}</span>`
                    : '<span class="mute-sm">未补全</span>'
                }</td>
                <td class="mute-sm">${esc(fmtDateTime(gap.last_asked_at))}</td>
                <td><span class="tag ${esc(meta.cls)}">${esc(meta.label)}</span></td>
                <td class="actions">
                  ${
                    unresolved
                      ? `<button class="btn-link" type="button" data-act="resolve" data-id="${esc(gap.id)}">一键建档</button>
                         <button class="btn-link" type="button" data-act="ignore" data-id="${esc(gap.id)}">忽略</button>`
                      : '<span class="mute-sm">已处理</span>'
                  }
                </td>
              </tr>`;
            })
            .join('')}
        </tbody>
      </table>
    </div>
  `;
}
