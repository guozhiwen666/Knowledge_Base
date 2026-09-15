/**
 * 知识沉淀管理 · FAQ 审核弹窗
 *
 * 从 settlement-module.js 拆出。
 *
 * 接口：`POST /api/settlement/faqs/{id}/review`（8.7）
 *   通过 approve -> 置 published、写 reviewer_id / reviewed_at、写入 FAQ 缓存（11.3）
 *   驳回 reject  -> 置 rejected、让缓存失效
 *   edited_answer 非空时覆盖原答案（挖掘阶段不建议答案，suggested_answer 通常为 null）
 *
 * 交互设计说明：两个动作语义相反，因此「驳回」放在弹窗底部左侧（次级位置），
 * 「通过并发布」用主色按钮放在右侧（主操作位置），避免误点。
 */

import { reviewFaq } from '../api/settlement.js';
import { el, esc, $, toast, openModal, fmtNumber } from '../core/dom.js';

/**
 * 打开 FAQ 审核弹窗。
 * @param {object} item 推荐项 { id, question, frequency, related_unit_id, suggested_answer }
 * @param {Function} onDone 审核完成后的刷新回调
 */
export function openReviewModal(item, onDone) {
  openModal({
    title: `FAQ 审核 · #${item.id}`,
    size: 'lg',
    bodyHtml: `
      <div class="form-error hidden" data-role="error"></div>

      <div class="review-panel mb16">
        <div class="row-between mb8">
          <strong>${esc(item.question || '')}</strong>
          ${item.frequency ? `<span class="tag tag-primary">推荐频次 ${esc(fmtNumber(item.frequency))} 次</span>` : ''}
        </div>
        <div class="row">
          <span class="mute-sm">关联知识单元：</span>
          ${
            item.related_unit_id
              ? `<span class="tag mono">#${esc(item.related_unit_id)}</span>`
              : '<span class="mute-sm">未关联</span>'
          }
        </div>
      </div>

      <div class="field">
        <label>标准答案</label>
        <textarea class="textarea textarea-lg" data-role="answer"
                  placeholder="填写标准答案；通过后该答案将作为 FAQ 缓存的标准答复">${esc(item.suggested_answer || '')}</textarea>
        <div class="field-hint">
          对应 8.7 的 <code>edited_answer</code>：非空时覆盖原答案并写入缓存；
          留空则沿用挖掘阶段已有的答案（通常为空，因此建议填写）。
        </div>
      </div>

      <div class="pending-block">
        <span class="pending-title">审核可编辑性</span>
        审核弹窗内可编辑标准答案与选择通过 / 驳回，这两项均有 8.7 的接口支撑；
        待审核项本身没有编辑或删除接口，因此不提供这两项操作。
      </div>
    `,
    okText: '通过并发布',
    onOk: async (body) => {
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');
      // 第 1 步：读取编辑后的答案（允许留空）
      const editedAnswer = $('[data-role="answer"]', body).value.trim();
      try {
        // 第 2 步：approve 动作（8.7）
        await reviewFaq(item.id, 'approve', editedAnswer);
        toast('已通过并发布，问答对已写入缓存', 'success');
        if (onDone) onDone();
        return true;
      } catch (error) {
        errorBox.textContent = error.message || '审核失败';
        errorBox.classList.remove('hidden');
        return false;
      }
    },
    onMount: (body) => bindRejectButton(body, item, onDone),
  });
}

/** 动态插入「驳回」按钮（通用弹窗只有取消/确定两个动作位） */
function bindRejectButton(body, item, onDone) {
  const foot = body.parentElement.querySelector('.modal-foot');
  if (!foot) return;

  const rejectBtn = el('<button class="btn btn-danger" type="button">驳回</button>');
  foot.insertBefore(rejectBtn, foot.firstElementChild);

  rejectBtn.addEventListener('click', async () => {
    const errorBox = $('[data-role="error"]', body);
    rejectBtn.disabled = true;
    try {
      // reject 动作不传 edited_answer（8.7 只规定 approve 时覆盖答案）
      await reviewFaq(item.id, 'reject');
      toast('已驳回', 'success');
      const mask = body.closest('.modal-mask');
      if (mask) mask.remove();
      if (onDone) onDone();
    } catch (error) {
      errorBox.textContent = error.message || '驳回失败';
      errorBox.classList.remove('hidden');
      rejectBtn.disabled = false;
    }
  });
}
