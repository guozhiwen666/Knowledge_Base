/**
 * 知识导入 · 文件队列模型与渲染
 *
 * 从 knowledge-import.js 拆出。导入中心有两段进度（上传字节 / 后台解析），
 * 但它们描述的是**同一个文件**，因此队列只有一份状态，
 * 每项字段：
 *   file        原始 File 对象
 *   status      pending | uploading | parsing | done | rejected | error
 *   percent     当前阶段的百分比（上传阶段为字节进度，解析阶段为后端进度）
 *   stage       解析阶段（queued/starting/parsing/chunking/embedding/indexing/...）
 *   taskId      解析任务标识，来自导入响应的 accepted[].task_id
 *   unitCode    入库后的单元编号（解析完成才有）
 *   chunkCount  切片数（解析完成才有）
 *   reason      被拒绝或失败的原因（原样展示后端 message）
 *
 * 渲染与模型放在一起：表格列口径就是状态字段的展开，放在同一个文件里
 * 才能保证「新增状态」时不会只改一半。
 */

import { esc, fmtFileSize, fmtNumber } from '../core/dom.js';

/** 队列状态 → 标签 */
const STATUS_TAG = {
  pending: '<span class="tag">待上传</span>',
  uploading: '<span class="tag tag-primary">上传中</span>',
  parsing: '<span class="tag tag-primary">解析中</span>',
  done: '<span class="tag tag-success">已入库</span>',
  rejected: '<span class="tag tag-danger">被拒绝</span>',
  error: '<span class="tag tag-danger">失败</span>',
};

/** 解析阶段的中文名（stage 取值见 backend import_tasks.py 的状态机） */
export const STAGE_LABEL = {
  queued: '排队中',
  starting: '启动',
  parsing: '解析文本',
  chunking: '切片',
  embedding: '向量化',
  indexing: '建索引',
  completed: '完成',
  failed: '失败',
};

/** 支持的文件扩展名（8.4：仅 PDF、Markdown、Word、TXT） */
export const ACCEPT_EXT = ['pdf', 'md', 'markdown', 'docx', 'doc', 'txt'];

/** 取文件扩展名（小写，不含点） */
export function extOf(name) {
  const index = String(name).lastIndexOf('.');
  return index === -1 ? '' : name.slice(index + 1).toLowerCase();
}

/** 新增一个待上传条目 */
export function createEntry(file) {
  return { file, status: 'pending', percent: 0, stage: '', taskId: '', reason: '', unitCode: '', chunkCount: 0 };
}

/**
 * 把导入接口的响应写回条目（8.4 的新契约：accepted 只给 task_id，rejected 给原因）。
 *
 * 入库结果（unit_code / chunk_count）不在这一步 —— 解析在后台线程跑，
 * 只能由 applyProgressRecord() 从进度接口补齐。
 */
export function applyImportResult(item, data) {
  const accepted = (data && data.accepted) || [];
  const rejected = (data && data.rejected) || [];
  if (accepted.length) {
    item.taskId = accepted[0].task_id;
    item.status = 'parsing';
    item.stage = 'queued';
    item.percent = 0; // 解析进度从 0 起算，与上传进度分开计
    return;
  }
  item.status = 'rejected';
  item.reason = rejected.length ? rejected[0].reason || 'unknown' : '响应中没有 accepted / rejected 明细';
}

/** 把进度接口的一条记录写回条目（终态只有 completed / failed） */
export function applyProgressRecord(item, record) {
  if (record.stage) item.stage = record.stage;
  const percent = Number(record.percent);
  if (Number.isFinite(percent)) item.percent = Math.max(0, Math.min(100, percent));

  if (record.status === 'completed') {
    item.status = 'done';
    item.percent = 100;
    item.unitCode = record.unit_code || '';
    item.chunkCount = record.chunk_count || 0;
  } else if (record.status === 'failed') {
    item.status = 'error';
    item.reason = record.reason || '解析失败';
  } else {
    item.status = 'parsing';
  }
}

/** 进度接口回传的「查不到的任务」如实标注，而不是丢掉 */
export function markMissingTask(item) {
  if (item.status === 'done') return;
  item.status = 'error';
  item.reason = '解析任务记录不存在（缓存中已清理或标识已失效）';
}

/**
 * 绑定上传区交互（点击选择 + 拖拽），把选中的文件交给 onFiles。
 *
 * 9.4 要求支持拖拽；顺带在 host 上拦掉浏览器默认的「拖入即打开文件」行为，
 * 否则用户拖偏一点就会跳走页面。
 *
 * @param {object} opts
 *   dropzone   拖拽/点击区域
 *   fileInput  隐藏的 file 控件（multiple）
 *   host       整页容器，用于拦浏览器默认拖放
 *   onFiles    (FileList) => void
 */
export function bindDropzone({ dropzone, fileInput, host, onFiles }) {
  // 第 1 步：点击选择
  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    onFiles(fileInput.files);
    fileInput.value = ''; // 清空以便重复选择同一文件
  });

  // 第 2 步：拖入态高亮
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

  // 第 3 步：落下即入队
  dropzone.addEventListener('drop', (event) => {
    if (event.dataTransfer && event.dataTransfer.files) onFiles(event.dataTransfer.files);
  });

  // 第 4 步：阻止浏览器把文件拖到页面其它位置时的默认下载行为
  ['dragover', 'drop'].forEach((type) => {
    host.addEventListener(type, (event) => event.preventDefault());
  });
}

/** 汇总一行文案：共 N 个文件，已入库 X 个，失败 Y 个 */
export function summarizeQueue(items) {
  if (!items.length) return '';
  const done = items.filter((item) => item.status === 'done').length;
  const failed = items.filter((item) => item.status === 'rejected' || item.status === 'error').length;
  return `共 ${fmtNumber(items.length)} 个文件，已入库 ${fmtNumber(done)} 个，失败 ${fmtNumber(failed)} 个`;
}

/** 渲染整个队列表格（队列通常只有个位数文件，整体重渲染比增量 diff 简单可靠） */
export function renderQueueTableHtml(items) {
  return `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th>文件</th>
            <th style="width:86px">大小</th>
            <th style="width:92px">状态</th>
            <th style="width:96px">阶段</th>
            <th style="width:190px">进度</th>
            <th>结果</th>
            <th style="width:64px">操作</th>
          </tr>
        </thead>
        <tbody>${items.map((item, index) => renderRow(item, index)).join('')}</tbody>
      </table>
    </div>
  `;
}

/** 渲染单行；index 用作「移除」按钮的定位锚点（与队列下标一致） */
function renderRow(item, index) {
  const taskLine = item.taskId
    ? `<div class="mute-sm mono" style="font-size:11px">${esc(item.taskId)}</div>`
    : '';

  // 阶段列：上传阶段与解析阶段分开描述，避免把「传输」说成「解析」
  const stage = item.status === 'uploading' ? '上传' : STAGE_LABEL[item.stage] || (item.status === 'done' ? '完成' : '-');

  const detail =
    item.status === 'rejected' || item.status === 'error'
      ? `<span class="mute-sm">${esc(item.reason || '未知原因')}</span>`
      : item.status === 'done'
        ? `<span class="tag tag-success">${esc(item.unitCode || '已入库')}</span>
           <span class="mute-sm">${esc(fmtNumber(item.chunkCount || 0))} 个切片</span>`
        : item.status === 'parsing'
          ? '<span class="mute-sm">解析中…</span>'
          : '<span class="mute-sm">待入库</span>';

  // 处理中的条目不允许移除，避免请求回调写到已删除的对象上
  const locked = item.status === 'uploading' || item.status === 'parsing';

  return `
    <tr>
      <td>
        <div title="${esc(item.file.name)}">${esc(item.file.name)}</div>
        ${taskLine}
      </td>
      <td class="mute-sm">${esc(fmtFileSize(item.file.size))}</td>
      <td>${STATUS_TAG[item.status] || STATUS_TAG.pending}</td>
      <td class="mute-sm">${esc(stage)}</td>
      <td>
        <span class="bar"><i style="width:${item.percent || 0}%"></i></span>
        <span class="mute-sm">${item.percent || 0}%</span>
      </td>
      <td>${detail}</td>
      <td>
        <button class="btn-link" type="button" data-remove="${index}"
                ${locked ? 'disabled title="上传或解析中，不能移除"' : ''}>移除</button>
      </td>
    </tr>
  `;
}
