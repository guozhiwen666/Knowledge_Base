# 知识库管理平台 · 前端（纯静态 SPA）

原生 HTML / CSS / ES Module，**无构建步骤、无 npm 依赖**。双击 `index.html`
或用任意静态服务器托管即可运行。

---

## 1. 启动方式

### 方式一：Python 静态服务器（推荐）

```bash
cd frontend
python -m http.server 8080
```

然后浏览器打开 <http://127.0.0.1:8080/index.html>。

> 用 `file://` 直接双击 `index.html` 也能打开，但 ES Module 在部分浏览器下
> 受同源策略限制会加载失败，因此推荐用上面的静态服务器方式。

### 方式二：由后端 FastAPI 的 StaticFiles 挂载

把 `frontend/` 目录挂到后端静态目录即可，无需任何改动。

### 后端地址配置

后端基址集中在一处，改一行即可全站生效：

```js
// js/api/client.js
export const BASE_URL = 'http://127.0.0.1:8000';
```

### 后端需要允许跨域（CORS）

前端与后端不同端口，后端需允许来源 `http://127.0.0.1:8080`，
并放通 `Authorization` 与 `Content-Type` 两个请求头。

### SSE 与反向代理

`POST /api/ai/chat/stream` 是流式响应。若前面挂了 Nginx，
必须关闭缓冲，否则流式效果会被吞掉：

```nginx
proxy_buffering off;
proxy_cache off;
```

---

## 2. 图表库 ECharts 的引入说明

ECharts **已随交付放在本地** `vendor/echarts.min.js`（v5，约 1.0 MB），
由 `index.html` 用普通 `<script>` 标签引入，不引入任何 npm 依赖。

如果该文件丢失或需要换成 CDN，把 `index.html` 里这一行替换为：

```html
<!-- 本地引入（当前用法） -->
<script src="vendor/echarts.min.js"></script>

<!-- 改为 CDN 引入 -->
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
```

换成 CDN 后，图表页若加载不到库，页面会显示
「图表库未加载：请确认 vendor/echarts.min.js 存在，或按本 README 的说明改用 CDN 引入」，
而不是留一块空白画布。

---

## 3. 目录结构

```
frontend/
├─ index.html                      SPA 入口，仅挂载 #app 与两个宿主容器
├─ README.md
├─ css/
│  ├─ base.css                     设计变量、reset、应用骨架布局
│  ├─ components.css               通用组件：按钮/表单/表格/标签/占位态/弹窗/Toast/Tab/分页
│  └─ features.css                 业务样式：指标卡片/图表/上传/树/权限分组/对话/Markdown
├─ vendor/
│  └─ echarts.min.js               图表库（本地引入，无 npm 依赖）
└─ js/
   ├─ app.js                       入口：注册路由、装配外壳、启动
   ├─ api/                         接口层
   │  ├─ client.js                 BASE_URL、请求拦截器、401 处理、带进度的上传
   │  ├─ auth.js                   POST /api/auth/login
   │  ├─ org.js                    组织接口（部门树/写、用户列表/详情/重置口令/删除、角色 CRUD、角色权限）
   │  ├─ knowledge.js              知识接口（导入 / 进度轮询 / 列表 / 新建 / 详情 / 更新 / 状态 / 权限 / 删除 / 鉴权）
   │  ├─ ai.js                     AI 接口（SSE 流式问答 / 历史会话列表 / 会话明细）
   │  ├─ dashboard.js              看板接口（指标 / 双榜 / 趋势）
   │  └─ settlement.js             沉淀接口（推荐 / 审核 / FAQ 库 / 下线 / 缺口列表 / 补全 / 忽略）
   ├─ core/                        基础设施
   │  ├─ store.js                  sessionStorage 会话、权限判断
   │  ├─ router.js                 hash 路由 + 登录态与菜单权限守卫
   │  ├─ shell.js                  顶栏 + 侧边栏 + 动态菜单
   │  ├─ sse.js                    fetch + ReadableStream 手动解析 SSE
   │  ├─ markdown.js               自写的极简 Markdown → HTML（先转义再替换）
   │  ├─ pager.js                  通用分页条模板（知识单元 / 用户 / FAQ 三处共用）
   │  └─ dom.js                    el / esc / toast / modal / 占位态 / 格式化
   └─ modules/                     业务模块（对应 2.9.5 七个模块）
      ├─ auth-module.js            认证与权限控制 + 个人中心
      ├─ org-module.js             组织架构：Tab 容器 + 角色管理
      ├─ org-role-form.js          角色表单弹窗（新增 / 编辑）
      ├─ org-user-management.js    用户管理（列表 / 筛选 / 分页 / 行内操作）
      ├─ org-user-table.js         用户表格模板
      ├─ org-user-form.js          用户表单模板与取值
      ├─ org-user-reset.js         重置口令弹窗（含明文口令展示）
      ├─ org-department.js         部门树 + 成员列表 + 节点操作
      ├─ org-department-form.js    部门表单弹窗
      ├─ org-role-permissions.js   角色权限树组件
      ├─ org-options.js            部门 / 角色 / 用户下拉选项（带缓存与树拍平）
      ├─ knowledge-module.js       知识模块路由入口
      ├─ knowledge-import.js       导入中心（拖拽 + 并发上传 + 两段进度）
      ├─ import-queue.js           导入队列模型与表格渲染 + 上传区交互
      ├─ import-progress.js        解析进度轮询器（定时器与终态判定）
      ├─ knowledge-unit-list.js    知识单元列表
      ├─ knowledge-unit-form.js    手工新建知识单元弹窗
      ├─ unit-list-table.js        列表表格模板
      ├─ knowledge-detail.js       知识单元详情与编辑
      ├─ permission-dialog.js      数据权限配置弹窗（四组实体同一弹窗）
      ├─ permission-selects.js     四组实体的多选组件（部门树 / 角色 / 人员）
      ├─ chat-module.js            AI 对话工作台（状态与交互）
      ├─ chat-render.js            对话渲染（回答 / 引用卡片 / 权限提示 / 历史回放）
      ├─ chat-stream.js            SSE 七事件 → 轮次状态映射
      ├─ chat-send.js              一次提问的生命周期与流的中断管理
      ├─ chat-history.js           左侧会话栏取数（本地会话 + 服务端历史会话）
      ├─ dashboard-module.js       数据看板
      ├─ dashboard-charts.js       ECharts 封装
      ├─ settlement-module.js      知识沉淀管理（页面外壳 / 推荐审核 / 缺口）
      ├─ settlement-faq-library.js FAQ 库（分页 / 状态筛选 / 下线）
      ├─ settlement-gap-actions.js 知识缺口补全与忽略
      └─ settlement-review.js      FAQ 审核弹窗
```

单文件均不超过 300 行。

---

## 4. 路由表（hash 路由，对应文档 9.3）

| 路由 | 页面 | 所需权限码 |
| --- | --- | --- |
| `#/login` | 登录与个人中心 | 免鉴权（已登录时渲染个人中心） |
| `#/org/users`、`#/org/roles`、`#/org/departments` | 组织架构与权限管理 | `menu:org` |
| `#/knowledge/import`、`#/knowledge/units`、`#/knowledge/units/:id` | 知识维护与导入 | `menu:knowledge` |
| `#/ai/chat` | AI 对话鉴权工作台 | `ai:chat:access` + 登录态 |
| `#/dashboard` | 数据看板 | `menu:dashboard` |
| `#/settlement` | 知识沉淀管理 | `menu:settlement` |

守卫逻辑：未登录一律重定向 `#/login`；已登录但无对应菜单权限码时提示并回退到
当前账号有权限的第一个页面。

---

## 5. 关键实现要点

| 项 | 实现 |
| --- | --- |
| 会话存储 | Token / user_info / permissions 存 `sessionStorage`，关闭标签页即失效 |
| 请求拦截 | `js/api/client.js` 统一附加 `Authorization: Bearer <token>`；收到 401 清会话并跳登录页（403 不跳登录） |
| 响应解包 | 统一拆 `{code, message, data}`；`code !== 0` 抛 `ApiError`，页面按需分支 |
| 空串查询参数 | 默认丢弃空串（「不筛选」=「不传参」）；个别接口把空串当有效取值（`GET /api/settlement/faqs` 的 `status=` 表示不限状态），由调用方用 `keepEmpty` 显式声明 |
| SSE | `EventSource` 不支持 POST，故用 `fetch` + `ReadableStream` 按 `event:` / `data:` 逐行解析，处理 4.8 的七种事件 |
| 流式渲染 | 自写极简 Markdown（标题 / 粗体 / 列表 / 代码块 / 行内代码 / 换行），**先整体 HTML 转义再做标记替换**，防 XSS |
| 两段导入进度 | 上传进度来自 XHR 的真实字节进度（并发度 3 的分批 `Promise.allSettled`）；解析进度由 `import-progress.js` 每 1.8 秒轮询 `GET /api/knowledge/import/progress`，`all_finished` 为真或达到次数上限即停止，路由切走时清理定时器 |
| 历史对话 | 左侧列出 `GET /api/ai/conversations`（最近提问摘要 / 轮数 / 时间），点击后拉 `GET /api/ai/conversations/{session_id}` 并复用对话渲染函数回放（`cited_units` 映射成引用来源卡片）；后端对非本人会话返回 404，页面原样提示 |
| 明文口令 | 重置口令走独立接口，响应里的明文口令只在弹窗里展示一次（库里只存哈希），提示管理员立即转达 |
| 权限弹窗 | 全局（开关）/ 部门（多选树）/ 角色（多选）/ 人员（多选）四组同一弹窗，提交结构直接对应 `POST /api/knowledge/units/{id}/permissions` 的 `permissions` 数组。人员选项来自 `GET /api/org/users`（要求 `menu:org`）；取不到时退化为只读展示已配置的人员实体，保存按原值保留，避免全量覆盖把看不见的授权清空 |
| 图表 | ECharts 双 Y 轴折线（Token + 响应时间），支持日 / 周切换；**数据为空时显示空态而非空白画布** |
| 空态与防白屏 | 首屏有 boot 引导屏；页面外壳同步返回、内容区先挂 loading，保证 100ms 内有骨架 |
| 按钮级权限 | 按登录返回的 `permissions` 控制增删改按钮显隐；无权限时用 `disabled` + `title` 说明所需权限码 |
| 后端报错如实展示 | 422 等业务拒绝（部门有子部门 / 角色仍被使用 / 编码重复 / 格式不支持）一律显示后端 `message`，不替换成通用文案 |
| 资源清理 | 路由切走时中断进行中的 SSE 连接与解析进度轮询、`dispose()` 全部 ECharts 实例 |

---

## 6. 使用的接口范围

前端调用后端在文档 **8 章列出的 21 个接口**，加上第 14 章确认后**补齐的 20 个接口**
（共 41 个），不新增、不改名、不猜端点。完整映射见 `js/api/*.js` 的文件头注释。

补齐的 20 个接口：

| 模块 | 接口 | 页面落位 |
| --- | --- | --- |
| 知识 | `POST /api/knowledge/units` | 知识单元列表 · 手工新建 |
| 知识 | `PUT /api/knowledge/units/{id}/status` | 知识单元列表 · 置为 active |
| 知识 | `GET /api/knowledge/import/progress` | 导入中心 · 解析进度轮询 |
| 组织 | `GET/DELETE /api/org/users`、`GET /api/org/users/{id}` | 用户管理 · 列表 / 删除 |
| 组织 | `POST /api/org/users/{id}/reset-password` | 用户管理 · 重置口令 |
| 组织 | `POST/PUT/DELETE /api/org/departments`、`GET /api/org/departments/{id}/members` | 部门管理 · 增删改与成员列表 |
| 组织 | `POST/PUT/DELETE /api/org/roles` | 角色管理 · 增删改 |
| AI | `GET /api/ai/conversations`、`GET /api/ai/conversations/{session_id}` | AI 工作台 · 历史对话与回放 |
| 沉淀 | `GET /api/settlement/faqs`、`POST /api/settlement/faqs/{id}/offline` | 沉淀页 · FAQ 库与下线 |
| 沉淀 | `POST /api/settlement/knowledge-gaps/{id}/resolve`、`.../ignore` | 沉淀页 · 一键建档 / 忽略 |

`POST /api/knowledge/import` 的响应结构随之变化：`accepted` 只给 `task_id`
（解析改为后台异步），入库结果（`unit_code` / `chunk_count`）从进度接口取。

`js/core/dom.js` 的 `pendingBlock()` 函数与 `.pending-block` 样式保留：样式仍在用
（权限不足、审核可编辑性这类**非缺接口**的说明块），函数本身目前没有调用方，
留作后续新增页面时的占位设施。此前因「接口未确认」而做的占位块已全部替换为真实实现。

---

## 7. 浏览器兼容

需要支持 ES Module、`fetch`、`ReadableStream`、`AbortController` 的现代浏览器
（Chrome / Edge 90+、Firefox 90+、Safari 15+）。
