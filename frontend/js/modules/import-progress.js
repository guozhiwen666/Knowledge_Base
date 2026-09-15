/**
 * 知识导入 · 解析进度轮询器
 *
 * 从 knowledge-import.js 拆出。它只做一件事：定时调用
 * `GET /api/knowledge/import/progress` 并把结果交给调用方，直到
 * 响应里的 `all_finished` 为真（9.4「全部任务终态后停止」）。
 *
 * 为什么独立成文件：定时器的启停与「路由切走要清理」是这类轮询最容易写错的地方，
 * 收在一处才能保证每个使用方都正确清理；页面文件只管渲染。
 *
 * 保护措施（真实场景里都会遇到）：
 *   1. 上一次请求没回来时跳过这一拍，避免请求堆叠（网络慢时尤其明显）；
 *   2. 轮询次数上限，后端若始终不返回终态也不会无限轮询；
 *   3. stop() 幂等，重复调用安全。
 */

import { getImportProgress } from '../api/knowledge.js';

/** 轮询间隔（9.4 建议 1.5~2 秒；解析含向量化，2 秒内的变化才有观察价值） */
export const POLL_INTERVAL_MS = 1800;

/** 轮询次数上限：1800ms × 300 ≈ 9 分钟，超过即判定为异常并停止 */
export const MAX_POLL_TIMES = 300;

/**
 * 创建一个进度轮询器（创建即开始，第一拍立刻执行，不等一个间隔）。
 *
 * @param {object} opts
 *   taskIds    string[] 任务标识
 *   onUpdate   ({items, missing, times}) => void 每次拿到进度后回调
 *   onFinish   ({reason}) => void 停止时回调；reason 为 finished | timeout
 *   onError    (error, times) => void 单次请求失败的回调（轮询会继续）
 * @returns {{stop: Function, isRunning: Function}}
 */
export function createProgressPoller({ taskIds, onUpdate, onFinish, onError, intervalMs = POLL_INTERVAL_MS }) {
  let timer = null;
  let stopped = false;
  let inFlight = false;
  let times = 0;

  /** 停止轮询：清掉待执行的定时器并让在途请求的回调失效 */
  const stop = () => {
    stopped = true;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };

  /** 结束并通知调用方 */
  const finish = (reason) => {
    stop();
    if (onFinish) onFinish({ reason, times });
  };

  /** 单拍：请求 → 回调 → 决定继续还是结束 */
  const tick = async () => {
    if (stopped || inFlight) return;
    inFlight = true;
    try {
      const data = await getImportProgress(taskIds);
      if (stopped) return;
      times += 1;
      if (onUpdate) {
        onUpdate({
          items: (data && data.items) || [],
          missing: (data && data.missing_task_ids) || [],
          times,
        });
      }
      // 第 1 步：终态判定。后端把「全部任务到达终态且一个都没缺」压成一个布尔值
      if (data && data.all_finished) {
        finish('finished');
        return;
      }
      // 第 2 步：兜底上限，避免后端异常时无限轮询
      if (times >= MAX_POLL_TIMES) {
        finish('timeout');
        return;
      }
    } catch (error) {
      if (stopped) return;
      times += 1;
      // 单次失败不立刻放弃：网络抖动或后端重启后恢复都能自愈；
      // 连续失败由次数上限兜住
      if (onError) onError(error, times);
      if (times >= MAX_POLL_TIMES) {
        finish('timeout');
        return;
      }
    } finally {
      inFlight = false;
    }
    if (!stopped) timer = setTimeout(tick, intervalMs);
  };

  tick();

  return { stop, isRunning: () => !stopped };
}
