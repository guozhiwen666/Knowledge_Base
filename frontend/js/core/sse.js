/**
 * SSE 流式请求（对应 9.4「流式渲染」与文档 4.8 的七个事件）
 *
 * 为什么不用 EventSource：EventSource 只支持 GET，而
 * `POST /api/ai/chat/stream` 是 POST + JSON body（8.5），
 * 因此必须用 `fetch` + `ReadableStream` 手动解析 SSE 文本帧。
 *
 * 解析规则（按 SSE 规范的最小集实现）：
 *   - 一个「帧」以空行分隔（\n\n 或 \r\n\r\n）
 *   - 帧内 `event:` 行给出事件名，可有多行 `data:`，按 \n 拼接
 *   - `:` 开头的行是注释（心跳），忽略
 *
 * 支持的事件名（4.8）：session / faq_hit / citation /
 * permission_notice / delta / done / error
 */

import { getToken } from './store.js';

/**
 * 发起一次流式问答请求。
 *
 * @param {object} opts
 *   url     完整的请求地址
 *   body    请求体对象（JSON 序列化后发送）
 *   onEvent (eventName, dataObject, raw) => void
 *           每个事件到达时回调；dataObject 是 data 的 JSON 解析结果，
 *           解析失败时给出 { raw: <原始字符串> } 以免丢掉内容。
 *   onClose () => void 流正常结束
 *   onError (Error) => void 网络或 HTTP 层异常
 * @returns {{abort: Function}} 可中断句柄（页面切走时必须调用）
 */
export function streamSSE({ url, body, onEvent, onClose, onError }) {
  const controller = new AbortController();

  // 第 1 步：用一个立即执行的异步函数串起整个读取流程；
  // 不 await 它，把中断句柄同步返回给调用方
  (async () => {
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          // 8.1 认证约定：除 login 外全部要带 Bearer
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify(body || {}),
        signal: controller.signal,
      });

      // 第 2 步：HTTP 层错误（401/403/422/500）在拿到流之前就要处理掉
      if (!response.ok) {
        let detail = '';
        try {
          detail = await response.text();
        } catch {
          detail = '';
        }
        const error = new Error(`流式请求失败：HTTP ${response.status}${detail ? ` - ${detail.slice(0, 200)}` : ''}`);
        error.status = response.status;
        throw error;
      }
      if (!response.body) {
        throw new Error('当前浏览器不支持 ReadableStream，无法解析流式响应');
      }

      // 第 3 步：逐块读取并解码。用 TextDecoder 的 stream 选项处理跨块的多字节字符
      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // 第 4 步：切出完整的帧，最后一段可能不完整，留在 buffer 里等下一块
        let boundary = findBoundary(buffer);
        while (boundary.index !== -1) {
          const frame = buffer.slice(0, boundary.index);
          buffer = buffer.slice(boundary.index + boundary.length);
          dispatchFrame(frame, onEvent);
          boundary = findBoundary(buffer);
        }
      }

      // 第 5 步：流结束时 buffer 里可能还剩最后一帧（服务端没补尾空行）
      buffer += decoder.decode();
      if (buffer.trim()) dispatchFrame(buffer, onEvent);

      if (onClose) onClose();
    } catch (error) {
      // 主动中断不算错误，静默返回
      if (error && error.name === 'AbortError') return;
      if (onError) onError(error);
    }
  })();

  return { abort: () => controller.abort() };
}

/** 找到下一个帧边界（空行）；兼容 \n\n 与 \r\n\r\n 两种写法 */
function findBoundary(text) {
  const lf = text.indexOf('\n\n');
  const crlf = text.indexOf('\r\n\r\n');
  if (lf === -1 && crlf === -1) return { index: -1, length: 0 };
  if (crlf !== -1 && (lf === -1 || crlf < lf)) return { index: crlf, length: 4 };
  return { index: lf, length: 2 };
}

/** 解析单个帧并回调事件 */
function dispatchFrame(frame, onEvent) {
  if (!frame || !frame.trim() || !onEvent) return;

  const dataLines = [];
  let eventName = 'message'; // SSE 默认事件名

  for (const rawLine of frame.split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    if (!line || line.startsWith(':')) continue; // 空行与注释（心跳）跳过

    const colon = line.indexOf(':');
    // 没有冒号的行按「字段名 + 空值」处理（SSE 规范）
    const field = colon === -1 ? line : line.slice(0, colon);
    // 规范：冒号后若跟一个空格，该空格需要去掉，只去掉一个
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);

    if (field === 'event') eventName = value;
    else if (field === 'data') dataLines.push(value);
  }

  if (!dataLines.length) return;

  // 多行 data 按换行拼接（SSE 规范），再尝试 JSON 解析
  const dataText = dataLines.join('\n');
  let data;
  try {
    data = JSON.parse(dataText);
  } catch {
    // 后端若直接下发纯文本，退化成 { raw: 文本 }，前端仍能渲染
    data = { raw: dataText };
  }
  onEvent(eventName, data, dataText);
}
