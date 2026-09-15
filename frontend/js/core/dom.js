/**
 * DOM 与通用工具（纯展示层工具，不含任何业务判断）
 *
 * 职责：
 *   esc       —— HTML 转义，所有插入 innerHTML 的动态数据都必须过一遍，防 XSS
 *   el/$/$$    —— 元素创建与查询
 *   toast     —— 轻提示
 *   openModal —— 通用模态框（表单弹窗、权限配置弹窗复用）
 *   loading/emptyState/pendingBlock —— 三种通用占位态
 *   fmt*      —— 数字与时间格式化
 *
 * 风格约定：页面视图统一用「模板字符串 + innerHTML」渲染，配合事件委托绑定交互。
 * 这样在没有框架的前提下依然能保持渲染函数短小，代价是必须严格转义。
 */

/** HTML 转义：& < > " ' 五个字符，任何拼进模板的动态值都要调用 */
export function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 用 HTML 字符串创建元素 */
export function el(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}

/** 单元素查询 */
export function $(selector, root = document) {
  return root.querySelector(selector);
}

/** 多元素查询，返回真数组（便于用 map/forEach） */
export function $$(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

/** 轻提示；type 取 info / success / warn / error */
export function toast(message, type = 'info', duration = 2600) {
  const host = document.getElementById('toast-host');
  if (!host) return;
  const node = el(`<div class="toast ${esc(type)}">${esc(message)}</div>`);
  host.appendChild(node);
  setTimeout(() => node.remove(), duration);
}

/**
 * 通用模态框。
 * @param {object} opts
 *   title    标题
 *   bodyHtml 内容 HTML（调用方负责转义）
 *   size     'lg' 时用宽弹窗
 *   okText   确定按钮文案；传 null 表示不显示确定按钮
 *   onOk     (modalBodyEl) => boolean|Promise<boolean|void>
 *            返回 false 时不关闭弹窗（用于表单校验失败）
 *   onMount  (modalBodyEl) => void 挂载后回调，用于绑定弹窗内的事件
 * @returns {{close: Function, body: HTMLElement}}
 */
export function openModal({ title, bodyHtml, size, okText = '确定', onOk, onMount }) {
  const host = document.getElementById('modal-host');
  const mask = el(`
    <div class="modal-mask">
      <div class="modal ${size === 'lg' ? 'modal-lg' : ''}">
        <div class="modal-head">
          <span>${esc(title || '')}</span>
          <button class="modal-close" type="button" aria-label="关闭">&times;</button>
        </div>
        <div class="modal-body">${bodyHtml || ''}</div>
        <div class="modal-foot">
          <button class="btn" type="button" data-role="cancel">取消</button>
          ${okText ? `<button class="btn btn-primary" type="button" data-role="ok">${esc(okText)}</button>` : ''}
        </div>
      </div>
    </div>
  `);

  const body = $('.modal-body', mask);
  const close = () => mask.remove();

  // 第 1 步：关闭动作（右上角 ×、取消按钮、点击遮罩空白处）
  $('.modal-close', mask).addEventListener('click', close);
  $('[data-role="cancel"]', mask).addEventListener('click', close);
  mask.addEventListener('click', (event) => {
    if (event.target === mask) close();
  });

  // 第 2 步：确定按钮：由 onOk 决定是否关闭（返回 false 表示校验未通过）
  const okBtn = $('[data-role="ok"]', mask);
  if (okBtn) {
    okBtn.addEventListener('click', async () => {
      okBtn.disabled = true;
      try {
        const result = onOk ? await onOk(body) : true;
        if (result !== false) close();
      } finally {
        okBtn.disabled = false;
      }
    });
  }

  host.appendChild(mask);
  if (onMount) onMount(body);
  // 第 3 步：自动聚焦第一个可输入控件，减少一次点击
  const firstInput = $('input, textarea, select', body);
  if (firstInput) firstInput.focus();

  return { close, body };
}

/**
 * 确认框（危险操作二次确认）。返回 Promise<boolean>。
 */
export function confirmDialog(message, okText = '确定') {
  return new Promise((resolve) => {
    const ctrl = openModal({
      title: '操作确认',
      bodyHtml: `<p>${esc(message)}</p>`,
      okText,
      onOk: () => {
        resolve(true);
        ctrl.close();
        return false; // 由这里统一关闭，避免重复 remove
      },
    });
    // 取消 / 关闭一律视为未确认
    const mask = document.getElementById('modal-host').lastElementChild;
    mask.addEventListener('click', (event) => {
      if (event.target.closest('[data-role="cancel"]') || event.target.closest('.modal-close') || event.target === mask) {
        resolve(false);
      }
    });
  });
}

/** 加载态 HTML */
export function loadingState(text = '加载中…') {
  return `<div class="loading-state"><span class="spinner"></span>${esc(text)}</div>`;
}

/** 空态 HTML：图表与列表都用它，避免出现空白画布 */
export function emptyState(text = '暂无数据', icon = '[ ]') {
  return `<div class="empty-state"><span class="ico">${esc(icon)}</span>${esc(text)}</div>`;
}

/**
 * 「该功能待接口确认」占位块。
 * 使用前提：文档第 8 章的 21 个接口里确实没有能支撑该数据源的接口。
 * 这里必须如实标注缺失的是哪个接口的哪类数据，不允许自造端点补位。
 */
export function pendingBlock(title, detail, missing = '') {
  return `
    <div class="pending-block">
      <span class="pending-title">${esc(title)}：该功能待接口确认</span>
      ${esc(detail)}
      ${missing ? `<div class="mute-sm mt8">缺失数据源：<code>${esc(missing)}</code></div>` : ''}
    </div>
  `;
}

/** 数字千分位 */
export function fmtNumber(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '0';
  return num.toLocaleString('zh-CN');
}

/** 保留 n 位小数（默认 0 位），用于平均耗时这类指标 */
export function fmtFixed(value, digits = 0) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '0';
  return num.toFixed(digits);
}

/** 时间格式化：后端返回 ISO 或 MySQL DATETIME 字符串，统一裁到分钟 */
export function fmtDateTime(value) {
  if (!value) return '-';
  const raw = String(value).replace('T', ' ');
  // 去掉毫秒与时区尾巴，只保留 YYYY-MM-DD HH:mm
  return raw.length >= 16 ? raw.slice(0, 16) : raw;
}

/** 文件体积格式化 */
export function fmtFileSize(bytes) {
  const num = Number(bytes);
  if (!Number.isFinite(num) || num <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB'];
  let index = 0;
  let size = num;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}
