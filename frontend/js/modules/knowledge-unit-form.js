/**
 * 知识单元列表 · 手工新建弹窗
 *
 * 从 knowledge-unit-list.js 拆出（该文件保留列表、筛选、分页与行内操作）。
 *
 * 接口：`POST /api/knowledge/units`（第 14 章 #2）
 *   请求字段：title（必填，≤255）/ content? / category?（≤64）/ summary?（≤1000）
 *   响应字段：{ id, unit_code, title, category, status, created_at }
 *
 * 与「导入」的分工：导入由文件解析出正文，来源字段（source_file_name /
 * file_type / file_size）由后端填写；手工新建不提交这三个字段，
 * 后端也留空 —— 数据上就能区分「上传而来」与「人工录入」。
 *
 * 正文非空时后端会同步切片并写入向量库（否则新建出来的单元检索不到），
 * 因此该接口依赖 MinIO / Milvus 就绪，未就绪时返回 503，页面原样提示。
 */

import { createUnit } from '../api/knowledge.js';
import { $, toast, openModal } from '../core/dom.js';

/**
 * 打开「新建知识单元」弹窗。
 * @param {(created:object)=>void} onCreated 创建成功回调，参数为后端返回的单元
 */
export function openUnitCreateModal(onCreated) {
  openModal({
    title: '新建知识单元',
    size: 'lg',
    bodyHtml: `
      <div class="form-error hidden" data-role="error"></div>
      <p class="page-desc">
        标题必填；分类、摘要、正文可留空。正文非空时后端会立即切片并写入向量库。
      </p>

      <div class="field">
        <label>标题<span class="req">*</span></label>
        <input class="input" data-role="title" type="text" placeholder="最长 255 字" />
      </div>
      <div class="field">
        <label>分类</label>
        <input class="input" data-role="category" type="text" placeholder="可留空，最长 64 字" />
      </div>
      <div class="field">
        <label>摘要</label>
        <textarea class="textarea" data-role="summary" placeholder="可留空，最长 1000 字"></textarea>
      </div>
      <div class="field">
        <label>正文</label>
        <textarea class="textarea textarea-lg" data-role="content" placeholder="知识单元正文（可留空）"></textarea>
        <div class="field-hint">
          保存后该单元即可被 AI 问答检索到（需另行配置数据权限，默认拒绝，见 6.2）。
        </div>
      </div>
    `,
    okText: '创建',
    onOk: async (body) => {
      // 第 1 步：清掉上一次的错误提示
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');

      // 第 2 步：只做「必填项」这一层前端校验，长度与取值交给后端裁定（超限返回 422）
      const title = $('[data-role="title"]', body).value.trim();
      if (!title) {
        errorBox.textContent = '标题不能为空';
        errorBox.classList.remove('hidden');
        return false;
      }

      // 第 3 步：可选字段留空时不提交，避免写入空串
      const payload = { title };
      const category = $('[data-role="category"]', body).value.trim();
      const summary = $('[data-role="summary"]', body).value.trim();
      const content = $('[data-role="content"]', body).value;
      if (category) payload.category = category;
      if (summary) payload.summary = summary;
      if (content) payload.content = content;

      try {
        const created = await createUnit(payload);
        toast(`创建成功：${(created && created.unit_code) || title}`, 'success');
        if (onCreated) onCreated(created);
        return true;
      } catch (error) {
        errorBox.textContent = error.message || '创建失败';
        errorBox.classList.remove('hidden');
        return false;
      }
    },
  });
}
