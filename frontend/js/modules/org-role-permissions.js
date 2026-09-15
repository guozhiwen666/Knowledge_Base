/**
 * 角色权限树组件（9.1「组织架构管理模块」的「角色权限树」部分）
 *
 * 职责：
 *   1. 提供权限码目录（三类：菜单 / 操作 / AI 访问，对应 6.1 的 permission_type）；
 *   2. 渲染勾选树，把角色已有的权限码回填为选中态；
 *   3. 输出可直接提交给 `POST /api/org/roles/{id}/permissions` 的
 *      `[{permission_code, permission_type}]` 结构（8.3）。
 *
 * 目录口径说明：6.1 的权限码表在文档中标注为「仅示例，不作为强制约定」，
 * 因此这里把目录当成「默认展示集」而非「合法值白名单」——
 * 后端返回的目录外权限码会以「其他（后端已配置）」分组补进树里，
 * 避免出现「界面上看不见但实际有」的权限，也避免保存时被静默丢掉。
 */

import { esc } from '../core/dom.js';

/** 权限码目录：与 6.1 的示例表一致，按 permission_type 分三组 */
export const PERMISSION_CATALOG = [
  {
    type: 'menu',
    label: '菜单访问权限',
    hint: '控制左侧菜单与页面路由的可见性（9.3 路由守卫）',
    codes: [
      { code: 'menu:org', label: '组织架构与权限管理页' },
      { code: 'menu:knowledge', label: '知识维护与导入页' },
      { code: 'menu:dashboard', label: '数据看板页' },
      { code: 'menu:settlement', label: '知识沉淀管理页' },
    ],
  },
  {
    type: 'operation',
    label: '知识单元操作权限',
    hint: '控制知识单元增删改查按钮的显隐',
    codes: [
      { code: 'knowledge:unit:create', label: '知识单元新增（含导入）' },
      { code: 'knowledge:unit:read', label: '知识单元查询' },
      { code: 'knowledge:unit:update', label: '知识单元编辑' },
      { code: 'knowledge:unit:delete', label: '知识单元删除' },
    ],
  },
  {
    type: 'ai',
    label: 'AI 问答访问权限',
    hint: '控制 AI 对话工作台的访问与提问',
    codes: [{ code: 'ai:chat:access', label: 'AI 问答访问' }],
  },
];

/** 目录内全部权限码（用于计算「目录外」的剩余码） */
const CATALOG_CODES = new Set(
  PERMISSION_CATALOG.flatMap((group) => group.codes.map((item) => item.code)),
);

/**
 * 渲染权限树 HTML。
 * @param {Array<{permission_code:string,permission_type:string}>} assigned 角色已分配的权限
 * @returns {string} HTML 片段；勾选框统一用 `name="perm"` + `data-type` 承载类型
 */
export function renderPermissionTree(assigned = []) {
  // 第 1 步：把已分配权限整理成 Set，回填勾选态
  const assignedCodes = new Set((assigned || []).map((item) => item.permission_code).filter(Boolean));

  // 第 2 步：目录内分组渲染
  const groups = PERMISSION_CATALOG.map((group) => {
    const items = group.codes
      .map(
        (item) => `
        <label class="check-item">
          <input type="checkbox" name="perm" value="${esc(item.code)}"
                 data-type="${esc(group.type)}" data-label="${esc(item.label)}"
                 ${assignedCodes.has(item.code) ? 'checked' : ''} />
          <span>${esc(item.label)}<br /><span class="mute-sm mono">${esc(item.code)}</span></span>
        </label>`,
      )
      .join('');
    const allChecked = group.codes.every((item) => assignedCodes.has(item.code));
    return `
      <div class="perm-group">
        <div class="perm-group-head">
          <span>${esc(group.label)} <span class="tag">${esc(group.type)}</span></span>
          <label class="check-item">
            <input type="checkbox" data-role="group-all" data-type="${esc(group.type)}" ${allChecked ? 'checked' : ''} />
            <span class="mute-sm">全选</span>
          </label>
        </div>
        <div class="perm-group-body">
          <div class="field-hint mb8">${esc(group.hint)}</div>
          <div class="check-grid">${items}</div>
        </div>
      </div>
    `;
  });

  // 第 3 步：目录外权限码单独成组，保证「看得见才改得对」
  const extras = [...assignedCodes].filter((code) => !CATALOG_CODES.has(code));
  if (extras.length) {
    const items = extras
      .map(
        (code) => `
        <label class="check-item">
          <input type="checkbox" name="perm" value="${esc(code)}" data-type="other" checked />
          <span class="mono">${esc(code)}</span>
        </label>`,
      )
      .join('');
    groups.push(`
      <div class="perm-group">
        <div class="perm-group-head">
          <span>其他（后端已配置）</span>
          <span class="mute-sm">目录外权限码</span>
        </div>
        <div class="perm-group-body">
          <div class="field-hint mb8">这些权限码不在 6.1 的示例目录中，但该角色已持有；取消勾选即会被移除。</div>
          <div class="check-grid">${items}</div>
        </div>
      </div>
    `);
  }

  return groups.join('');
}

/**
 * 绑定权限树的交互（分组全选）。
 * @param {HTMLElement} root 权限树容器
 */
export function bindPermissionTree(root) {
  root.querySelectorAll('[data-role="group-all"]').forEach((groupBox) => {
    groupBox.addEventListener('change', () => {
      // 该分组的普通勾选框全部跟随「全选」的状态
      root.querySelectorAll(`input[name="perm"]:not([data-type="other"])`).forEach((box) => {
        if (box.dataset.type === groupBox.dataset.type) box.checked = groupBox.checked;
      });
    });
  });
}

/**
 * 从权限树容器收集勾选结果。
 * @param {HTMLElement} root
 * @returns {Array<{permission_code:string, permission_type:string}>} 直接对应 8.3 的请求体
 */
export function collectPermissions(root) {
  const result = [];
  const seen = new Set();
  root.querySelectorAll('input[name="perm"]:checked').forEach((box) => {
    const code = box.value;
    if (!code || seen.has(code)) return; // 表上有 uk_role_perm 唯一约束，前端先去重
    seen.add(code);
    result.push({ permission_code: code, permission_type: box.dataset.type || '' });
  });
  return result;
}
