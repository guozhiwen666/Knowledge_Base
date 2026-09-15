"""知识单元管理服务（2.9.6）主模块 —— 对外唯一入口。

按《技术方案设计文档》5.3 的六项职责编排，落点对应关系：

    1 文档上传 / 2 格式解析 / 3 文本切片 / 6 向量化同步
        -> ``knowledge_importer.KnowledgeUnitImporter``
           （内部复用 knowledge_parsers 与 knowledge_storage 两个协作者）
    4 知识单元 CRUD      -> get_unit / list_units / update_unit / batch_delete
    5 状态与版本管理      -> update_status（版本管理见下）

**同步模型**：本服务是同步（``def``）而非异步 —— 依赖的 SQLAlchemy Session、
minio、pymilvus 都是同步客户端，塞进 ``async def`` 只会在事件循环上阻塞；
接口层用 ``def`` 声明路由，FastAPI 会自动丢线程池。事务边界：一个公开方法
即一次完整事务，方法内自行 commit。

**三处刻意不做的事**（需求未写明，不擅自实现）：

* **版本管理**：5.3 写明"具体版本表或字段扩展见第 14 章【待确认】"，确认前不动表结构；
* **``tags`` / ``attachments`` 入参**：8.4 的 PUT 列了这两个字段，但 2.9.7 的
  ``knowledge_units`` 表没有对应列。没有存储位置就不接收入参，
  避免"接口答应了却存不下来"这种最难排查的假成功；
* **Milvus 标量同步**：``category`` / ``status`` 在 MySQL 侧变更后，Milvus 中的
  同名标量不会跟随 —— Milvus 不支持只改标量，必须整行（含向量）重写，即重新
  向量化。需求只规定了"内容变更后重新切片并同步"与"删除时同步删除向量"，
  这两条之外不做重写。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from models import KnowledgeUnit, UnitPermission
from models.enums import TargetType
from services.knowledge_unit_management_service.knowledge_importer import (
    KnowledgeUnitImporter,
    UnsupportedFileType,
    UploadedFile,
)
from services.knowledge_unit_management_service.knowledge_parsers import (
    CHUNK_MAX_CHARS,
    CHUNK_MIN_CHARS,
    split_into_chunks,
)
from services.knowledge_unit_management_service.knowledge_storage import ObjectStorage, VectorStore

__all__ = [
    "KnowledgeUnitNotFound",
    "UnsupportedFileType",
    "UploadedFile",
    "KnowledgeUnitService",
]

# 合法的权限实体类型（2.9.4 的四类）。直接从枚举取，避免两处各写一份取值
_VALID_TARGET_TYPES = frozenset(item.value for item in TargetType)


class KnowledgeUnitNotFound(LookupError):
    """知识单元不存在。接口层据此返回 404（见 8.1 错误码约定）。"""


class _Unset:
    """占位类型：区分"调用方没传这个字段"与"调用方显式传了 None"。"""

    __slots__ = ()


# 单例哨兵，配合 _Unset 使用
_UNSET = _Unset()


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
        self._max_chars = chunk_max_chars
        self._min_chars = chunk_min_chars
        # 导入流水线单独成类：本类只做编排与读写，不掺上传解析细节
        self._importer = KnowledgeUnitImporter(
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
    ) -> dict[str, list[dict[str, Any]]]:
        """批量导入文档（``POST /api/knowledge/import`` 的服务侧实现）。

        逐文件隔离失败的批量逻辑与单文件流水线都在导入流水线类里，本方法只做转发，
        保持"对外入口在本类、实现就近在协作者"的结构。入参含义见 8.4。
        :return: ``{"accepted": [...], "rejected": [...]}``。
        """
        return self._importer.import_documents(files, creator_id, category)

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

    # ------------------------------------------------------------------ 更新

    def update_unit(
        self,
        unit_id: int,
        *,
        title: str | _Unset = _UNSET,
        content: str | None | _Unset = _UNSET,
        category: str | None | _Unset = _UNSET,
        summary: str | None | _Unset = _UNSET,
    ) -> KnowledgeUnit:
        """更新知识单元；正文变更后重新切片并同步向量索引。

        字段按"未传即不改"处理，用哨兵 ``_UNSET`` 区分"没传"与"显式传 None"（后者用于
        把可空字段清空）。不接收 ``tags`` / ``attachments``，理由见模块说明。

        :raise KnowledgeUnitNotFound: 知识单元不存在。
        :raise ValueError: 标题被显式置空（``title`` 列非空，不允许）。
        """
        # 第 1 步：取单元，不存在直接抛错，不做静默创建
        unit = self.get_unit(unit_id)
        if unit is None:
            raise KnowledgeUnitNotFound(f"知识单元不存在：id={unit_id}")

        # 第 2 步：逐字段套用变更，标题额外做非空校验
        if title is not _UNSET:
            if not title:
                raise ValueError("知识标题不能为空")
            unit.title = title
        if summary is not _UNSET:
            unit.summary = summary
        if category is not _UNSET:
            unit.category = category
        # 正文变更需要重新切片，单独记标记
        content_changed = content is not _UNSET
        if content_changed:
            unit.content = content

        # 第 3 步：先落库拿到新值（updated_at 由 onupdate 自动刷新）
        self._session.flush()

        # 第 4 步：正文变更后重新切片并重同步向量（8.4 PUT 的明确要求）；
        # 向量写入失败则整体回滚，保证"库里正文"与"向量库切片"不脱节
        if content_changed:
            try:
                self._vectors.upsert_chunks(
                    unit_id=unit.id,
                    unit_code=unit.unit_code,
                    category=unit.category,
                    status=unit.status,
                    chunks=split_into_chunks(
                        unit.content or "", self._max_chars, self._min_chars
                    ),
                )
            except Exception:
                self._session.rollback()
                raise

        # 第 5 步：提交
        self._session.commit()
        return unit

    def update_status(self, unit_id: int, status: str) -> KnowledgeUnit:
        """变更知识单元状态（5.3 第 5 项：``status`` 承载状态流转）。

        状态取值集合尚未确认（第 14 章【待确认】），故不做取值白名单校验，只落库；
        取值确认后由接口层补校验。Milvus 侧 ``status`` 标量不同步，理由见模块说明。
        """
        # 第 1 步：取单元，不存在直接抛错
        unit = self.get_unit(unit_id)
        if unit is None:
            raise KnowledgeUnitNotFound(f"知识单元不存在：id={unit_id}")
        # 第 2 步：改状态并提交
        unit.status = status
        self._session.commit()
        return unit

    # ------------------------------------------------------------------ 权限

    def set_unit_permissions(
        self, unit_id: int, permissions: Sequence[Mapping[str, Any]]
    ) -> int:
        """批量配置知识单元的数据权限实体（``POST /api/knowledge/units/{id}/permissions``）。

        采用**全量覆盖**语义：先清空该单元现有权限记录、再写入传入集合。
        与角色权限分配保持一致 —— 界面上的勾选状态与库内记录必须完全对应，
        差量更新算错差集就会出现"界面上取消了、实际还有权限"这种最危险的偏差。

        **权限校验（读）归数据权限引擎，权限配置（写）归本服务**：
        2.9.6 描述引擎"负责计算并校验权限，输出允许列表与拒绝列表"，是只读的；
        而配置权限是知识单元自身的属性维护，属知识单元管理范畴。

        :param unit_id: 知识单元 id。
        :param permissions: 元素为 ``{"target_type": str, "target_id": int}``，
            ``target_type`` 取 global / department / role / user。
        :return: 实际写入的权限条数（按三元组去重）。
        :raise KnowledgeUnitNotFound: 知识单元不存在。
        :raise ValueError: ``target_type`` 非法或缺少 target_id。
        """
        # 第 1 步：确认知识单元存在
        if self.get_unit(unit_id) is None:
            raise KnowledgeUnitNotFound(f"知识单元不存在：id={unit_id}")

        # 第 2 步：先清空现有记录，保证覆盖语义
        self._session.execute(
            delete(UnitPermission)
            .where(UnitPermission.unit_id == unit_id)
            .execution_options(synchronize_session=False)
        )

        # 第 3 步：校验并按 (target_type, target_id) 去重后写入。
        # 唯一约束 uk_unit_perm 就是这三列，重复插入会直接撞约束
        unique: set[tuple[str, int]] = set()
        for item in permissions:
            target_type = (item or {}).get("target_type")
            if target_type not in _VALID_TARGET_TYPES:
                raise ValueError(f"非法的权限实体类型：{target_type}")
            raw_target_id = (item or {}).get("target_id", 0)
            try:
                target_id = int(raw_target_id)
            except (TypeError, ValueError):
                raise ValueError(f"非法的权限实体 id：{raw_target_id}") from None
            unique.add((target_type, target_id))

        for target_type, target_id in sorted(unique):
            self._session.add(
                UnitPermission(
                    unit_id=unit_id, target_type=target_type, target_id=target_id
                )
            )

        # 第 4 步：提交并返回实际写入条数
        self._session.commit()
        return len(unique)

    # ------------------------------------------------------------------ 删除

    def batch_delete(self, unit_ids: Sequence[int]) -> int:
        """批量删除知识单元，返回实际删除数量。

        按 8.4 约定同步删除 ``unit_permissions`` 关联记录与 Milvus 向量切片。删除顺序为
        **向量 → 权限记录 → 单元本体**：向量库与关系库不在同一事务里，先做可重放的一步 ——
        万一数据库提交失败，重跑导入即可恢复；反过来（先删库再删向量）会留下永远不知道
        属于谁、也永远清不掉的孤立向量。MinIO 原始文件不删除：8.4 只要求删权限记录与向量切片。

        :param unit_ids: 待删除的知识单元 id 列表，重复项会被去重。
        """
        # 第 1 步：去重并过滤空入参，避免无谓的库访问
        ids = sorted({int(i) for i in unit_ids})
        if not ids:
            return 0

        # 第 2 步：只处理真实存在的单元，不存在的 id 忽略（幂等删除）
        units = (
            self._session.execute(
                select(KnowledgeUnit).where(KnowledgeUnit.id.in_(ids))
            )
            .scalars()
            .all()
        )
        if not units:
            return 0
        found_ids = [unit.id for unit in units]

        # 第 3 步：同步删除 Milvus 中的向量切片
        for unit in units:
            self._vectors.delete_by_unit(unit.id)

        # 第 4 步：删除 unit_permissions 关联记录，避免留下指向已删单元的孤儿权限
        self._session.execute(
            delete(UnitPermission)
            .where(UnitPermission.unit_id.in_(found_ids))
            .execution_options(synchronize_session=False)
        )

        # 第 5 步：删除知识单元本体
        self._session.execute(
            delete(KnowledgeUnit)
            .where(KnowledgeUnit.id.in_(found_ids))
            .execution_options(synchronize_session=False)
        )

        # 第 6 步：提交并返回实际删除数量
        self._session.commit()
        return len(units)
