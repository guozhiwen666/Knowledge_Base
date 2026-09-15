"""知识单元接口（8.4）。

实现 8.4 列出的 7 个接口：

    POST   /api/knowledge/import                    单文件 / 多文件批量导入
    GET    /api/knowledge/units                     分页查询
    GET    /api/knowledge/units/{id}                详情 + 已配置的数据权限
    PUT    /api/knowledge/units/{id}                更新（正文变更会重建向量）
    POST   /api/knowledge/units/{id}/permissions     批量配置数据权限实体
    DELETE /api/knowledge/units                     批量删除
    POST   /api/knowledge/check-permissions         数据权限鉴权

**未实现的接口**：8.4 没有"知识单元手工新建"接口（第 14 章【待确认】），
也没有解析进度查询接口。因此导入只返回逐文件的终态结果，不做进度轮询。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from sqlalchemy.orm import Session

from core.container import Container, get_container
from core.db import get_session
from core.response import AppError, ok
from core.security import CurrentUser, get_current_user, require_permission
from schemas.requests import (
    CheckPermissionsRequest,
    UnitDeleteRequest,
    UnitPermissionsRequest,
    UnitUpdateRequest,
)
from services.data_permission_engine.permission import DataPermissionEngine
from services.knowledge_unit_management_service.knowledge import (
    KnowledgeUnitService,
    UploadedFile as UploadedFileItem,
)

__all__ = ["router"]

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


@router.post("/import", summary="单文件或多文件批量导入")
def import_documents(
    files: list[UploadFile] = File(..., description="PDF / Markdown / Word / TXT"),
    category: str | None = Form(default=None, description="统一分类，可选"),
    user: CurrentUser = Depends(require_permission("knowledge:unit:create")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """接收 multipart/form-data 上传并解析入库。

    返回结构对齐 8.4：``accepted`` 逐文件给出已入库的知识单元与切片数，
    ``rejected`` 逐文件给出被拒原因（``unsupported_format`` 或解析失败信息）。
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

    # 第 2 步：装配服务
    storage, vectors = _storage_ready(container)
    service = KnowledgeUnitService(session, storage, vectors)

    # 第 3 步：把 UploadFile 读成字节。路由是同步函数，因此用底层同步文件对象，
    # 不用 async 的 UploadFile.read()
    payload = [
        UploadedFileItem(file_name=item.filename or "unnamed", data=item.file.read())
        for item in files
    ]

    # 第 4 步：交给服务层逐文件处理（单文件失败不中断其余）
    result = service.import_documents(payload, creator_id=user.user_id, category=category)
    return ok(result)


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
    engine = DataPermissionEngine(session)
    rows = []
    for unit in items:
        rows.append(
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
        )
    return ok({"total": total, "items": rows, "page": page, "page_size": page_size})


@router.get("/units/{id}", summary="知识单元详情与已配置的数据权限")
def get_unit(
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("knowledge:unit:read")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """返回单元详情（含正文）与已配置的权限实体列表。"""
    # 第 1 步：取单元本体
    service = KnowledgeUnitService(
        session, container.object_storage, container.vector_store
    )
    unit = service.get_unit(id) or _raise_not_found(id)

    # 第 2 步：取已配置的数据权限（含实体名称，供弹窗回填）
    permissions = DataPermissionEngine(session).list_unit_permissions(id)

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
    kwargs = {field: getattr(payload, field) for field in provided}

    # 第 2 步：写库（正文变更会触发向量重建）
    service = _service(session, container)
    unit = service.update_unit(id, **kwargs)
    return ok({"id": unit.id, "title": unit.title, "updated_at": unit.updated_at})


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
    session: Session = Depends(get_session),
) -> dict:
    """四维数据权限鉴权（2.9.4 的 OR 逻辑 + 默认拒绝）。

    这是 8.4 指定的鉴权接口，与 AI 链路内部调用的是**同一份实现**（5.4 要求），
    因此结果不会出现两套口径。
    """
    # 第 1 步：交给数据权限引擎判定
    result = DataPermissionEngine(session).check_permissions(
        payload.user_id, payload.unit_ids
    )
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
