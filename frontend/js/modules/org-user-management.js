/**
 * 组织架构管理模块 · 用户管理 Tab
 * （2.9.3「用户管理：列表/新增/编辑/重置密码/启停用」）
 *
 * 接口落位（严格限定在 8 章列出的接口内）：
 *   - 新增用户      -> `POST /api/org/users`
 *   - 编辑用户      -> `PUT /api/org/users/{id}`
 *   - 启停用        -> `PUT /api/org/users/{id}` 只提交 { status }（8.3 明确由该接口承载）
 *   - 重置密码      -> `PUT /api/org/users/{id}` 只提交 { password }（8.3 明确「重置密码走 PUT 的密码重置语义」）
 *   - 部门/角色下拉 -> `GET /api/org/departments`、`GET /api/org/roles`
 *
 * **明确缺失、页面用占位说明的部分**：
 *   用户列表查询接口（`GET /api/org/users`）不在 8 章，因此本页无法渲染用户表格，
 *   也拿不到用户主键。为在不自造端点的前提下仍然可用，编辑类操作改为
 *   「手工输入用户 ID」的方式承载，并在界面上如实标注这一限制。
 *
 * 表单模板与取值逻辑拆在 org-user-form.js。
 */

import { createUser, updateUser, listDepartments, listRoles } from '../api/org.js';
import { el, esc, toast, pendingBlock, openModal } from '../core/dom.js';
import { userFormHtml, readUserForm } from './org-user-form.js';

/** 缓存部门与角色选项，避免每次打开弹窗都重复拉取 */
let optionCache = null;

/** 拉取部门（拍平带缩进）与角色选项 */
export async function loadOptions(force = false) {
  if (optionCache && !force) return optionCache;

  // 第 1 步：两个接口并行拉取，任一个失败则该项降级为空列表（弹窗仍可用）
  const [departments, roles] = await Promise.all([
    listDepartments().catch(() => []),
    listRoles().catch(() => []),
  ]);

  // 第 2 步：把部门树拍平成带层级缩进的选项列表（原生 select 无法表达树，用全角空格缩进）
  const flatDepartments = [];
  const walk = (nodes, depth) => {
    (nodes || []).forEach((node) => {
      flatDepartments.push({ id: node.id, name: `${'　'.repeat(depth)}${node.name}` });
      walk(node.children, depth + 1);
    });
  };
  walk(departments, 0);

  optionCache = { departments: flatDepartments, roles: roles || [] };
  return optionCache;
}

/** 供外部在角色 / 部门变更后刷新选项缓存 */
export function invalidateOptionCache() {
  optionCache = null;
}

/** 弹窗内的错误提示 */
function showFormError(box, message) {
  box.textContent = message;
  box.classList.remove('hidden');
}

/** 打开用户操作弹窗 */
async function openUserModal(mode) {
  const titles = {
    create: '新增用户',
    edit: '编辑用户',
    password: '重置密码',
    status: '启停用用户',
  };

  // 第 1 步：拉选项。失败不阻塞弹窗，降级为空选项
  let options = { departments: [], roles: [] };
  try {
    options = await loadOptions();
  } catch {
    optionCache = null;
  }

  openModal({
    title: titles[mode],
    bodyHtml: `<div class="form-error hidden" data-role="error"></div>${userFormHtml(mode, options)}`,
    okText: '保存',
    onOk: async (body) => {
      const errorBox = body.querySelector('[data-role="error"]');
      errorBox.classList.add('hidden');
      const payload = readUserForm(body, mode);

      // 第 2 步：只做「必填项」这一层前端校验，其余交给后端裁定
      if (mode !== 'create' && !payload._userId) {
        showFormError(errorBox, '请填写目标用户 ID');
        return false;
      }
      if (mode === 'create' && (!payload.username || !payload.password || !payload.display_name)) {
        showFormError(errorBox, '登录名、初始密码、显示名均为必填');
        return false;
      }

      try {
        // 第 3 步：按 mode 分派到 8.3 的两个接口
        if (mode === 'create') {
          await createUser(payload);
        } else {
          // _userId 只用于拼路径，不能进请求体
          const userId = payload._userId;
          delete payload._userId;
          await updateUser(userId, payload);
        }
        toast('保存成功', 'success');
        return true;
      } catch (error) {
        showFormError(errorBox, error.message || '保存失败');
        return false;
      }
    },
  });
}

/**
 * 渲染用户管理 Tab 的内容。
 * @returns {HTMLElement}
 */
export function renderUserTab() {
  const container = el(`
    <div>
      <div class="row-between mb16">
        <div class="row">
          <button class="btn btn-primary" type="button" data-act="create">新增用户</button>
          <button class="btn" type="button" data-act="edit">编辑用户</button>
          <button class="btn" type="button" data-act="password">重置密码</button>
          <button class="btn" type="button" data-act="status">启停用</button>
        </div>
        <span class="mute-sm">四个动作分别落到 8.3 的 POST / PUT 接口</span>
      </div>

      <div class="card">
        <div class="card-title">用户列表</div>
        ${pendingBlock(
          '用户列表',
          '2.9.3 要求用户管理支持列表展示，但文档 8 章未列出用户列表查询接口，因此无法渲染用户表格与分页，也无法取得用户主键。上方的编辑 / 重置密码 / 启停用改为手工输入用户 ID 的方式承载。',
          'GET /api/org/users（8 章未列出）',
        )}
      </div>

      <div class="card">
        <div class="card-title">选项数据来源</div>
        <table class="data">
          <thead><tr><th>选项</th><th>数据来源接口</th><th>状态</th></tr></thead>
          <tbody>
            <tr>
              <td>所属部门</td>
              <td class="mono">GET /api/org/departments</td>
              <td><span class="tag tag-success">8 章已列出</span></td>
            </tr>
            <tr>
              <td>角色（多选）</td>
              <td class="mono">GET /api/org/roles</td>
              <td><span class="tag tag-success">8 章已列出</span></td>
            </tr>
            <tr>
              <td>用户列表 / 部门成员</td>
              <td class="mono">—</td>
              <td><span class="tag tag-warn">待接口确认</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `);

  // 事件委托：四个按钮共用一套处理
  container.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-act]');
    if (!btn) return;
    openUserModal(btn.dataset.act);
  });

  return container;
}
