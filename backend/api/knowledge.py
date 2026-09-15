"""知识单元接口（8.4 + 第 14 章 #2 / #3 补齐）。

实现 8.4 列出的 7 个接口：

    POST   /api/knowledge/import                    单文件 / 多文件批量导入
    GET    /api/knowledge/units                     分页查询
    GET    /api/knowledge/units/{id}                详情 + 已配置的数据权限
    PUT    /api/knowledge/units/{id}                更新（正文变更会重建向量）
    POST   /api/knowledge/units/{id}/permissions    批量配置数据权限实体
    DELETE /api/knowledge/units                     批量删除
    POST   /api/knowledge/check-permissions         数据权限鉴权

并补齐 2.9.3 要求但 8.4 未列出的 3 个接口（第 14 章 #1 / #2 / #3 已确认为"补充新建接口"）：

    POST   /api/knowledge/units                     手工新建知识单元
    PUT    /api/knowledge/units/{id}/status         状态流转（#1：版本管理=仅状态流转）
    GET    /api/knowledge/import/progress           解析任务进度轮询

**导入为什么改成异步**：2.9.3 / 2.9.5 要的是"上传 → 解析中 → 完成"的进度轮询。
若导入仍在请求内同步跑完，响应发出时任务已经结束，进度永远是 100%，
轮询就成了一场表演。因此本接口只做**接收与扩展名校验**（很快），
把"解析 + 切片 + 向量化"丢到后台线程，前端再按 ``task_ids`` 轮询进度。
这也让响应结构回到 8.4 原文的样子（``accepted`` 里是 ``task_id`` 而不是入库结果）。
"""

from __future__ import annotations

import threading
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from sqlalchemy.orm import Session

from core.container import Container, get_container
from core.db import SessionLocal, get_session
from core.response import AppError, ok
from core.security import (
    CurrentUser,
    get_current_user,
    get_permission_engine,
    require_permission,
)
from models.enums import UnitStatus
from schemas.requests import (
    CheckPermissionsRequest,
    UnitCreateRequest,
    UnitDeleteRequest,
    UnitPermissionsRequest,
    UnitStatusRequest,
    UnitUpdateRequest,
)
from services.data_permission_engine.permission import DataPermissionEngine
from services.knowledge_unit_management_service.import_tasks import (
    TERMINAL_STATUSES,
    ImportTaskRunner,
    ImportTaskStore,
    QueuedFile,
)
from services.knowledge_unit_management_service.knowledge import (
    KnowledgeUnitService,
    UploadedFile as UploadedFileItem,
)
from services.knowledge_unit_management_service.knowledge_importer import (
    KnowledgeUnitImporter,
)
from services.knowledge_unit_management_service.knowledge_parsers import (
    detect_file_type,
)

__all__ = ["router"]

# 单文件大小上限（字节）：防止超大文件把请求线程内存打爆（P1-8 DoS 防护）
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_UPLOAD_SIZE_MB = MAX_UPLOAD_SIZE // (1024 * 1024)

router = APIRouter(prefix="/api/knowledge", tags=["知识单元"])

# 单次导入允许的最大文件数，防止一次请求打满内存
MAX_IMPORT_FILES = 50


def _storage_ready(container: Container) -> tuple[object, object]:
    """取出对象存储与向量存储；任一不可用就抛出可读的错误。

    MinIO / Milvus 是外部依赖，没起来时不该返回 500 让人猜，
    直接说明"哪个组件没就绪"。
    """
    storage = container.object_storage
    vectors = container.vector_store
    if storage is None or vectors is None:
        raise AppError(
            "知识导入依赖的 MinIO 或 Milvus 未就绪，请检查 .env 配置与服务状态",
            code=503,
            http_status=503,
        )
    return storage, vectors


def _service(
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> KnowledgeUnitService:
    """知识单元管理服务依赖（装配存储与向量组件）。"""
    storage, vectors = _storage_ready(container)
    return KnowledgeUnitService(session, storage, vectors)


# 后台导入执行器与进度存储的进程内单例。构造需要 MinIO / Milvus 就绪，
# 因此共用容器里的那份客户端，不每个请求重连一次
_singleton_lock = threading.Lock()
_runner: ImportTaskRunner | None = None
_store: ImportTaskStore | None = None


def _get_store(container: Container) -> ImportTaskStore:
    """取（或惰性构造）导入任务的进度存储。"""
    global _store
    if _store is None:
        with _singleton_lock:
            if _store is None:
                _store = ImportTaskStore(container.cache)
    return _store


def _get_runner(container: Container) -> ImportTaskRunner:
    """取（或惰性构造）后台导入执行器。

    与 :func:`_get_store` 共用同一个存储实例 —— 执行器写进度、接口读进度，
    必须是同一个对象（虽然缓存客户端本身就能跨实例共享，但共用一处更不容易写错）。
    """
    global _runner
    if _runner is None:
        with _singleton_lock:
            if _runner is None:
                storage, vectors = _storage_ready(container)
                _runner = ImportTaskRunner(
                    _get_store(container),
                    SessionLocal,
                    lambda session: KnowledgeUnitImporter(session, storage, vectors),
                )
    return _runner


# ---------------------------------------------------------------------- 导入


@router.post("/import", summary="单文件或多文件批量导入（异步解析）")
def import_documents(
    files: list[UploadFile] = File(..., description="PDF / Markdown / Word / TXT"),
    category: str | None = Form(default=None, description="统一分类，可选"),
    user: CurrentUser = Depends(require_permission("knowledge:unit:create")),
    container: Container = Depends(get_container),
) -> dict:
    """接收 multipart/form-data 上传，返回逐文件的解析任务标识。

    响应结构对齐 8.4：``accepted`` 给出已接收的文件与 ``task_id``，
    ``rejected`` 给出被拒原因（``unsupported_format``，在接收阶段按扩展名判定）。

    解析结果（入库的知识单元、切片数、失败原因）通过
    ``GET /api/knowledge/import/progress`` 轮询取得。
    """
    # 第 1 步：入参兜底，避免一次导入过多文件
    if not files:
        raise AppError("未收到任何文件", code=422, http_status=422)
    if len(files) > MAX_IMPORT_FILES:
        raise AppError(
            f"单次最多导入 {MAX_IMPORT_FILES} 个文件，当前 {len(files)} 个",
            code=422,
            http_status=422,
        )

    accepted: list[dict] = []
    rejected: list[dict] = []
    queued: list[QueuedFile] = []

    # 第 2 步：先只做「纯入参校验」——扩展名预筛 + 单文件大小预筛。
    # 这一步不碰任何外部依赖（MinIO / Milvus 都没动），因此即便存储组件
    # 暂时不可用，超大文件 / 不支持的格式也照样被拒，不会退化成 503 ——
    # 先校验入参再碰外部依赖，才是正确顺序。路由是同步函数，故用底层
    # 同步文件对象读字节，不用 async 的 read()
    for item in files:
        file_name = item.filename or "unnamed"
        if detect_file_type(file_name) is None:
            rejected.append({"file_name": file_name, "reason": "unsupported_format"})
            continue
        # 先按 Content-Length 预判大小，没有该头则读入后再量（同样在入队前拦截）
        data = item.file.read()
        size = item.size if item.size is not None else len(data)
        if size > MAX_UPLOAD_SIZE:
            rejected.append(
                {
                    "file_name": file_name,
                    "reason": f"too_large: 单文件不得超过 {MAX_UPLOAD_SIZE_MB}MB",
                }
            )
            continue
        task_id = f"t-{uuid4().hex[:12]}"
        queued.append(QueuedFile(task_id, file_name, data))
        accepted.append({"file_name": file_name, "task_id": task_id})

    # 第 3 步：仅当确有合法文件时，才取出后台执行器并校验存储组件就绪。
    # 若存储不可用，合法文件会拿到 503，但被拒文件（超大 / 格式不符）已
    # 在上一步完成判定，不受影响 —— 两者互不拖累
    if queued:
        runner = _get_runner(container)
        store = _get_store(container)
        for q in queued:
            store.create(
                q.task_id, q.file_name, creator_id=user.user_id, category=category
            )
        # 第 4 步：丢进后台线程池，立即返回。不等结果 ——
        # 等待就又把同步阻塞请了回来
        runner.submit(queued)

    return ok({"accepted": accepted, "rejected": rejected})


@router.get("/import/progress", summary="解析任务进度查询")
def import_progress(
    task_ids: str = Query(..., description="逗号分隔的任务标识，来自 import 响应"),
    _: CurrentUser = Depends(require_permission("knowledge:unit:create")),
    container: Container = Depends(get_container),
) -> dict:
    """按 ``task_ids`` 批量查询解析进度（第 14 章 #3 的落地）。

    :return: ``items`` 为逐任务的进度记录；``missing_task_ids`` 给出查不到的任务
        （记录已清理或标识输错）；``all_finished`` 标识是否全部到达终态 ——
        前端据此决定停止轮询（9.4"全部任务终态后停止"）。
    """
    # 第 1 步：切分并清洗入参，去掉空串与首尾空白
    wanted = [tid.strip() for tid in (task_ids or "").split(",") if tid.strip()]
    if not wanted:
        raise AppError("task_ids 不能为空", code=422, http_status=422)

    # 第 2 步：批量取
    store = _get_store(container)
    items = store.get_many(wanted)
    found = {item["task_id"] for item in items}

    # 第 3 步：回传终态判定与缺失项。缺失项如实说明，不伪造一条失败记录
    return ok(
        {
            "items": items,
            "missing_task_ids": [tid for tid in wanted if tid not in found],
            "all_finished": all(item["status"] in TERMINAL_STATUSES for item in items)
            and len(items) == len(wanted),
        }
    )


# ---------------------------------------------------------------------- 查询


@router.get("/units", summary="按标题、分类、状态分页查询")
def list_units(
    title: str | None = Query(default=None, description="标题模糊匹配"),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: CurrentUser = Depends(require_permission("knowledge:unit:read")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
    engine: DataPermissionEngine = Depends(get_permission_engine),
) -> dict:
    """分页查询知识单元列表。

    列表项带 ``permission_summary``（数据权限摘要），
    对应 2.9.3 知识单元列表里那一列"数据权限摘要"。
    """
    # 第 1 步：查询本体。列表不依赖向量库，因此这里不校验存储就绪
    service = KnowledgeUnitService(
        session, container.object_storage, container.vector_store
    )
    total, items = service.list_units(
        title=title, category=category, status=status, page=page, page_size=page_size
    )

    # 第 2 步：为当页单元批量取权限摘要，避免逐条查库
    rows = [
        {
            "id": unit.id,
            "unit_code": unit.unit_code,
            "title": unit.title,
            "category": unit.category,
            "file_type": unit.file_type,
            "status": unit.status,
            "creator_id": unit.creator_id,
            "updated_at": unit.updated_at,
            "permission_summary": _summarize(engine.list_unit_permissions(unit.id)),
        }
        for unit in items
    ]
    return ok({"total": total, "items": rows, "page": page, "page_size": page_size})


@router.get("/units/{id}", summary="知识单元详情与已配置的数据权限")
def get_unit(
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("knowledge:unit:read")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
    engine: DataPermissionEngine = Depends(get_permission_engine),
) -> dict:
    """返回单元详情（含正文）与已配置的权限实体列表。"""
    # 第 1 步：取单元本体
    service = KnowledgeUnitService(
        session, container.object_storage, container.vector_store
    )
    unit = service.get_unit(id) or _raise_not_found(id)

    # 第 2 步：取已配置的数据权限（含实体名称，供弹窗回填）
    permissions = engine.list_unit_permissions(id)

    return ok(
        {
            "id": unit.id,
            "unit_code": unit.unit_code,
            "title": unit.title,
            "content": unit.content,
            "summary": unit.summary,
            "category": unit.category,
            "source_file_name": unit.source_file_name,
            "file_type": unit.file_type,
            "file_size": unit.file_size,
            "status": unit.status,
            "creator_id": unit.creator_id,
            "created_at": unit.created_at,
            "updated_at": unit.updated_at,
            "permissions": permissions,
        }
    )


# ---------------------------------------------------------------------- 新建


@router.post("/units", summary="手工新建知识单元")
def create_unit(
    payload: UnitCreateRequest,
    user: CurrentUser = Depends(require_permission("knowledge:unit:create")),
    container: Container = Depends(get_container),
    session: Session = Depends(get_session),
) -> dict:
    """不经上传、直接录入一个知识单元（第 14 章 #2 的落地）。

    正文非空时会同步切片并写入向量库 —— 否则新建出来的单元检索不到。
    三个来源字段（``source_file_name`` / ``file_type`` / ``file_size``）留空，
    用于区分"上传而来"与"手工录入"。
    """
    # 第 1 步：装配服务（新建需要向量化能力，因此要求存储就绪）
    storage, vectors = _storage_ready(container)
    service = KnowledgeUnitService(session, storage, vectors)

    # 第 2 步：写库 + 向量化
    unit = service.create_unit(
        payload.title,
        payload.content,
        category=payload.category,
        summary=payload.summary,
        creator_id=user.user_id,
        status=payload.status or UnitStatus.ACTIVE.value,
    )
    # 第 3 步：返回与详情一致的字段，前端可直接跳详情页
    return ok(
        {
            "id": unit.id,
            "unit_code": unit.unit_code,
            "title": unit.title,
            "category": unit.category,
            "status": unit.status,
            "created_at": unit.created_at,
        }
    )


@router.put("/units/{id}", summary="更新知识单元")
def update_unit(
    payload: UnitUpdateRequest,
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("knowledge:unit:update")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """更新标题 / 正文 / 分类 / 摘要。

    正文变更后服务层会重新切片并重建向量；若向量库不可用，
    服务层会回滚正文改动并抛错，避免"库里有新正文、向量库还是旧切片"。
    """
    # 第 1 步：只把"显式传了的字段"交给服务层（未传即不改）
    provided = payload.model_dump(exclude_unset=True)

    # 第 2 步：写库（正文变更会触发向量重建）
    service = _service(session, container)
    unit = service.update_unit(id, **provided)
    return ok({"id": unit.id, "title": unit.title, "updated_at": unit.updated_at})


@router.put("/units/{id}/status", summary="状态流转")
def update_unit_status(
    payload: UnitStatusRequest,
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("knowledge:unit:update")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """变更知识单元状态（第 14 章 #1：版本管理确认为"仅状态流转"）。

    取值走白名单校验（#13 确认取值为 ``active``）。
    """
    service = KnowledgeUnitService(
        session, container.object_storage, container.vector_store
    )
    unit = service.update_status(id, payload.status)
    return ok({"id": unit.id, "status": unit.status, "updated_at": unit.updated_at})


@router.post("/units/{id}/permissions", summary="批量配置数据权限实体")
def set_unit_permissions(
    payload: UnitPermissionsRequest,
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("knowledge:unit:update")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """全量覆盖式保存该单元的数据权限实体（2.9.4 的四类可混合）。

    :return: ``{"saved": n}``。
    """
    service = KnowledgeUnitService(
        session, container.object_storage, container.vector_store
    )
    saved = service.set_unit_permissions(id, [item.model_dump() for item in payload.permissions])
    return ok({"saved": saved})


@router.delete("/units", summary="批量删除知识单元")
def delete_units(
    payload: UnitDeleteRequest,
    _: CurrentUser = Depends(require_permission("knowledge:unit:delete")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """批量删除，并同步删除权限记录与向量切片。

    向量库不可用时仍然删除数据库记录（服务层会跳过向量清理），
    返回值里说明实际删除数量。
    """
    service = KnowledgeUnitService(
        session, container.object_storage, container.vector_store
    )
    deleted = service.batch_delete(payload.unit_ids)
    return ok({"deleted": deleted})


@router.post("/check-permissions", summary="数据权限鉴权")
def check_permissions(
    payload: CheckPermissionsRequest,
    _: CurrentUser = Depends(get_current_user),
    engine: DataPermissionEngine = Depends(get_permission_engine),
) -> dict:
    """四维数据权限鉴权（2.9.4 的 OR 逻辑 + 默认拒绝）。

    这是 8.4 指定的鉴权接口，与 AI 链路内部调用的是**同一份实现**（5.4 要求），
    因此结果不会出现两套口径 —— 边界规则（部门继承、管理员绕过）也同源。
    """
    # 第 1 步：交给数据权限引擎判定
    result = engine.check_permissions(payload.user_id, payload.unit_ids)
    # 第 2 步：字段名与 8.4 的响应字段一一对应
    return ok(
        {
            "authorized_unit_ids": result.authorized_unit_ids,
            "unauthorized_unit_ids": result.unauthorized_unit_ids,
        }
    )


# ---------------------------------------------------------------------- 内部


def _summarize(permissions: list[dict]) -> str:
    """把权限列表压成一句摘要，供列表页展示（2.9.3 的"数据权限摘要"）。"""
    if not permissions:
        return "无权限（默认拒绝）"
    counts: dict[str, int] = {}
    for item in permissions:
        counts[item["target_type"]] = counts.get(item["target_type"], 0) + 1
    labels = {
        "global": "全局公开",
        "department": "部门",
        "role": "角色",
        "user": "个人",
    }
    parts = []
    for target_type, count in counts.items():
        name = labels.get(target_type, target_type)
        parts.append(name if target_type == "global" else f"{count} 个{name}")
    return " / ".join(parts)


def _raise_not_found(unit_id: int):
    """知识单元不存在时抛出 404（供 ``or`` 表达式使用）。"""
    raise AppError(f"知识单元不存在：id={unit_id}", code=404, http_status=404)
