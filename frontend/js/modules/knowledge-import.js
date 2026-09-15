/**
 * 模块三之一：知识维护与批量导入 · 导入中心
 * （对应 9.1 第三个模块 / 页面 2.9.3-3 知识导入中心）
 *
 * 需求（2.9.3 / 9.4）与实现落位：
 *   拖拽上传        -> dropzone 的 dragover/drop 事件
 *   多文件并发上传  -> 按并发度分批 Promise.allSettled（9.4：单个失败不中断其余）
 *   进度展示        -> 上传字节进度来自 XMLHttpRequest.upload.onprogress（真实进度）
 *   逐文件状态      -> accepted / rejected 分别落成「已入库」「被拒绝」
 *   格式约束        -> PDF / Markdown / Word / TXT（8.4 明确只接受这四类）
 *
 * **接口缺失的如实处理**：
 *   8.4 的导入接口是「同步解析并返回结果」，一次请求即终态；
 *   9.4 要求的「解析进度轮询」所依赖的查询接口不在 8 章，因此这里只展示
 *   上传字节进度与最终解析结果，并在页面上给出「待接口确认」的占位说明。
 */

import { importDocuments } from '../api/knowledge.js';
import { hasPermission } from '../core/store.js';
import { navigate } from '../core/router.js';
import { el, esc, $, toast, pendingBlock, fmtFileSize, fmtNumber } from '../core/dom.js';

/** 支持的文件扩展名（8.4：仅 PDF、Markdown、Word、TXT） */
const ACCEPT_EXT = ['pdf', 'md', 'markdown', 'docx', 'doc', 'txt'];

/** 并发度：同时最多处理几个文件（9.4：并发度可配） */
const CONCURRENCY = 3;

/** 取文件扩展名（小写，不含点） */
function extOf(name) {
  const index = String(name).lastIndexOf('.');
  return index === -1 ? '' : name.slice(index + 1).toLowerCase();
}

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
          : `<div class="card">${pendingBlock('导入操作', '当前账号缺少 knowledge:unit:create 权限，无法发起导入。', 'knowledge:unit:create')}</div>`
      }

      <div class="card">
        <div class="card-title">
          <span>上传文件</span>
          <span class="mute-sm">并发度 ${CONCURRENCY} · 单个文件失败不影响其余文件</span>
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

        <div class="upload-list" data-role="list"></div>
      </div>

      <div class="card">
        <div class="card-title">解析进度轮询</div>
        ${pendingBlock(
          '解析进度轮询',
          '9.4 要求「定时轮询解析进度，全部任务终态后停止」。但本页展示的上传进度只是字节传输进度，解析阶段（切片、向量化入库）的进度需要查询任务状态的接口，该接口未在文档 8 章列出，因此无法轮询。当前实现为：导入请求返回后直接落定每个文件的最终结果（已入库 / 被拒绝）。',
          '解析任务状态查询接口（8 章未列出）',
        )}
      </div>
    </div>
  `);

  // ---- 状态容器：待上传文件列表，每项含 { file, status, percent, reason } ----
  const queue = [];
  const listBox = $('[data-role="list"]', container);
  const summaryBox = $('[data-role="summary"]', container);
  const dropzone = $('[data-role="dropzone"]', container);
  const fileInput = $('[data-role="file-input"]', container);

  /** 重新渲染整个队列（队列通常只有个位数文件，整体重渲染比增量 diff 简单可靠） */
  const renderQueue = () => {
    if (!queue.length) {
      listBox.innerHTML = '';
      summaryBox.textContent = '';
      return;
    }
    listBox.innerHTML = queue
      .map((item, index) => {
        const statusTag =
          item.status === 'done'
            ? '<span class="tag tag-success">已入库</span>'
            : item.status === 'rejected'
              ? `<span class="tag tag-danger">被拒绝</span>`
              : item.status === 'uploading'
                ? '<span class="tag tag-primary">上传中</span>'
                : item.status === 'parsing'
                  ? '<span class="tag tag-primary">解析中</span>'
                  : item.status === 'error'
                    ? '<span class="tag tag-danger">失败</span>'
                    : '<span class="tag">待上传</span>';
        const detail =
          item.status === 'rejected' || item.status === 'error'
            ? `<span class="mute-sm">${esc(item.reason || '未知原因')}</span>`
            : item.status === 'done'
              ? `<span class="mute-sm mono">${esc(item.unitCode || '')}</span>`
              : '';
        return `
        <div class="upload-item">
          <span class="name" title="${esc(item.file.name)}">${esc(item.file.name)}</span>
          <span class="mute-sm">${esc(fmtFileSize(item.file.size))}</span>
          ${statusTag}
          <span class="bar"><i style="width:${item.percent || 0}%"></i></span>
          <span class="mute-sm" style="width:44px;text-align:right">${item.percent || 0}%</span>
          ${detail}
          <button class="btn-link" type="button" data-remove="${index}">移除</button>
        </div>`;
      })
      .join('');

    const done = queue.filter((item) => item.status === 'done').length;
    const rejected = queue.filter((item) => item.status === 'rejected' || item.status === 'error').length;
    summaryBox.textContent = `共 ${fmtNumber(queue.length)} 个文件，已入库 ${fmtNumber(done)} 个，失败 ${fmtNumber(rejected)} 个`;
  };

  /** 加入文件（客户端先按扩展名预筛，避免把明显不支持的文件发到后端） */
  const addFiles = (files) => {
    let unsupported = 0;
    Array.from(files).forEach((file) => {
      if (!ACCEPT_EXT.includes(extOf(file.name))) {
        unsupported += 1;
        // 不支持的格式直接在队列里标成「被拒绝」，与后端返回的 unsupported_format 语义一致
        queue.push({ file, status: 'rejected', percent: 0, reason: 'unsupported_format（前端预筛：仅支持 PDF/MD/DOCX/TXT）' });
        return;
      }
      queue.push({ file, status: 'pending', percent: 0 });
    });
    if (unsupported) toast(`${unsupported} 个文件格式不支持，已标记为拒绝`, 'warn');
    renderQueue();
  };

  // ---- 交互 1：点击 dropzone 触发文件选择 ----
  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    addFiles(fileInput.files);
    fileInput.value = ''; // 清空以便重复选择同一文件
  });

  // ---- 交互 2：拖拽（9.4 要求支持拖拽） ----
  ['dragenter', 'dragover'].forEach((type) => {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add('dragover');
    });
  });
  ['dragleave', 'drop'].forEach((type) => {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.remove('dragover');
    });
  });
  dropzone.addEventListener('drop', (event) => {
    if (event.dataTransfer && event.dataTransfer.files) addFiles(event.dataTransfer.files);
  });
  // 阻止浏览器把文件拖到页面其它位置时的默认下载行为
  ['dragover', 'drop'].forEach((type) => {
    container.addEventListener(type, (event) => event.preventDefault());
  });

  // ---- 交互 3：移除单个文件 ----
  listBox.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-remove]');
    if (!btn) return;
    const index = Number(btn.dataset.remove);
    // 上传中的条目不允许移除，避免请求回调写到已删除的对象上
    if (queue[index] && queue[index].status === 'uploading') {
      toast('该文件正在上传，无法移除', 'warn');
      return;
    }
    queue.splice(index, 1);
    renderQueue();
  });

  // ---- 交互 4：清空 ----
  $('[data-role="clear"]', container).addEventListener('click', () => {
    if (queue.some((item) => item.status === 'uploading' || item.status === 'parsing')) {
      toast('有文件正在处理中，请等待完成', 'warn');
      return;
    }
    queue.length = 0;
    renderQueue();
  });

  /** 处理单个文件：上传 → 落结果。返回 Promise，供并发池调度 */
  const processOne = async (item, category) => {
    item.status = 'uploading';
    item.percent = 0;
    renderQueue();
    try {
      // 第 1 步：单文件提交一次请求（8.4 支持单文件时只有一个 files 字段）
      const data = await importDocuments([item.file], category, (percent) => {
        item.percent = percent;
        renderQueue();
      });
      item.percent = 100;

      // 第 2 步：按 8.4 的 accepted / rejected 落结果
      const accepted = (data && data.accepted) || [];
      const rejected = (data && data.rejected) || [];
      if (accepted.length) {
        item.status = 'done';
        item.unitCode = accepted[0].unit_code || '';
        item.unitId = accepted[0].unit_id;
        item.chunkCount = accepted[0].chunk_count;
      } else if (rejected.length) {
        item.status = 'rejected';
        item.reason = rejected[0].reason || 'unknown';
      } else {
        item.status = 'rejected';
        item.reason = '响应中没有 accepted / rejected 明细';
      }
    } catch (error) {
      item.status = 'error';
      item.reason = error.message || '上传失败';
    }
    renderQueue();
  };

  // ---- 交互 5：开始导入（分片并发） ----
  $('[data-role="start"]', container).addEventListener('click', async () => {
    const category = $('[data-role="category"]', container).value.trim();
    const pending = queue.filter((item) => item.status === 'pending');
    if (!pending.length) {
      toast('没有待上传的文件', 'warn');
      return;
    }

    const startBtn = $('[data-role="start"]', container);
    startBtn.disabled = true;
    startBtn.textContent = '导入中…';

    // 第 1 步：按并发度切批，逐批 Promise.allSettled
    // 用 allSettled 而非 all：9.4 要求「单个失败不中断其余」
    for (let i = 0; i < pending.length; i += CONCURRENCY) {
      const batch = pending.slice(i, i + CONCURRENCY);
      await Promise.allSettled(batch.map((item) => processOne(item, category)));
    }

    // 第 2 步：汇总提示
    const done = queue.filter((item) => item.status === 'done').length;
    const failed = queue.filter((item) => item.status === 'rejected' || item.status === 'error').length;
    toast(`导入完成：成功 ${done} 个，失败 ${failed} 个`, failed ? 'warn' : 'success');

    startBtn.disabled = false;
    startBtn.textContent = '开始导入';
  });

  // 初始渲染
  renderQueue();
  return container;
}

/** 供列表页跳转使用的入口（导入完成后查看知识单元列表） */
export function goUnitList() {
  navigate('/knowledge/units');
}
