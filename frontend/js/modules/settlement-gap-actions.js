/**
 * 知识沉淀 · 知识缺口操作（一键建档 / 关联已有单元 / 忽略）
 *
 * 从 settlement-module.js 拆出（该文件保留页面外壳与两块列表的渲染）。
 *
 * 接口（第 14 章 #7 / 11.4）：
 *   `POST /api/settlement/knowledge-gaps/{id}/resolve`  补全
 *   `POST /api/settlement/knowledge-gaps/{id}/ignore`   忽略
 *
 * resolve 的两种用法（同一个接口，按是否传 unit_id 分派）：
 *   传 `unit_id`   → 关联已有知识单元；
 *   不传 `unit_id` → **一键建档**：标题缺省用缺口的问题模式，正文缺省由后端
 *                    用样本提问拼出骨架，然后新建单元并关联。
 *
 * 建档需要向量化组件（MinIO / Milvus）就绪，未就绪时后端返回 503 —— 这类错误
 * 原样展示，管理员才知道该去检查什么，而不是看到一句「操作失败」。
 */

import { resolveGap, ignoreGap } from '../api/settlement.js';
import { esc, $, toast, openModal, confirmDialog, fmtNumber } from '../core/dom.js';

/**
 * 打开「补全知识缺口」弹窗。
 * @param {object} gap 缺口行 { id, question_pattern, ask_count, sample_questions }
 * @param {Function} onDone 成功回调（用于刷新缺口列表）
 */
export function openGapResolveModal(gap, onDone) {
  const samples = gap.sample_questions || [];

  openModal({
    title: `补全知识缺口 · #${gap.id}`,
    size: 'lg',
    bodyHtml: `
      <div class="form-error hidden" data-role="error"></div>

      <div class="review-panel mb16">
        <div class="row-between mb8">
          <strong>${esc(gap.question_pattern || '')}</strong>
          <span class="tag tag-warn">提问 ${esc(fmtNumber(gap.ask_count || 0))} 次</span>
        </div>
        ${
          samples.length
            ? `<div class="mute-sm mb8">样本提问：</div>
               <ul class="mute-sm">${samples.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>`
            : ''
        }
      </div>

      <div class="field">
        <label>补全方式</label>
        <div class="check-grid">
          <label class="check-item">
            <input type="radio" name="mode" value="create" checked />
            <span>一键建档（新建知识单元）</span>
          </label>
          <label class="check-item">
            <input type="radio" name="mode" value="link" />
            <span>关联已有知识单元</span>
          </label>
        </div>
      </div>

      <div data-role="create-panel">
        <div class="field">
          <label>标题</label>
          <input class="input" data-role="title" type="text" maxlength="255"
                 placeholder="留空则用缺口的问题模式作为标题" />
        </div>
        <div class="field">
          <label>正文</label>
          <textarea class="textarea textarea-lg" data-role="content"
                    placeholder="留空则由后端用样本提问拼出骨架，之后再人工补充标准答案"></textarea>
        </div>
        <div class="field">
          <label>分类</label>
          <input class="input" data-role="category" type="text" maxlength="64"
                 placeholder="可留空，最长 64 字" />
        </div>
        <div class="field-hint">建档需要向量化组件就绪，未就绪时后端返回 503 并说明原因。</div>
      </div>

      <div data-role="link-panel" class="hidden">
        <div class="field">
          <label>已有知识单元 ID<span class="req">*</span></label>
          <input class="input" data-role="unit-id" type="number" min="1" placeholder="例如 12" />
          <div class="field-hint">可在「知识单元列表」页查到单元 ID；补全后缺口状态变为 resolved 并记录该单元。</div>
        </div>
      </div>
    `,
    okText: '提交',
    onMount: (body) => {
      // 第 1 步：切换两种补全方式的面板
      body.addEventListener('change', () => {
        const mode = body.querySelector('input[name="mode"]:checked').value;
        $('[data-role="create-panel"]', body).classList.toggle('hidden', mode !== 'create');
        $('[data-role="link-panel"]', body).classList.toggle('hidden', mode !== 'link');
      });
    },
    onOk: async (body) => {
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');
      const mode = body.querySelector('input[name="mode"]:checked').value;

      // 第 2 步：组装载荷。一键建档时三个字段都可空（后端有缺省逻辑），
      // 因此空值不提交，让后端的缺省生效
      let payload = {};
      if (mode === 'link') {
        const raw = $('[data-role="unit-id"]', body).value.trim();
        if (!raw) {
          errorBox.textContent = '关联已有单元时必须填写知识单元 ID';
          errorBox.classList.remove('hidden');
          return false;
        }
        payload = { unit_id: Number(raw) };
      } else {
        const title = $('[data-role="title"]', body).value.trim();
        const content = $('[data-role="content"]', body).value.trim();
        const category = $('[data-role="category"]', body).value.trim();
        if (title) payload.title = title;
        if (content) payload.content = content;
        if (category) payload.category = category;
      }

      try {
        const data = await resolveGap(gap.id, payload);
        // 第 3 步：建档成功时把新单元编号报出来（管理员据此再去补正文与权限）
        if (data && data.created_unit_code) {
          toast(`已建档并关联，新单元编号：${data.created_unit_code}`, 'success');
        } else {
          toast(`已关联知识单元 #${(data && data.resolved_unit_id) || ''}`, 'success');
        }
        if (onDone) onDone();
        return true;
      } catch (error) {
        errorBox.textContent = error.message || '补全失败';
        errorBox.classList.remove('hidden');
        return false;
      }
    },
  });
}

/**
 * 忽略知识缺口（二次确认）。
 * @param {object} gap 缺口行
 * @param {Function} onDone 成功回调
 */
export async function confirmIgnoreGap(gap, onDone) {
  const ok = await confirmDialog(
    `确认忽略缺口「${gap.question_pattern}」？记录会保留，但不再出现在未解决清单里。`,
    '忽略',
  );
  if (!ok) return;
  try {
    const data = await ignoreGap(gap.id);
    toast(`已忽略（状态：${(data && data.status) || 'ignored'}）`, 'success');
    if (onDone) onDone();
  } catch (error) {
    toast(error.message || '忽略失败', 'error');
  }
}
