/**
 * 知识维护与导入 · 知识单元列表
 *
 * 从 knowledge-module.js 拆出（该文件只保留路由转发）。
 * 表格与分页的模板拆在 unit-list-table.js。
 *
 * 接口落位（严格限定在 8 章列出的接口内）：
 *   `GET    /api/knowledge/units`                   列表分页查询（title / category / status / page / page_size）
 *   `GET    /api/knowledge/units/{id}`              取详情，用于权限弹窗回填
 *   `POST   /api/knowledge/units/{id}/permissions`  数据权限全量覆盖
 *   `DELETE /api/knowledge/units`                   批量删除
 *
 * **明确缺失、页面用占位说明的部分**：手工新建知识单元接口（8 章未列出）。
 */

import { listUnits, deleteUnits, getUnit } from '../api/knowledge.js';
import { hasPermission } from '../core/store.js';
import { navigate } from '../core/router.js';
import { el, esc, $, toast, confirmDialog, pendingBlock, emptyState, loadingState, fmtNumber } from '../core/dom.js';
import { openPermissionDialog } from './permission-dialog.js';
import { renderUnitTableHtml, renderPagerHtml } from './unit-list-table.js';

/** #/knowledge/units 页面渲染 */
export function renderUnitList() {
  // 第 1 步：同步返回骨架（筛选区立即可用），列表数据异步填充
  const container = el(`
    <div>
      <h2 class="page-title">知识单元列表</h2>
      <p class="page-desc">按标题、分类、状态查询；每行可查看详情、编辑、配置数据权限或删除</p>

      <div class="card">
        <div class="row-between">
          <div class="row">
            <input class="input" data-role="title" type="text" placeholder="标题模糊搜索" style="width:200px" />
            <input class="input" data-role="category" type="text" placeholder="分类（精确）" style="width:150px" />
            <select class="select" data-role="status" style="width:140px">
              <option value="">全部状态</option>
              <option value="active">active</option>
              <option value="archived">archived</option>
              <option value="draft">draft</option>
            </select>
            <button class="btn btn-primary" type="button" data-role="search">查询</button>
            <button class="btn" type="button" data-role="reset">重置</button>
          </div>
          <button class="btn" type="button" data-role="to-import">去导入中心</button>
        </div>
        <div class="field-hint mt8">
          状态取值口径：<code>knowledge_units.status</code> 的枚举属第 14 章【待确认】项（文档标注默认 active），
          故此处同时列出 active / archived / draft 三个候选值，查询为精确匹配。
        </div>
      </div>

      <div class="card">
        <div class="card-title">
          <span>查询结果</span>
          <div class="row">
            <span class="mute-sm" data-role="total"></span>
            <button class="btn btn-sm btn-danger" type="button" data-role="batch-delete" disabled>批量删除</button>
          </div>
        </div>
        <div data-role="table">${loadingState()}</div>
        <div class="pager" data-role="pager"></div>
      </div>

      <div class="card">
        <div class="card-title">手工新建知识单元</div>
        ${pendingBlock(
          '手工新建知识单元',
          '2.9.3 的知识维护包含「新增」，但 8.4 只列出了导入、列表、详情、更新、批量删除五个接口，没有手工新建接口。因此本页不提供新建按钮，新增内容请走导入中心上传文件。',
          'POST /api/knowledge/units（8 章未列出）',
        )}
      </div>
    </div>
  `);

  // 第 2 步：按钮级权限（6.1 操作权限）
  const canUpdate = hasPermission('knowledge:unit:update');
  const canDelete = hasPermission('knowledge:unit:delete');

  const state = { page: 1, page_size: 10, total: 0, items: [], selected: new Set() };
  const tableBox = $('[data-role="table"]', container);
  const pagerBox = $('[data-role="pager"]', container);
  const totalBox = $('[data-role="total"]', container);
  const batchBtn = $('[data-role="batch-delete"]', container);

  /** 读取筛选条件 */
  const readFilters = () => ({
    title: $('[data-role="title"]', container).value.trim(),
    category: $('[data-role="category"]', container).value.trim(),
    status: $('[data-role="status"]', container).value,
  });

  const syncBatchBtn = () => {
    batchBtn.disabled = !(canDelete && state.selected.size > 0);
    batchBtn.textContent = state.selected.size ? `批量删除（${state.selected.size}）` : '批量删除';
  };

  /** 渲染表格与分页 */
  const renderRows = () => {
    totalBox.textContent = `共 ${fmtNumber(state.total)} 条`;
    if (!state.items.length) {
      tableBox.innerHTML = emptyState('没有符合条件的知识单元');
      return;
    }
    tableBox.innerHTML = renderUnitTableHtml(state.items, canUpdate, canDelete);

    // 勾选联动「批量删除」按钮
    $('[data-role="check-all"]', tableBox).addEventListener('change', (event) => {
      tableBox.querySelectorAll('[data-role="check-one"]').forEach((box) => {
        box.checked = event.target.checked;
        if (box.checked) state.selected.add(String(box.value));
        else state.selected.delete(String(box.value));
      });
      syncBatchBtn();
    });
  };

  /** 拉取列表 */
  const load = async () => {
    tableBox.innerHTML = loadingState();
    try {
      // 8.1 分页约定：响应 { total, items }
      const data = await listUnits({ ...readFilters(), page: state.page, page_size: state.page_size });
      state.total = (data && data.total) || 0;
      state.items = (data && data.items) || [];
    } catch (error) {
      tableBox.innerHTML = `<div class="empty-state">加载失败：${esc(error.message)}</div>`;
      pagerBox.innerHTML = '';
      return;
    }
    // 换页后清掉上一页的勾选，避免误删到不可见的行
    state.selected.clear();
    renderRows();
    pagerBox.innerHTML = renderPagerHtml(state.page, state.page_size, state.total);
    syncBatchBtn();
  };

  // 第 3 步：行内操作（事件委托）
  tableBox.addEventListener('click', async (event) => {
    const box = event.target.closest('[data-role="check-one"]');
    if (box) {
      if (box.checked) state.selected.add(String(box.value));
      else state.selected.delete(String(box.value));
      syncBatchBtn();
      return;
    }

    const btn = event.target.closest('[data-act]');
    if (!btn) return;
    const id = btn.dataset.id;
    const unit = state.items.find((item) => String(item.id) === String(id));

    if (btn.dataset.act === 'detail' || btn.dataset.act === 'edit') {
      navigate(`/knowledge/units/${id}`);
      return;
    }

    if (btn.dataset.act === 'perm') {
      // 列表接口只返回 permission_summary 摘要，不含明细。
      // 权限弹窗必须回填真实已配实体，否则保存（全量覆盖）会把既有权限清空，
      // 因此先取一次详情（8.4 GET /api/knowledge/units/{id}）再打开弹窗。
      try {
        const detail = await getUnit(id);
        openPermissionDialog({
          unitId: id,
          unitTitle: (detail && detail.title) || (unit && unit.title),
          assigned: (detail && detail.permissions) || [],
          onSaved: load,
        });
      } catch (error) {
        toast(error.message || '读取知识单元权限失败', 'error');
      }
      return;
    }

    if (btn.dataset.act === 'del') {
      const ok = await confirmDialog(
        `确认删除知识单元「${(unit && unit.title) || id}」？将同步删除其数据权限记录与向量切片。`,
        '删除',
      );
      if (!ok) return;
      try {
        await deleteUnits([Number(id)]);
        toast('删除成功', 'success');
        load();
      } catch (error) {
        toast(error.message || '删除失败', 'error');
      }
    }
  });

  // 第 4 步：分页
  pagerBox.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-page]');
    if (!btn || btn.disabled) return;
    state.page += btn.dataset.page === 'next' ? 1 : -1;
    if (state.page < 1) state.page = 1;
    load();
  });

  // 第 5 步：筛选区动作
  $('[data-role="search"]', container).addEventListener('click', () => {
    state.page = 1;
    load();
  });
  $('[data-role="reset"]', container).addEventListener('click', () => {
    $('[data-role="title"]', container).value = '';
    $('[data-role="category"]', container).value = '';
    $('[data-role="status"]', container).value = '';
    state.page = 1;
    load();
  });
  $('[data-role="to-import"]', container).addEventListener('click', () => navigate('/knowledge/import'));
  // 回车即查询，省一次鼠标移动
  container.querySelectorAll('.input').forEach((input) => {
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        state.page = 1;
        load();
      }
    });
  });

  // 第 6 步：批量删除
  batchBtn.addEventListener('click', async () => {
    if (!state.selected.size) return;
    const ok = await confirmDialog(`确认删除选中的 ${state.selected.size} 个知识单元？`, '批量删除');
    if (!ok) return;
    try {
      await deleteUnits([...state.selected].map(Number));
      toast('批量删除成功', 'success');
      load();
    } catch (error) {
      toast(error.message || '批量删除失败', 'error');
    }
  });

  // 第 7 步：首次加载
  load();
  return container;
}
