/**
 * 数据看板接口封装
 *
 * 覆盖 8.8 接口清单中的第 15~18 个接口：
 *   15. `GET /api/dashboard/metrics`                  —— 五个核心指标
 *   16. `GET /api/dashboard/rankings/questions`       —— 常见问题 TOP 榜
 *   17. `GET /api/dashboard/rankings/units`           —— 知识单元热度 TOP 榜
 *   18. `GET /api/dashboard/stats/tokens`             —— Token 与响应时间趋势（day|week）
 *
 * 字段名与 8.6 及 backend/services/data_dashboard_statistics_service/aggregation.py
 * 的 collect_metrics / rank_questions / rank_units / collect_token_trend 逐项对齐。
 */

import { get } from './client.js';

/**
 * 核心指标（8.6）。
 * @returns {Promise<{total_access_count:number, unique_user_count:number,
 *   knowledge_unit_count:number, total_tokens:number, avg_response_time_ms:number}>}
 */
export function fetchMetrics() {
  return get('/api/dashboard/metrics');
}

/**
 * 常见问题 TOP 榜（8.6）。
 * @param {number} topN 取前 N 条（8.6 声明 top_n 可配）
 * @returns {Promise<Array<{question:string, ask_count:number}>>}
 */
export function fetchTopQuestions(topN = 10) {
  return get('/api/dashboard/rankings/questions', { top_n: topN });
}

/**
 * 知识单元热度 TOP 榜（8.6）。
 * @param {number} topN
 * @returns {Promise<Array<{unit_id:number, title:string, access_count:number}>>}
 */
export function fetchTopUnits(topN = 10) {
  return get('/api/dashboard/rankings/units', { top_n: topN });
}

/**
 * Token 消耗与响应时间趋势（8.6）。
 * @param {'day'|'week'} granularity 粒度，对应 9.4 的「日/周切换」
 * @returns {Promise<Array<{date:string, total_tokens:number, avg_response_time_ms:number}>>}
 */
export function fetchTokenTrend(granularity = 'day') {
  return get('/api/dashboard/stats/tokens', { granularity });
}
