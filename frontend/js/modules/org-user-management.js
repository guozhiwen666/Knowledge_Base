/**
 * 组织架构管理模块 · 用户管理 Tab
 * （2.9.3「用户管理：列表/新增/编辑/重置密码/启停用」）
 *
 * 接口落位：
 *   `GET    /api/org/users`                      列表（分页 + 关键词/部门/状态筛选）
 *   `POST   /api/org/users`                      新增
 *   `PUT    /api/org/users/{id}`                 编辑与启停用（后端按「未传即不改」处理）
 *   `DELETE /api/org/users/{id}`                 删除
 *   `POST   /api/org/users/{id}/reset-password`  重置口令（明文只返回一次，必须展示）
 *   下拉选项（部门 / 角色 / 用户）走 org-options.js，与部门表单共用同一份缓存。
 *
 * 关键约定：
 *   1. 目标用户一律来自列表行的真实主键，界面上没有「手输用户 ID」这一步；
 *   2. 重置口令成功后必须把返回的明文口令展示给管理员 —— 库里只有哈希，错过就没了；
 *   3. 后端返回的 message（如登录名重复的 422）原样显示，不替换成通用文案。
 *
 * 表格模板在 org-user-table.js，表单模板在 org-user-form.js。
 */

import { listUsers, createUser, updateUser, deleteUser } from '../api/org.js';
import { hasPermission } from '../core/store.js';
import {
  el,
  esc,
  $,
  toast,
  confirmDialog,
  openModal,
  emptyState,
  loadingState,
  fmtNumber,
} from '../core/dom.js';
import { renderPagerHtml } from '../core/pager.js';
import { loadOptions } from './org-options.js';
import { renderUserTableHtml } from './org-user-table.js';
import { userFormHtml, readUserForm } from './org-user-form.js';
import { openResetPasswordModal } from './org-user-reset.js';

/** 弹窗内的错误提示 */
function showFormError(box, message) {
  box.textContent = message;
  box.classList.remove('hidden');
}

/**
 * 打开用户新增 / 编辑弹窗。
 * @param {'create'|'edit'} mode
 * @param {object} [user] 编辑时的目标用户（来自列表行）
 * @param {Function} onDone 保存成功回调
 */
async function openUserFormModal(mode, user, onDone) {
  // 第 1 步：拉选项。失败不阻塞弹窗，降级为空选项
  let options = { departments: [], roles: [] };
  try {
    options = await loadOptions();
  } catch {
    options = { departments: [], roles: [] };
  }

  openModal({
    title: mode === 'create' ? '新增用户' : `编辑用户 · ${user.display_name || user.username}`,
    size: mode === 'edit' ? 'lg' : '',
    bodyHtml: `<div class="form-error hidden" data-role="error"></div>${userFormHtml(mode, options, user)}`,
    okText: '保存',
    onOk: async (body) => {
      const errorBox = $('[data-role="error"]', body);
      errorBox.classList.add('hidden');
      const payload = readUserForm(body, mode);

      // 第 2 步：只做「必填项」这一层前端校验，其余交给后端裁定
      if (mode === 'create' && (!payload.username || !payload.password || !payload.display_name)) {
        showFormError(errorBox, '登录名、初始密码、显示名均为必填');
        return false;
      }
      if (mode === 'edit' && !payload.display_name) {
        showFormError(errorBox, '显示名不能为空');
        return false;
      }

      try {
        // 第 3 步：按 mode 分派接口。目标 id 来自列表行，不用用户输入
        if (mode === 'create') {
          await createUser(payload);
        } else {
          await updateUser(user.id, payload);
        }
        toast('保存成功', 'success');
        if (onDone) onDone();
        return true;
      } catch (error) {
        // 422（登录名重复等）原样展示后端 message
        showFormError(errorBox, error.message || '保存失败');
        return false;
      }
    },
  });
}

/**
 * 渲染用户管理 Tab 的内容（同步返回骨架，列表数据异步填充）。
 * @returns {HTMLElement}
 */
export function renderUserTab() {
  const canWrite = hasPermission('menu:org');

  const container = el(`
    <div>
      <div class="card">
        <div class="row-between">
          <div class="row">
            <input class="input" data-role="keyword" type="text" placeholder="登录名或显示名" style="width:180px" />
            <select class="select" data-role="department" style="width:190px">
              <option value="">全部部门</option>
            </select>
            <select class="select" data-role="status" style="width:120px">
              <option value="">全部状态</option>
              <option value="1">启用</option>
              <option value="0">停用</option>
            </select>
            <button class="btn btn-primary" type="button" data-role="search">查询</button>
            <button class="btn" type="button" data-role="reset">重置</button>
          </div>
          <button class="btn btn-primary" type="button" data-role="create"
                  ${canWrite ? '' : 'disabled title="需要 menu:org 权限"'}>新增用户</button>
        </div>
        <div class="field-hint mt8">
          数据来源 <code>GET /api/org/users</code>：关键词同时匹配登录名与显示名，
          部门与状态为精确匹配；分页参数 page / page_size。
        </div>
      </div>

      <div class="card">
        <div class="card-title">
          <span>用户列表</span>
          <span class="mute-sm" data-role="total"></span>
        </div>
        <div data-role="table">${loadingState()}</div>
        <div class="pager" data-role="pager"></div>
      </div>
    </div>
  `);

  const tableBox = $('[data-role="table"]', container);
  const pagerBox = $('[data-role="pager"]', container);
  const totalBox = $('[data-role="total"]', container);
  const deptBox = $('[data-role="department"]', container);

  const state = { page: 1, page_size: 20, total: 0, items: [] };

  /** 拉取列表 */
  const load = async () => {
    tableBox.innerHTML = loadingState();
    const query = {
      keyword: $('[data-role="keyword"]', container).value.trim(),
      department_id: deptBox.value === '' ? '' : Number(deptBox.value),
      status: $('[data-role="status"]', container).value === '' ? '' : Number($('[data-role="status"]', container).value),
      page: state.page,
      page_size: state.page_size,
    };
    try {
      const data = await listUsers(query);
      state.total = (data && data.total) || 0;
      state.items = (data && data.items) || [];
    } catch (error) {
      // 403（无 menu:org）与网络错误都在这里如实展示
      tableBox.innerHTML = `<div class="empty-state">加载失败：${esc(error.message)}</div>`;
      pagerBox.innerHTML = '';
      totalBox.textContent = '';
      return;
    }
    totalBox.textContent = `共 ${fmtNumber(state.total)} 个用户`;
    tableBox.innerHTML = state.items.length
      ? renderUserTableHtml(state.items, canWrite)
      : emptyState('没有符合条件的用户');
    pagerBox.innerHTML = renderPagerHtml(state.page, state.page_size, state.total, '人');
  };

  // 第 1 步：部门筛选下拉（与部门表单共用同一份选项缓存）
  loadOptions()
    .then((options) => {
      deptBox.innerHTML =
        '<option value="">全部部门</option>' +
        options.departments.map((dept) => `<option value="${esc(dept.id)}">${esc(dept.name)}</option>`).join('');
    })
    .catch(() => {
      deptBox.innerHTML = '<option value="">全部部门（选项加载失败）</option>';
    });

  // 第 2 步：行内操作（事件委托）
  tableBox.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-act]');
    if (!btn || btn.disabled) return;
    const user = state.items.find((item) => String(item.id) === String(btn.dataset.id));
    if (!user) return;
    const label = user.display_name || user.username;

    if (btn.dataset.act === 'edit') {
      openUserFormModal('edit', user, load);
      return;
    }

    if (btn.dataset.act === 'reset') {
      openResetPasswordModal(user);
      return;
    }

    if (btn.dataset.act === 'toggle') {
      const next = Number(user.status) === 1 ? 0 : 1;
      const action = next === 1 ? '启用' : '停用';
      const ok = await confirmDialog(`确认${action}用户「${label}」？${next === 0 ? '停用后该账号无法登录。' : ''}`, action);
      if (!ok) return;
      try {
        // 只提交 status：后端「未传即不改」，不会顺手改动角色与部门
        await updateUser(user.id, { status: next });
        toast(`已${action}「${label}」`, 'success');
        load();
      } catch (error) {
        toast(error.message || `${action}失败`, 'error');
      }
      return;
    }

    if (btn.dataset.act === 'del') {
      const ok = await confirmDialog(
        `确认删除用户「${label}」？其角色关联会一并清除，指向该用户的单元授权保留。`,
        '删除',
      );
      if (!ok) return;
      try {
        await deleteUser(user.id);
        toast('删除成功', 'success');
        // 删掉当前页最后一条时回退一页，避免停在空页上
        if (state.items.length === 1 && state.page > 1) state.page -= 1;
        load();
      } catch (error) {
        toast(error.message || '删除失败', 'error');
      }
    }
  });

  // 第 3 步：分页
  pagerBox.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-page]');
    if (!btn || btn.disabled) return;
    state.page += btn.dataset.page === 'next' ? 1 : -1;
    if (state.page < 1) state.page = 1;
    load();
  });

  // 第 4 步：筛选动作
  $('[data-role="search"]', container).addEventListener('click', () => {
    state.page = 1;
    load();
  });
  $('[data-role="reset"]', container).addEventListener('click', () => {
    $('[data-role="keyword"]', container).value = '';
    deptBox.value = '';
    $('[data-role="status"]', container).value = '';
    state.page = 1;
    load();
  });
  // 回车即查询，省一次鼠标移动
  $('[data-role="keyword"]', container).addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      state.page = 1;
      load();
    }
  });

  // 第 5 步：新增用户
  $('[data-role="create"]', container).addEventListener('click', () => {
    if (!canWrite) return;
    openUserFormModal('create', null, () => {
      state.page = 1;
      load();
    });
  });

  // 第 6 步：首次加载
  load();
  return container;
}
