/**
 * 模块三之一：知识维护与批量导入 · 导入中心
 * （对应 9.1 第三个模块 / 页面 2.9.3-3 知识导入中心）
 *
 * 需求（2.9.3 / 9.4）与实现落位：
 *   拖拽上传        -> dropzone 的 dragover/drop 事件
 *   多文件并发上传  -> 按并发度分批 Promise.allSettled（9.4：单个失败不中断其余）
 *   两段进度        -> 上传阶段用 XMLHttpRequest.upload.onprogress 的字节进度；
 *                      上传返回 task_id 后，交给 import-progress.js 按时轮询
 *                      GET /api/knowledge/import/progress，all_finished 为真即停止
 *   格式约束        -> PDF / Markdown / Word / TXT（前端预筛 + 后端按扩展名复筛，
 *                      被拒原因原样展示：unsupported_format）
 *
 * 队列模型与表格渲染在 import-queue.js，轮询器在 import-progress.js；
 * 本文件只负责页面组装与流程编排。
 *
 * 注意 8.4 的导入已改为「接收即返回」：unit_code / chunk_count 这些入库结果
 * 只能从进度接口取，上传响应里只有 task_id。
 *
 * 路由切走必须停止轮询（否则定时器会在别的页面上继续发请求）：
 * 模块只加载一次，这里注册一次全局 hashchange 监听即可。
 */

import { importDocuments } from '../api/knowledge.js';
import { hasPermission } from '../core/store.js';
import { navigate } from '../core/router.js';
import { el, $, toast, emptyState } from '../core/dom.js';
import { createProgressPoller } from './import-progress.js';
import {
  ACCEPT_EXT,
  applyImportResult,
  applyProgressRecord,
  bindDropzone,
  createEntry,
  extOf,
  markMissingTask,
  renderQueueTableHtml,
  summarizeQueue,
} from './import-queue.js';

/** 并发度：同时最多处理几个文件（9.4：并发度可配） */
const CONCURRENCY = 3;

/** 当前进行中的轮询器（路由切走必须 stop） */
let activePoll = null;

window.addEventListener('hashchange', () => {
  if (activePoll) {
    activePoll.stop();
    activePoll = null;
  }
});

/** 渲染导入中心页面（同步返回骨架，拖拽区立即可用） */
export function renderImportCenter() {
  const canCreate = hasPermission('knowledge:unit:create');

  const container = el(`
    <div>
      <h2 class="page-title">知识导入中心</h2>
      <p class="page-desc">
        支持 PDF / Markdown / Word / TXT 四类格式；每个文件解析后独立成为一个知识单元（2.9.4）
      </p>

      ${
        canCreate
          ? ''
          : `<div class="card"><div class="field-hint">
               当前账号缺少 <code>knowledge:unit:create</code> 权限：无法发起导入 ——
               上传与解析进度轮询都要求该权限码，下方按钮已禁用。
             </div></div>`
      }

      <div class="card">
        <div class="card-title">
          <span>上传与解析进度</span>
          <div class="row">
            <span class="mute-sm" data-role="parse-status">等待上传</span>
            <button class="btn btn-sm" type="button" data-role="to-units">查看知识单元列表</button>
          </div>
        </div>

        <div class="field">
          <label>统一分类（可选）</label>
          <input class="input" data-role="category" type="text" placeholder="留空则不设置分类" style="max-width:320px" />
          <div class="field-hint">对应 8.4 请求字段 <code>category</code>，作为本批文件默认分类（后端写入 knowledge_units.category）。</div>
        </div>

        <div class="dropzone" data-role="dropzone">
          <div class="dz-title">把文件拖到这里，或点击选择文件</div>
          <div class="mute-sm">支持 .pdf / .md / .docx / .txt，可一次选择多个</div>
          <input type="file" multiple hidden data-role="file-input"
                 accept=".pdf,.md,.markdown,.docx,.doc,.txt" />
        </div>

        <div class="row mt16">
          <button class="btn btn-primary" type="button" data-role="start" ${canCreate ? '' : 'disabled'}>
            开始导入
          </button>
          <button class="btn" type="button" data-role="clear">清空列表</button>
          <span class="mute-sm" data-role="summary"></span>
        </div>

        <div class="mt16" data-role="table"></div>
        <div class="field-hint mt8">
          并发度 ${CONCURRENCY}，单个文件失败不影响其余；解析进度每 1.8 秒查询一次
          <code>GET /api/knowledge/import/progress</code>，全部任务到达终态后自动停止。
        </div>
      </div>
    </div>
  `);

  // ---- 状态：待处理文件队列 + 轮询状态 ----
  const queue = [];
  let pollInfo = { running: false, finished: false, times: 0, lastError: '' };

  const tableBox = $('[data-role="table"]', container);
  const summaryBox = $('[data-role="summary"]', container);
  const parseStatus = $('[data-role="parse-status"]', container);
  const dropzone = $('[data-role="dropzone"]', container);
  const fileInput = $('[data-role="file-input"]', container);

  /** 整体重渲染：队列行数是个位数量级，重渲染比增量 diff 简单可靠 */
  const render = () => {
    summaryBox.textContent = summarizeQueue(queue);
    tableBox.innerHTML = queue.length
      ? renderQueueTableHtml(queue)
      : emptyState('还没有待上传的文件：拖入或选择文件后会在这里显示上传与解析进度');

    // 轮询状态文案。失败信息如实展示，不吞掉
    if (pollInfo.lastError) {
      parseStatus.textContent = `轮询出错（第 ${pollInfo.times} 次）：${pollInfo.lastError}`;
    } else if (pollInfo.running) {
      parseStatus.textContent = `轮询中 · 第 ${pollInfo.times} 次`;
    } else if (pollInfo.finished) {
      parseStatus.textContent = '轮询已结束（全部任务到达终态）';
    } else {
      parseStatus.textContent = '等待上传';
    }
  };

  /** 加入文件（客户端先按扩展名预筛，避免把明显不支持的文件发到后端） */
  const addFiles = (files) => {
    let unsupported = 0;
    Array.from(files).forEach((file) => {
      const entry = createEntry(file);
      if (!ACCEPT_EXT.includes(extOf(file.name))) {
        unsupported += 1;
        // 与后端 rejected 的 unsupported_format 语义一致，先在前端标出来
        entry.status = 'rejected';
        entry.reason = 'unsupported_format（前端预筛：仅支持 PDF/MD/DOCX/TXT）';
      }
      queue.push(entry);
    });
    if (unsupported) toast(`${unsupported} 个文件格式不支持，已标记为拒绝`, 'warn');
    render();
  };

  // ---- 交互 1：上传区（点击选择 / 拖拽），选中即入队 ----
  bindDropzone({ dropzone, fileInput, host: container, onFiles: addFiles });

  // ---- 交互 2：移除单个文件（处理中的行上按钮是 disabled 的） ----
  tableBox.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-remove]');
    if (!btn || btn.disabled) return;
    queue.splice(Number(btn.dataset.remove), 1);
    render();
  });

  // ---- 交互 3：清空 ----
  $('[data-role="clear"]', container).addEventListener('click', () => {
    if (queue.some((item) => item.status === 'uploading' || item.status === 'parsing')) {
      toast('有文件正在处理中，请等待完成', 'warn');
      return;
    }
    if (activePoll) {
      activePoll.stop();
      activePoll = null;
      pollInfo = { running: false, finished: true, times: pollInfo.times, lastError: '' };
    }
    queue.length = 0;
    render();
  });

  /** 上传单个文件；成功后拿 task_id 进入「解析中」，入库结果留给轮询补齐 */
  const processOne = async (item, category) => {
    item.status = 'uploading';
    item.percent = 0;
    render();
    try {
      // 第 1 步：单文件提交一次请求（8.4 支持单文件时只有一个 files 字段）
      const data = await importDocuments([item.file], category, (percent) => {
        item.percent = percent;
        render();
      });
      item.percent = 100;
      // 第 2 步：按新契约落结果（accepted 给 task_id，rejected 给原因）
      applyImportResult(item, data);
    } catch (error) {
      item.status = 'error';
      item.reason = error.message || '上传失败';
    }
    render();
  };

  /** 把进度接口返回的记录写回队列（按 task_id 匹配） */
  const applyProgress = (records) => {
    records.forEach((record) => {
      const item = queue.find((entry) => entry.taskId === record.task_id);
      if (item) applyProgressRecord(item, record);
    });
  };

  /** 进度接口回传的「查不到的任务」如实标注 */
  const markMissing = (taskIds) => {
    taskIds.forEach((taskId) => {
      const item = queue.find((entry) => entry.taskId === taskId);
      if (item) markMissingTask(item);
    });
  };

  /** 启动轮询（同一时刻只允许一个轮询器） */
  const startPolling = (taskIds) => {
    if (activePoll) activePoll.stop();
    pollInfo = { running: true, finished: false, times: 0, lastError: '' };
    render();

    activePoll = createProgressPoller({
      taskIds,
      onUpdate: ({ items, missing, times }) => {
        pollInfo.times = times;
        pollInfo.lastError = '';
        applyProgress(items);
        markMissing(missing);
        render();
      },
      onError: (error, times) => {
        pollInfo.times = times;
        pollInfo.lastError = error.message || '请求失败';
        render();
      },
      onFinish: ({ reason }) => {
        pollInfo = { running: false, finished: true, times: pollInfo.times, lastError: '' };
        activePoll = null;
        render();
        const done = queue.filter((item) => item.status === 'done').length;
        const failed = queue.filter((item) => item.status === 'error' || item.status === 'rejected').length;
        if (reason === 'timeout') {
          toast(`轮询已达次数上限，停止等待解析结果：已入库 ${done} 个，失败 ${failed} 个`, 'warn');
        } else {
          toast(`解析完成：已入库 ${done} 个，失败 ${failed} 个`, failed ? 'warn' : 'success');
        }
      },
    });
  };

  // ---- 交互 5：开始导入（分批并发上传 → 启动解析进度轮询） ----
  $('[data-role="start"]', container).addEventListener('click', async () => {
    const category = $('[data-role="category"]', container).value.trim();
    const pending = queue.filter((item) => item.status === 'pending');
    if (!pending.length) {
      toast('没有待上传的文件', 'warn');
      return;
    }

    const startBtn = $('[data-role="start"]', container);
    startBtn.disabled = true;
    startBtn.textContent = '上传中…';

    // 第 1 步：按并发度切批，逐批 Promise.allSettled
    // 用 allSettled 而非 all：9.4 要求「单个失败不中断其余」
    for (let i = 0; i < pending.length; i += CONCURRENCY) {
      const batch = pending.slice(i, i + CONCURRENCY);
      await Promise.allSettled(batch.map((item) => processOne(item, category)));
    }

    startBtn.disabled = false;
    startBtn.textContent = '开始导入';

    // 第 2 步：把本轮拿到 task_id 的文件交给轮询器；一个都没有就只提示上传结果
    const taskIds = queue.filter((item) => item.status === 'parsing' && item.taskId).map((item) => item.taskId);
    if (!taskIds.length) {
      const failed = queue.filter((item) => item.status === 'rejected' || item.status === 'error').length;
      toast(failed ? `上传结束：${failed} 个文件未进入解析` : '没有文件需要解析', failed ? 'warn' : 'info');
      return;
    }
    toast(`已提交 ${taskIds.length} 个解析任务，正在轮询进度`, 'success');
    startPolling(taskIds);
  });

  $('[data-role="to-units"]', container).addEventListener('click', () => navigate('/knowledge/units'));

  // 初始渲染
  render();
  return container;
}
