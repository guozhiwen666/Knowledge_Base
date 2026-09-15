"""知识单元管理服务（2.9.6）主模块 —— 对外唯一入口。

按《技术方案设计文档》5.3 的六项职责编排，落点对应关系：

    1 文档上传 / 2 格式解析 / 3 文本切片 / 6 向量化同步
        -> ``knowledge_importer.KnowledgeUnitImporter``
    4 知识单元 CRUD      -> 本模块的查询方法 + ``unit_maintenance`` 的写方法
    5 状态与版本管理      -> ``unit_maintenance.update_status``
    异步导入任务与进度     -> ``import_tasks.ImportTaskStore`` / ``ImportTaskRunner``

**为什么拆成四个文件**：导入流水线（importer）、格式解析（parsers）、
存储封装（storage）、写侧维护（unit_maintenance）都是"干活的"，
本模块只做**转发与查询**，这样每个文件的注释密度都能保持住。
对外仍只有这一个入口类，接口层不需要知道内部拆了几个文件。

**同步模型**：本服务是同步（``def``）而非异步 —— 依赖的 SQLAlchemy Session、
minio、pymilvus 都是同步客户端，塞进 ``async def`` 只会在事件循环上阻塞；
接口层用 ``def`` 声明路由，FastAPI 会自动丢线程池。事务边界：一个公开方法
即一次完整事务，方法内自行 commit。

**三处刻意不做的事**：

* **``tags`` / ``attachments`` 入参**：8.4 的 PUT 列了这两个字段，但 2.9.7 的
  ``knowledge_units`` 表没有对应列。没有存储位置就不接收入参，
  避免"接口答应了却存不下来"这种最难排查的假成功；
* **Milvus 标量同步**：``category`` / ``status`` 在 MySQL 侧变更后，
  Milvus 中的同名标量不会跟随 —— Milvus 不支持只改标量，必须整行（含向量）
  重写，即重新做一次向量化。需求只规定了"内容变更后重新切片并同步"与
  "删除时同步删除向量"，这两条之外不做重写；
* **独立版本表**：第 14 章 #1 已确认为"仅状态流转"，因此不引入版本表或版本字段。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import KnowledgeUnit
from services.knowledge_unit_management_service.knowledge_importer import (
    KnowledgeUnitImporter,
    UnsupportedFileType,
    UploadedFile,
)
from services.knowledge_unit_management_service.knowledge_parsers import (
    CHUNK_MAX_CHARS,
    CHUNK_MIN_CHARS,
)
from services.knowledge_unit_management_service.knowledge_storage import (
    ObjectStorage,
    VectorStore,
)
from services.knowledge_unit_management_service.unit_maintenance import (
    KnowledgeUnitMaintenance,
    KnowledgeUnitNotFound,
)

__all__ = [
    "KnowledgeUnitNotFound",
    "UnsupportedFileType",
    "UploadedFile",
    "KnowledgeUnitService",
]


class KnowledgeUnitService:
    """知识单元管理服务。

    依赖注入：会话、对象存储、向量存储由外部传入，便于替换实现与写测试。
    """

    def __init__(
        self,
        session: Session,
        storage: ObjectStorage,
        vectors: VectorStore,
        *,
        chunk_max_chars: int = CHUNK_MAX_CHARS,
        chunk_min_chars: int = CHUNK_MIN_CHARS,
    ) -> None:
        """:param session: SQLAlchemy 会话。
        :param storage: MinIO 原始文件存储封装。
        :param vectors: Milvus 切片向量存储封装。
        :param chunk_max_chars: 切片长度阈值上限。
        :param chunk_min_chars: 尾块碎片判定阈值。
        """
        self._session = session
        self._storage = storage
        self._vectors = vectors
        # 两个协作者：导入流水线负责"从文件到向量"，维护类负责"对已存在的单元动手"
        self._importer = KnowledgeUnitImporter(
            session,
            storage,
            vectors,
            chunk_max_chars=chunk_max_chars,
            chunk_min_chars=chunk_min_chars,
        )
        self._maintenance = KnowledgeUnitMaintenance(
            session,
            storage,
            vectors,
            chunk_max_chars=chunk_max_chars,
            chunk_min_chars=chunk_min_chars,
        )

    # ------------------------------------------------------------------ 导入

    def import_documents(
        self,
        files: Sequence[UploadedFile],
        creator_id: int | None = None,
        category: str | None = None,
        on_progress: Callable[[str, str, int], None] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """批量导入文档（``POST /api/knowledge/import`` 的服务侧实现）。

        逐文件隔离失败的批量逻辑与单文件流水线都在导入流水线类里，本方法只做转发，
        保持"对外入口在本类、实现就近在协作者"的结构。入参含义见 8.4。

        :param on_progress: 可选的 ``(file_name, stage, percent)`` 上报回调，
            同步调用不传即为原行为。
        :return: ``{"accepted": [...], "rejected": [...]}``。
        """
        return self._importer.import_documents(
            files, creator_id, category, on_progress=on_progress
        )

    # ------------------------------------------------------------------ 新建

    def create_unit(
        self,
        title: str,
        content: str | None = None,
        *,
        category: str | None = None,
        summary: str | None = None,
        creator_id: int | None = None,
        status: str | None = None,
    ) -> KnowledgeUnit:
        """手工新建知识单元（``POST /api/knowledge/units``，第 14 章 #2 的落地）。

        与导入的差别只在来源：不经文件解析，正文由调用方直接给出。
        正文非空时同样会切片并写入向量库，否则新建出来的单元检索不到。

        :raise ValueError: 标题为空或状态取值非法。
        """
        # 状态未传时由维护类按 7.3 的列默认值（active）落库
        kwargs: dict[str, Any] = {
            "category": category,
            "summary": summary,
            "creator_id": creator_id,
        }
        if status is not None:
            kwargs["status"] = status
        return self._maintenance.create_unit(title, content, **kwargs)

    # ------------------------------------------------------------------ 查询

    def get_unit(self, unit_id: int) -> KnowledgeUnit | None:
        """按主键取知识单元，不存在返回 ``None``。数据权限由权限引擎负责，此处只取本体。"""
        return self._session.get(KnowledgeUnit, unit_id)

    def list_units(
        self,
        *,
        title: str | None = None,
        category: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[KnowledgeUnit]]:
        """按标题、分类、状态分页查询（``GET /api/knowledge/units``）。

        标题模糊匹配，分类与状态精确匹配；三个条件都可选，未传不参与过滤。
        :return: ``(total, items)``，与 8.1 的分页约定一致。
        """
        # 第 1 步：入参兜底。页码非法会产生负数 offset，页大小过大可能拖垮查询
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)

        # 第 2 步：拼装过滤条件
        conditions = []
        if title:
            conditions.append(KnowledgeUnit.title.like(f"%{title}%"))
        if category:
            conditions.append(KnowledgeUnit.category == category)
        if status:
            conditions.append(KnowledgeUnit.status == status)

        # 第 3 步：先取总数（分页响应需要），再取当页数据
        count_stmt = select(func.count()).select_from(KnowledgeUnit)
        list_stmt = select(KnowledgeUnit)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
            list_stmt = list_stmt.where(*conditions)
        total = self._session.execute(count_stmt).scalar_one()

        # 第 4 步：按更新时间倒序，最近维护的排在前面
        rows = (
            self._session.execute(
                list_stmt.order_by(KnowledgeUnit.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return total, list(rows)

    # ------------------------------------------------------------------ 维护

    def update_unit(self, unit_id: int, **fields: Any) -> KnowledgeUnit:
        """更新知识单元（``PUT /api/knowledge/units/{id}``）。

        **参数用 ``**fields`` 透传，不写成显式关键字**：维护类用的是哨兵语义
        （"没传即不改"），若门面把未传的字段补成 ``None`` 再转发，
        "没传"就变成了"显式清空"，改个标题会顺手把分类抹掉。
        接口层已用 ``model_dump(exclude_unset=True)`` 过滤过一遍，
        这里只把调用方真正传了的字段原样交给维护类。
        """
        return self._maintenance.update_unit(unit_id, **fields)

    def update_status(self, unit_id: int, status: str) -> KnowledgeUnit:
        """变更知识单元状态（5.3 第 5 项，取值见第 14 章 #13）。"""
        return self._maintenance.update_status(unit_id, status)

    def set_unit_permissions(
        self, unit_id: int, permissions: Sequence[Mapping[str, Any]]
    ) -> int:
        """批量配置数据权限实体（``POST /api/knowledge/units/{id}/permissions``）。"""
        return self._maintenance.set_unit_permissions(unit_id, permissions)

    def batch_delete(self, unit_ids: Sequence[int]) -> int:
        """批量删除知识单元（``DELETE /api/knowledge/units``），含向量与权限记录。"""
        return self._maintenance.batch_delete(unit_ids)
