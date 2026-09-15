/**
 * 模块七：知识沉淀与 FAQ 挖掘
 * （对应 9.1 第七个模块 / 页面 2.9.3-6 知识沉淀管理页）
 *
 * 需求落位：
 *   FAQ 推荐与审核     -> `GET /api/settlement/faqs/recommendations`（推荐频次 / 关联知识单元 / 建议答案）
 *                         + `POST /api/settlement/faqs/{id}/review`（通过 / 驳回 + 编辑答案）
 *   知识缺口列表       -> `GET /api/settlement/knowledge-gaps`（提问频次 / 最近提问时间 / 状态）
 *
 * 审核弹窗拆在 settlement-review.js。
 *
 * **明确缺失、页面用占位说明的部分**（第 14 章【待确认】第 6、7 条）：
 *   已发布 FAQ 库的查询 / 下线接口、知识缺口「一键创建关联知识单元补全」接口。
 */

import { listFaqRecommendations, listKnowledgeGaps } from '../api/settlement.js';
import {
  el,
  esc,
  $,
  pendingBlock,
  emptyState,
  loadingState,
  fmtNumber,
  fmtDateTime,
} from '../core/dom.js';
import { openReviewModal } from './settlement-review.js';

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
      <p class="page-desc">FAQ 自动推荐与审核、已发布 FAQ 库、知识缺口列表</p>

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

      <div class="card">
        <div class="card-title">
          <span>已发布 FAQ 库</span>
          <span class="tag tag-warn">待接口确认</span>
        </div>
        ${pendingBlock(
          '已发布 FAQ 库',
          '2.9.3 要求展示已发布的 FAQ 库（含命中次数），但 8.7 只提供了「待审核推荐列表」「审核」「知识缺口列表」三个接口，没有已发布 FAQ 的查询与下线接口。因此本区块不做实现。',
          '已发布 FAQ 列表查询 / 下线接口（8 章未列出）',
        )}
      </div>

      <div class="card">
        <div class="card-title">
          <span>知识缺口列表</span>
          <div class="row">
            <span class="mute-sm" data-role="gap-count"></span>
            <button class="btn btn-sm" type="button" data-role="reload-gaps">刷新</button>
          </div>
        </div>
        <div data-role="gaps">${loadingState()}</div>
        <div class="mt16">
          ${pendingBlock(
            '知识缺口一键建档',
            '11.4 要求支持「一键创建关联知识单元补全」，但对应的接口未在 8 章列出。本页只展示缺口的提问频次与最近提问时间，不提供建档按钮。',
            '知识缺口状态流转 / 关联知识单元接口（8 章未列出）',
          )}
        </div>
      </div>
    </div>
  `);

  // 两个区块独立加载，互不阻塞
  const recsCache = { items: [] };
  loadRecommendations(container, recsCache);
  loadGaps(container);

  $('[data-role="reload-recs"]', container).addEventListener('click', () =>
    loadRecommendations(container, recsCache),
  );
  $('[data-role="reload-gaps"]', container).addEventListener('click', () => loadGaps(container));

  return container;
}

/* ------------------------------------------------------ FAQ 推荐列表 */

async function loadRecommendations(container, cache) {
  const host = $('[data-role="recs"]', container);
  const countBox = $('[data-role="rec-count"]', container);
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

  // 审核按钮 → 打开审核弹窗
  host.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-act="review"]');
    if (!btn) return;
    const target = cache.items.find((item) => String(item.id) === btn.dataset.id);
    if (target) openReviewModal(target, () => loadRecommendations(container, cache));
  });
}

/* ------------------------------------------------------ 知识缺口列表 */

async function loadGaps(container) {
  const host = $('[data-role="gaps"]', container);
  const countBox = $('[data-role="gap-count"]', container);
  host.innerHTML = loadingState();

  let items;
  try {
    items = await listKnowledgeGaps();
  } catch (error) {
    host.innerHTML = `<div class="empty-state">加载失败：${esc(error.message)}</div>`;
    return;
  }
  items = items || [];
  countBox.textContent = `共 ${fmtNumber(items.length)} 条`;

  if (!items.length) {
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
            <th style="width:110px">提问频次</th>
            <th style="width:170px">最近提问时间</th>
            <th style="width:110px">状态</th>
          </tr>
        </thead>
        <tbody>
          ${items
            .map((gap) => {
              const meta = GAP_STATUS[gap.status] || { label: gap.status || '-', cls: 'tag' };
              return `
              <tr>
                <td>${esc(gap.id)}</td>
                <td>${esc(gap.question_pattern || '-')}</td>
                <td><span class="tag tag-warn">${esc(fmtNumber(gap.ask_count))} 次</span></td>
                <td class="mute-sm">${esc(fmtDateTime(gap.last_asked_at))}</td>
                <td><span class="tag ${esc(meta.cls)}">${esc(meta.label)}</span></td>
              </tr>`;
            })
            .join('')}
        </tbody>
      </table>
    </div>
  `;
}
