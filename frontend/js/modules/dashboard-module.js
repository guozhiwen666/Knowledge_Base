/**
 * 模块六：看板与图表
 * （对应 9.1 第六个模块 / 页面 2.9.3-5 数据看板页）
 *
 * 本文件负责「取数与区块编排」，ECharts 封装拆在 dashboard-charts.js。
 *
 * 需求落位（2.9.3 + 8.6 + 11.2）：
 *   5 个指标卡片           -> `GET /api/dashboard/metrics`
 *                              total_access_count / unique_user_count /
 *                              knowledge_unit_count / total_tokens / avg_response_time_ms
 *   常见问题 TOP 榜        -> `GET /api/dashboard/rankings/questions`
 *   知识单元热度 TOP 榜    -> `GET /api/dashboard/rankings/units`
 *   Token 与响应时间趋势   -> `GET /api/dashboard/stats/tokens`（支持 day / week 切换，9.4）
 *
 * 三个区块各自独立加载：任一个接口失败只影响自己那一块，
 * 不会因为一个 500 就把整页变成空白。
 */

import { fetchMetrics, fetchTopQuestions, fetchTopUnits, fetchTokenTrend } from '../api/dashboard.js';
import { el, esc, $, emptyState, loadingState, fmtNumber, fmtFixed } from '../core/dom.js';
import { renderBarChart, renderTrendChart } from './dashboard-charts.js';

/** 五个指标卡片的配置：字段名与 8.6 / aggregation.py 的 collect_metrics 逐项对齐 */
const METRIC_CARDS = [
  { key: 'total_access_count', label: 'Agent 访问次数', unit: '次', digits: 0 },
  { key: 'unique_user_count', label: '独立访问人数（UV）', unit: '人', digits: 0 },
  { key: 'knowledge_unit_count', label: '知识单元总数', unit: '个', digits: 0 },
  { key: 'total_tokens', label: '总 Token 消耗', unit: 'tokens', digits: 0 },
  { key: 'avg_response_time_ms', label: '平均响应时间', unit: 'ms', digits: 2 },
];

/** 渲染数据看板 */
export function renderDashboard() {
  const container = el(`
    <div>
      <h2 class="page-title">数据看板</h2>
      <p class="page-desc">核心指标、双排行榜与 Token / 响应时间趋势（口径见文档 11.2）</p>

      <div class="metric-grid" data-role="metrics">${loadingState()}</div>

      <div class="chart-grid">
        <div class="card">
          <div class="card-title">
            <span>常见问题 TOP 榜</span>
            <span class="mute-sm">按 question 归一化分组计数降序</span>
          </div>
          <div data-role="q-chart">${loadingState()}</div>
        </div>

        <div class="card">
          <div class="card-title">
            <span>知识单元热度 TOP 榜</span>
            <span class="mute-sm">按 recalled_unit_ids 展开计数</span>
          </div>
          <div data-role="u-chart">${loadingState()}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">
          <span>Token 消耗与响应时间趋势</span>
          <div class="row">
            <span class="mute-sm" data-role="trend-summary"></span>
            <button class="btn btn-sm btn-primary" type="button" data-gran="day">按日</button>
            <button class="btn btn-sm" type="button" data-gran="week">按周</button>
          </div>
        </div>
        <div data-role="trend-chart">${loadingState()}</div>
      </div>

      <div class="card">
        <div class="card-title">口径说明</div>
        <table class="data">
          <thead><tr><th>图表</th><th>聚合口径</th><th>来源</th></tr></thead>
          <tbody>
            <tr>
              <td>常见问题 TOP 榜</td>
              <td>按 question 归一化（去首尾空白 + 转小写）分组后计数降序</td>
              <td class="mute-sm">11.2</td>
            </tr>
            <tr>
              <td>知识单元热度 TOP 榜</td>
              <td>展开 recalled_unit_ids_csv 计数降序（该口径属第 14 章【待确认】第 11 条）</td>
              <td class="mute-sm">11.2</td>
            </tr>
            <tr>
              <td>Token 与响应时间趋势</td>
              <td>按日 SUM(total_tokens) 与 AVG(response_time_ms)；周粒度用 YEARWEEK 分桶</td>
              <td class="mute-sm">11.2</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `);

  // 各区块独立加载，互不阻塞
  loadMetrics(container);
  loadQuestionRanking(container);
  loadUnitRanking(container);

  // 趋势图粒度切换（9.4：日 / 周切换）
  container.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-gran]');
    if (!btn) return;
    container.querySelectorAll('[data-gran]').forEach((node) => {
      node.classList.toggle('btn-primary', node === btn);
    });
    loadTrend(container, btn.dataset.gran);
  });
  loadTrend(container, 'day');

  return container;
}

/* ---------------------------------------------------------------- 指标卡片 */

async function loadMetrics(container) {
  const host = $('[data-role="metrics"]', container);
  try {
    const data = await fetchMetrics();
    host.innerHTML = METRIC_CARDS.map(
      (card) => `
      <div class="metric-card">
        <div class="label">${esc(card.label)}</div>
        <div class="value">
          ${esc(card.digits ? fmtFixed(data && data[card.key], card.digits) : fmtNumber(data && data[card.key]))}
          <span class="unit">${esc(card.unit)}</span>
        </div>
      </div>`,
    ).join('');
  } catch (error) {
    host.innerHTML = `<div class="metric-card" style="grid-column:1/-1">${emptyState(`指标加载失败：${error.message}`)}</div>`;
  }
}

/* ---------------------------------------------------------------- 榜单 */

async function loadQuestionRanking(container) {
  const host = $('[data-role="q-chart"]', container);
  host.innerHTML = loadingState();
  try {
    // top_n 取 10：8.6 声明 top_n 可配，后端 DEFAULT_TOP_N 亦为 10
    const items = await fetchTopQuestions(10);
    renderBarChart(host, 'q-chart', items, 'ask_count', 'question');
  } catch (error) {
    host.innerHTML = `<div class="chart-empty">加载失败：${esc(error.message)}</div>`;
  }
}

async function loadUnitRanking(container) {
  const host = $('[data-role="u-chart"]', container);
  host.innerHTML = loadingState();
  try {
    const items = await fetchTopUnits(10);
    renderBarChart(host, 'u-chart', items, 'access_count', 'title');
  } catch (error) {
    host.innerHTML = `<div class="chart-empty">加载失败：${esc(error.message)}</div>`;
  }
}

/* ---------------------------------------------------------------- 趋势 */

async function loadTrend(container, granularity) {
  const host = $('[data-role="trend-chart"]', container);
  const summary = $('[data-role="trend-summary"]', container);
  host.innerHTML = loadingState();

  let items;
  try {
    items = await fetchTokenTrend(granularity);
  } catch (error) {
    host.innerHTML = `<div class="chart-empty">加载失败：${esc(error.message)}</div>`;
    summary.textContent = '';
    return;
  }
  renderTrendChart(host, summary, items, granularity);
}
