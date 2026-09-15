/**
 * 统一 HTTP 客户端（对应文档 8.1 通用约定）
 *
 * 职责：
 *   1. 后端基址集中在本文件的 BASE_URL，改一处即可全站生效；
 *   2. 请求拦截：自动附加 `Authorization: Bearer <token>`；
 *   3. 响应拦截：拆开 `{code, message, data}` 包装，业务失败抛 ApiError；
 *   4. 401 统一处理：清会话 + 跳登录页；
 *   5. 提供带上传进度的 XHR 分支（导入接口需要真实的上传进度）。
 *
 * 约定：本文件之外的任何模块都不允许直接调用 fetch，避免认证头漏加。
 */

import { getToken, clearSession } from '../core/store.js';

/** 后端基址。部署到别的地址时只改这一行 */
export const BASE_URL = 'http://127.0.0.1:8000';

/** API 前缀（8.1：基址为 /api） */
const API_PREFIX = '/api';

/** 业务异常：携带 HTTP 状态码与后端 message，便于页面区分处理 */
export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

/** 拼接完整地址；path 传 '/api/auth/login' 或 '/auth/login' 均可 */
function resolveUrl(path) {
  const suffix = path.startsWith('/api') ? path : `${API_PREFIX}${path}`;
  return `${BASE_URL}${suffix}`;
}

/** 构造基础请求头（含认证头） */
function buildHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

/**
 * 处理 401：清空会话并回登录页。
 * 只处理 401 —— 403（无操作权限）不能跳登录，否则用户会被莫名其妙踢出去。
 */
function handleUnauthorized() {
  clearSession();
  if (window.location.hash !== '#/login') {
    window.location.hash = '/login';
  }
}

/** 统一的响应解包：校验 code 并把 data 交给调用方 */
function unwrap(payload, status) {
  // 个别接口（例如未来可能的文件下载）可能直接返回非包装结构，这里做兜底
  if (!payload || typeof payload !== 'object' || !('code' in payload)) {
    return payload;
  }
  if (payload.code === 0) return payload.data;
  throw new ApiError(payload.message || '请求失败', status, payload.code);
}

/**
 * 发起 JSON 请求。
 * @param {string} method HTTP 方法
 * @param {string} path   接口路径（不含基址）
 * @param {object} body   请求体；undefined 表示不带体
 * @param {object} query  查询参数，非空值才会拼进 URL
 */
export async function request(method, path, { body, query } = {}) {
  // 第 1 步：拼查询串，过滤掉 null/undefined/空串，避免发出 `?title=undefined`
  const url = new URL(resolveUrl(path));
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === null || value === undefined || value === '') continue;
      url.searchParams.set(key, value);
    }
  }

  // 第 2 步：发请求
  let response;
  try {
    response = await fetch(url.toString(), {
      method,
      headers: buildHeaders(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (networkError) {
    throw new ApiError(
      `无法连接后端服务（${BASE_URL}），请确认服务已启动。原始错误：${networkError.message}`,
      0,
      null,
    );
  }

  // 第 3 步：401 统一跳登录
  if (response.status === 401) {
    handleUnauthorized();
    throw new ApiError('登录状态已失效，请重新登录', 401, null);
  }

  // 第 4 步：解析响应体。204 或空体按 null 处理
  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { code: response.ok ? 0 : response.status, message: text, data: null };
    }
  }

  // 第 5 步：HTTP 层失败（403/404/422/500）
  if (!response.ok) {
    const message = (payload && (payload.message || payload.detail)) || `HTTP ${response.status}`;
    throw new ApiError(typeof message === 'string' ? message : JSON.stringify(message), response.status, payload && payload.code);
  }

  // 第 6 步：业务层解包
  return unwrap(payload, response.status);
}

export const get = (path, query) => request('GET', path, { query });
export const post = (path, body) => request('POST', path, { body });
export const put = (path, body) => request('PUT', path, { body });
export const del = (path, body) => request('DELETE', path, { body });

/**
 * 带上传进度的 multipart 请求（仅用于 `POST /api/knowledge/import`）。
 *
 * 为什么单独走 XHR：9.4 要求导入中心有进度展示，而 fetch 目前拿不到
 * 上传阶段的进度事件；XMLHttpRequest 的 upload.onprogress 可以。
 * 注意这只是**上传字节**的进度，解析阶段的进度依赖的接口未在 8 章列出，
 * 页面上另有「待接口确认」占位说明。
 *
 * @param {string} path
 * @param {FormData} formData
 * @param {(percent:number)=>void} onProgress 上传进度回调，0~100
 * @returns {Promise<any>} 解包后的 data
 */
export function upload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', resolveUrl(path), true);

    // 第 1 步：附加认证头（与 request 保持一致）
    const token = getToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);

    // 第 2 步：上传进度回调
    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
    }

    // 第 3 步：完成回调，按与 request 相同的规则解包
    xhr.onload = () => {
      if (xhr.status === 401) {
        handleUnauthorized();
        reject(new ApiError('登录状态已失效，请重新登录', 401, null));
        return;
      }
      let payload = null;
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        payload = null;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const message = (payload && (payload.message || payload.detail)) || `HTTP ${xhr.status}`;
        reject(new ApiError(typeof message === 'string' ? message : JSON.stringify(message), xhr.status, payload && payload.code));
        return;
      }
      try {
        resolve(unwrap(payload, xhr.status));
      } catch (error) {
        reject(error);
      }
    };

    // 第 4 步：网络层错误
    xhr.onerror = () => reject(new ApiError(`无法连接后端服务（${BASE_URL}）`, 0, null));
    xhr.send(formData);
  });
}
