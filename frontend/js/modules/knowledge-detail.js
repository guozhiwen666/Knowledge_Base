/**
 * 模块三之二：知识单元详情与编辑页（页面 2.9.3-3 的「编辑详情页」）
 *
 * 接口落位：
 *   `GET /api/knowledge/units/{id}` -> 8.4 详情（含 permissions 明细）
 *   `PUT /api/knowledge/units/{id}` -> 8.4 更新（title / content / category / summary）
 *   `POST /api/knowledge/units/{id}/permissions` -> 由 permission-dialog.js 承载
 *
 * 字段口径（与 backend/services/knowledge_unit_management_service/knowledge.py 对齐）：
 *   - 8.4 的 PUT 列了 `tags` 与 `attachments`，但 2.9.7 的 knowledge_units 表没有
 *     对应列，后端**刻意不接收**这两个字段。因此本页不渲染这两个输入框，
 *     避免出现「界面填了、接口答应了、实际存不下来」的假成功。
 *   - 正文变更后后端会自动重新切片并同步向量索引，页面只需给出提示。
 */

import { getUnit, updateUnit } from '../api/knowledge.js';
import { hasPermission, getUser } from '../core/store.js';
import { navigate } from '../core/router.js';
import {
  el,
  esc,
  $,
  toast,
  emptyState,
  fmtDateTime,
  fmtFileSize,
  fmtNumber,
} from '../core/dom.js';
import { openPermissionDialog, summarizePermissions } from './permission-dialog.js';

/** 四维实体的展示名（6.2） */
const TYPE_LABEL = { global: '全局', department: '部门', role: '角色', user: '人员' };

/**
 * 渲染知识单元详情 / 编辑页。
 * @param {string|number} unitId 路由参数
 */
export async function renderUnitDetail(unitId) {
  // 第 1 步：拉详情。失败（如 404）直接落错误卡片
  let unit;
  try {
    unit = await getUnit(unitId);
  } catch (error) {
    return el(`<div class="card"><div class="empty-state">加载失败：${esc(error.message)}</div></div>`);
  }
  if (!unit) {
    return el(`<div class="card">${emptyState('知识单元不存在')}</div>`);
  }

  const canUpdate = hasPermission('knowledge:unit:update');

  const container = el(`
    <div>
      <div class="row-between mb16">
        <div>
          <h2 class="page-title">${esc(unit.title || '未命名知识单元')}</h2>
          <p class="page-desc">
            单元编号 <span class="mono">${esc(unit.unit_code || '-')}</span>
            · 状态 ${esc(unit.status || '-')}
            · 更新于 ${esc(fmtDateTime(unit.updated_at))}
          </p>
        </div>
        <div class="row">
          <button class="btn" type="button" data-role="back">返回列表</button>
          <button class="btn" type="button" data-role="perm">配置数据权限</button>
          <button class="btn btn-primary" type="button" data-role="save" ${canUpdate ? '' : 'disabled'}>保存</button>
        </div>
      </div>

      ${
        canUpdate
          ? ''
          : '<div class="card"><div class="pending-block"><span class="pending-title">编辑受限</span>当前账号缺少 <code>knowledge:unit:update</code> 权限，保存按钮已禁用。</div></div>'
      }

      <div class="card">
        <div class="card-title">元信息</div>
        <table class="data">
          <tbody>
            <tr>
              <th style="width:130px">来源文件</th>
              <td>${esc(unit.source_file_name || '-')}</td>
              <th style="width:110px">文件类型</th>
              <td>${unit.file_type ? `<span class="tag">${esc(unit.file_type)}</span>` : '-'}</td>
            </tr>
            <tr>
              <th>文件大小</th>
              <td>${esc(fmtFileSize(unit.file_size))}</td>
              <th>创建人 ID</th>
              <td>${esc(unit.creator_id ?? '-')}</td>
            </tr>
            <tr>
              <th>创建时间</th>
              <td>${esc(fmtDateTime(unit.created_at))}</td>
              <th>当前登录人</th>
              <td>${esc((getUser() || {}).display_name || '-')}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-title">数据权限（当前已配置）</div>
        <div data-role="perms"></div>
      </div>

      <div class="card">
        <div class="card-title">内容编辑</div>
        <div class="form-error hidden" data-role="error"></div>
        <div class="field">
          <label>标题<span class="req">*</span></label>
          <input class="input" data-role="title" type="text" value="${esc(unit.title || '')}" />
        </div>
        <div class="field">
          <label>分类</label>
          <input class="input" data-role="category" type="text" value="${esc(unit.category || '')}" placeholder="可留空" />
        </div>
        <div class="field">
          <label>摘要</label>
          <textarea class="textarea" data-role="summary" placeholder="可留空；summary 列的生成方式需求未规定，由人工填写">${esc(unit.summary || '')}</textarea>
        </div>
        <div class="field">
          <label>正文</label>
          <textarea class="textarea textarea-lg" data-role="content" placeholder="知识单元正文">${esc(unit.content || '')}</textarea>
          <div class="field-hint">
            保存正文后，后端会重新切片并同步向量索引（8.4 的 PUT 副作用）。
            正文规模：<span data-role="len">${fmtNumber((unit.content || '').length)}</span> 字符。
          </div>
        </div>
      </div>
    </div>
  `);

  // 第 2 步：渲染已配置的数据权限明细（target_name 由后端补全，缺失时退回 target_id）
  renderPermList($('[data-role="perms"]', container), unit.permissions || []);

  // 第 3 步：正文长度实时提示
  const contentBox = $('[data-role="content"]', container);
  contentBox.addEventListener('input', () => {
    $('[data-role="len"]', container).textContent = fmtNumber(contentBox.value.length);
  });

  // 第 4 步：返回列表
  $('[data-role="back"]', container).addEventListener('click', () => navigate('/knowledge/units'));

  // 第 5 步：数据权限弹窗（回填真实明细，保存为全量覆盖）
  $('[data-role="perm"]', container).addEventListener('click', () => {
    openPermissionDialog({
      unitId: unit.id,
      unitTitle: unit.title,
      assigned: unit.permissions || [],
      onSaved: () => navigate(`/knowledge/units/${unit.id}`), // 重新拉一次，保证展示与库内一致
    });
  });

  // 第 6 步：保存（只提交后端真正接收的四个字段）
  $('[data-role="save"]', container).addEventListener('click', async () => {
    const errorBox = $('[data-role="error"]', container);
    errorBox.classList.add('hidden');

    const payload = {
      title: $('[data-role="title"]', container).value.trim(),
      category: $('[data-role="category"]', container).value.trim() || null,
      summary: $('[data-role="summary"]', container).value.trim() || null,
      content: contentBox.value,
    };

    // 第 7 步：前端只拦「标题非空」这一条（title 列非空，后端也会校验）
    if (!payload.title) {
      errorBox.textContent = '标题不能为空';
      errorBox.classList.remove('hidden');
      return;
    }

    const saveBtn = $('[data-role="save"]', container);
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';
    try {
      await updateUnit(unit.id, payload);
      toast('保存成功，正文已重新切片并同步向量索引', 'success');
      // 重新渲染以刷新 updated_at
      navigate(`/knowledge/units/${unit.id}`);
    } catch (error) {
      errorBox.textContent = error.message || '保存失败';
      errorBox.classList.remove('hidden');
    }
    saveBtn.disabled = false;
    saveBtn.textContent = '保存';
  });

  return container;
}

/** 渲染已配置的数据权限明细表 */
function renderPermList(host, permissions) {
  if (!permissions.length) {
    host.innerHTML = `
      <div class="pending-block">
        <span class="pending-title">未配置任何数据权限实体</span>
        按 6.2 的「默认无权限」规则，该知识单元当前除管理员外不可被检索到。
      </div>
    `;
    return;
  }

  host.innerHTML = `
    <div class="row mb16">
      <span class="mute-sm">权限摘要：</span>
      <span class="tag tag-primary">${esc(summarizePermissions(permissions))}</span>
    </div>
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr><th style="width:110px">实体类型</th><th style="width:110px">目标 ID</th><th>目标名称</th></tr>
        </thead>
        <tbody>
          ${permissions
            .map(
              (item) => `
            <tr>
              <td><span class="tag">${esc(TYPE_LABEL[item.target_type] || item.target_type || '-')}</span></td>
              <td class="mono">${esc(item.target_id)}</td>
              <td>${esc(item.target_name || (item.target_type === 'global' ? '全部登录用户' : '（后端未返回 target_name）'))}</td>
            </tr>`,
            )
            .join('')}
        </tbody>
      </table>
    </div>
  `;
}
