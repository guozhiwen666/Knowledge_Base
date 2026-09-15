/**
 * 数据看板 · 图表层（ECharts 封装）
 *
 * 从 dashboard-module.js 拆出，只负责「给定容器与数据 → 画出图表」，不负责取数。
 *
 * 两条硬约束（9.4）：
 *   1. **数据为空时展示空态而非空白画布** —— 每个渲染函数入口先判空；
 *   2. 图表实例统一登记在 chartRegistry，路由切走时整体 dispose，
 *      否则来回切页会持续累积 ECharts 实例与事件监听。
 *
 * ECharts 由 index.html 以 script 标签从 vendor/echarts.min.js 引入（无 npm 依赖），
 * 因此这里从 window.echarts 取，取不到时给出明确的降级提示而不是抛异常。
 */

import { esc, fmtNumber, fmtFixed } from '../core/dom.js';

/** 已创建的图表实例：{ key: instance }，用于重建前销毁与路由切换时统一销毁 */
const chartRegistry = new Map();

// 路由切走时销毁全部图表，避免内存与监听泄漏
window.addEventListener('hashchange', () => {
  chartRegistry.forEach((chart) => chart.dispose());
  chartRegistry.clear();
});

/** 取全局 ECharts；未加载时返回 null 由调用方降级 */
function echartsLib() {
  return window.echarts || null;
}

/** 图表库缺失时的统一提示文案 */
const NO_LIB_HINT =
  '图表库未加载：请确认 vendor/echarts.min.js 存在，或按 README 的说明改用 CDN 引入。';

/** 销毁并重建指定 key 的图表，返回新实例（不存在则只创建） */
function recreate(host, key) {
  if (chartRegistry.has(key)) {
    chartRegistry.get(key).dispose();
    chartRegistry.delete(key);
  }
  host.innerHTML = '<div class="chart-box"></div>';
  const chart = echartsLib().init(host.firstElementChild);
  chartRegistry.set(key, chart);
  return chart;
}

/**
 * 渲染横向条形榜（常见问题榜 / 知识单元热度榜共用）。
 *
 * @param {HTMLElement} host 挂载容器
 * @param {string} key 图表实例登记键
 * @param {Array} items 后端 items
 * @param {string} valueKey 数值字段名（ask_count / access_count）
 * @param {string} nameKey 名称字段名（question / title）
 */
export function renderBarChart(host, key, items, valueKey, nameKey) {
  if (!host) return;

  // 第 1 步：空数据走空态，不留空白画布
  if (!items || !items.length) {
    host.innerHTML = '<div class="chart-empty">暂无数据</div>';
    return;
  }

  // 第 2 步：图表库就绪性检查
  if (!echartsLib()) {
    host.innerHTML = `<div class="chart-empty">${esc(NO_LIB_HINT)}</div>`;
    return;
  }

  const chart = recreate(host, key);

  // 第 3 步：条形图横向排列，名称过长时截断（完整名称放在 tooltip 里）
  const names = items.map((item) => {
    const raw = String(item[nameKey] ?? '-');
    return raw.length > 18 ? `${raw.slice(0, 18)}…` : raw;
  });
  const values = items.map((item) => Number(item[valueKey]) || 0);

  chart.setOption({
    grid: { left: 8, right: 40, top: 10, bottom: 10, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const param = params[0];
        const original = String(items[param.dataIndex][nameKey] ?? '-');
        return `${original}<br/>计数：${param.value}`;
      },
    },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef1f6' } } },
    yAxis: {
      type: 'category',
      inverse: true, // 值最大的排在最上面
      data: names,
      axisLabel: { fontSize: 12, color: '#64707f' },
      axisLine: { lineStyle: { color: '#e3e8f0' } },
    },
    series: [
      {
        type: 'bar',
        data: values,
        barMaxWidth: 18,
        itemStyle: { color: '#2563eb', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', fontSize: 11, color: '#64707f' },
      },
    ],
  });
}

/**
 * 渲染 Token 消耗与响应时间趋势折线图（双 Y 轴）。
 *
 * 量纲差异很大（Token 是千级、响应时间是千毫秒级但比例不同），
 * 共用一个 Y 轴会把其中一条压成贴底直线，因此必须分轴。
 *
 * @param {HTMLElement} host 挂载容器
 * @param {HTMLElement} summaryHost 右上角统计文案容器（可为 null）
 * @param {Array} items [{date, total_tokens, avg_response_time_ms}]
 * @param {'day'|'week'} granularity 用于空态文案
 */
export function renderTrendChart(host, summaryHost, items, granularity) {
  if (!host) return;

  // 第 1 步：空数据 → 空态
  if (!items || !items.length) {
    host.innerHTML = `<div class="chart-empty">暂无${granularity === 'day' ? '按日' : '按周'}趋势数据</div>`;
    if (summaryHost) summaryHost.textContent = '';
    return;
  }

  // 第 2 步：图表库就绪性检查
  if (!echartsLib()) {
    host.innerHTML = `<div class="chart-empty">${esc(NO_LIB_HINT)}</div>`;
    if (summaryHost) summaryHost.textContent = '';
    return;
  }

  // 第 3 步：右上角汇总（合计 Token 与平均耗时）
  if (summaryHost) {
    const tokens = items.map((item) => Number(item.total_tokens) || 0);
    const times = items.map((item) => Number(item.avg_response_time_ms) || 0);
    const totalTokens = tokens.reduce((sum, value) => sum + value, 0);
    const avgTime = times.length ? times.reduce((sum, value) => sum + value, 0) / times.length : 0;
    summaryHost.textContent = `合计 ${fmtNumber(totalTokens)} tokens · 平均 ${fmtFixed(avgTime, 2)} ms`;
  }

  // 第 4 步：建图
  const chart = recreate(host, 'trend-chart');
  const dates = items.map((item) => String(item.date ?? ''));
  const tokens = items.map((item) => Number(item.total_tokens) || 0);
  const times = items.map((item) => Number(item.avg_response_time_ms) || 0);

  chart.setOption({
    grid: { left: 10, right: 10, top: 40, bottom: 24, containLabel: true },
    tooltip: { trigger: 'axis' },
    legend: { data: ['Token 消耗', '平均响应时间'], top: 4 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { fontSize: 11, color: '#64707f' },
      axisLine: { lineStyle: { color: '#e3e8f0' } },
    },
    yAxis: [
      {
        type: 'value',
        name: 'Token',
        nameTextStyle: { color: '#64707f', fontSize: 11 },
        splitLine: { lineStyle: { color: '#eef1f6' } },
        axisLabel: { fontSize: 11, color: '#64707f' },
      },
      {
        type: 'value',
        name: '响应时间(ms)',
        nameTextStyle: { color: '#64707f', fontSize: 11 },
        splitLine: { show: false },
        axisLabel: { fontSize: 11, color: '#64707f' },
      },
    ],
    series: [
      {
        name: 'Token 消耗',
        type: 'line',
        smooth: true,
        data: tokens,
        itemStyle: { color: '#2563eb' },
        areaStyle: { color: 'rgba(37,99,235,0.10)' },
      },
      {
        name: '平均响应时间',
        type: 'line',
        smooth: true,
        yAxisIndex: 1,
        data: times,
        itemStyle: { color: '#d97706' },
      },
    ],
  });
}
