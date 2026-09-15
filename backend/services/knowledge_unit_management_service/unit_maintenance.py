"""知识单元管理服务 · 维护操作（新建 / 更新 / 状态流转 / 权限配置 / 删除）。

从 ``knowledge.py`` 拆出来的原因：门面还要承担导入转发与分页查询，
而本文件这五个方法都是**有副作用的写操作**，每个都要处理
"外部资源与关系库不在同一事务"的清理顺序，注释密度高，
留在原文件会把单文件推过 300 行。

事务边界：一个公开方法即一次完整事务，方法内自行 commit。
调用方（``KnowledgeUnitService``）只做转发，保证"对外入口在门面、
实现就近在协作者"的结构与导入流水线一致。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models import KnowledgeUnit, UnitPermission
from models.enums import TargetType, UnitStatus
from services.knowledge_unit_management_service.knowledge_importer import (
    generate_unit_code,
)
from services.knowledge_unit_management_service.knowledge_parsers import (
    CHUNK_MAX_CHARS,
    CHUNK_MIN_CHARS,
    split_into_chunks,
)
from services.knowledge_unit_management_service.knowledge_storage import (
    ObjectStorage,
    VectorStore,
)

__all__ = ["KnowledgeUnitNotFound", "KnowledgeUnitMaintenance"]

# 合法的权限实体类型（2.9.4 的四类）。直接从枚举取，避免两处各写一份取值
_VALID_TARGET_TYPES = frozenset(item.value for item in TargetType)

# 合法的知识单元状态（第 14 章 #13 确认取值为 active）。
# 做成白名单而不是"照单全收"，是为了挡住前端手滑传上来的任意字符串 ——
# status 参与列表筛选，脏值会让筛选项凭空多出几个永远点不到的按钮。
_VALID_UNIT_STATUS = frozenset(item.value for item in UnitStatus)


class KnowledgeUnitNotFound(LookupError):
    """知识单元不存在。接口层据此返回 404（见 8.1 错误码约定）。"""


class _Unset:
    """占位类型：区分"调用方没传这个字段"与"调用方显式传了 None"。

    与 ``organization_structure_service.org`` 内的同名哨兵保持一致 ——
    每个服务模块自带一份，不跨模块耦合。
    """

    __slots__ = ()


# 单例哨兵，配合 _Unset 使用
_UNSET = _Unset()


class KnowledgeUnitMaintenance:
    """知识单元的写入侧操作。

    依赖注入：会话、对象存储、向量存储由外部传入。
    对象存储只在删除时用于保留原始文件，本类不主动写对象存储
    （上传解析是导入流水线的职责）。
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
        :param storage: MinIO 封装（本类仅持有，不在新建流程中写对象）。
        :param vectors: Milvus 切片向量存储封装。
        :param chunk_max_chars: 切片长度阈值上限。
        :param chunk_min_chars: 尾块碎片判定阈值。
        """
        self._session = session
        self._storage = storage
        self._vectors = vectors
        self._max_chars = chunk_max_chars
        self._min_chars = chunk_min_chars

    # ------------------------------------------------------------------ 新建

    def create_unit(
        self,
        title: str,
        content: str | None = None,
        *,
        category: str | None = None,
        summary: str | None = None,
        creator_id: int | None = None,
        status: str = UnitStatus.ACTIVE.value,
    ) -> KnowledgeUnit:
        """手工新建知识单元（``POST /api/knowledge/units``）。

        与导入的区别只有一处来源：本方法不经文件解析，正文由调用方直接给出，
        因此 ``source_file_name`` / ``file_type`` / ``file_size`` 三列留空
        （7.3 的 DDL 允许为空），用于区分"上传而来"与"手工录入"。

        正文非空时同样要切片并写入向量库 —— 否则这个单元检索不到，
        等于建了个只能看不能问的摆设。

        :raise ValueError: 标题为空，或状态取值不在已确认的枚举内。
        """
        # 第 1 步：入参校验。title 列非空，status 列也非空，两者都在这里挡住
        if not title or not title.strip():
            raise ValueError("知识标题不能为空")
        if status not in _VALID_UNIT_STATUS:
            raise ValueError(f"不支持的知识单元状态：{status}")

        # 第 2 步：生成编号。与导入共用同一个生成函数，保证编号规则只有一处
        unit = KnowledgeUnit(
            unit_code=generate_unit_code(),
            title=title,
            content=content,
            summary=summary,
            category=category,
            creator_id=creator_id,
            status=status,
        )
        self._session.add(unit)
        # 第 3 步：flush 拿到自增主键，向量切片的标量字段要带上 unit_id
        self._session.flush()

        # 第 4 步：有正文才做向量化。空正文切不出切片，跳过即可，
        # 不必为了"统一"去调一次必然返回 0 条的向量写入
        if content and content.strip():
            try:
                self._vectors.upsert_chunks(
                    unit_id=unit.id,
                    unit_code=unit.unit_code,
                    category=unit.category,
                    status=unit.status,
                    chunks=split_into_chunks(
                        content, self._max_chars, self._min_chars
                    ),
                )
            except Exception:
                # 向量写入失败则该单元整体不成立：回滚数据库行，
                # 不留"库里有、检索不到"的半成品
                self._session.rollback()
                raise

        # 第 5 步：提交
        self._session.commit()
        return unit

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
        把可空字段清空）。不接收 ``tags`` / ``attachments``：8.4 列了这两个字段，
        但 2.9.7 的 ``knowledge_units`` 表没有对应列，没有存储位置就不接收入参。

        :raise KnowledgeUnitNotFound: 知识单元不存在。
        :raise ValueError: 标题被显式置空（``title`` 列非空，不允许）。
        """
        # 第 1 步：取单元，不存在直接抛错，不做静默创建
        unit = self._session.get(KnowledgeUnit, unit_id)
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

        第 14 章 #1 确认为"仅状态流转"，不引入独立版本表；#13 确认
        ``status`` 取值为 ``active``。取值走白名单校验。

        Milvus 侧的 ``status`` 标量**不跟随**：Milvus 不支持只改标量，
        必须整行（含向量）重写，即重新向量化一次。需求只规定了"内容变更后
        重新切片同步"与"删除时同步删除向量"，这两条之外不做重写。

        :raise KnowledgeUnitNotFound: 知识单元不存在。
        :raise ValueError: 状态取值不在已确认的枚举内。
        """
        # 第 1 步：取值白名单校验（脏值会让列表筛选多出点不到的选项）
        if status not in _VALID_UNIT_STATUS:
            raise ValueError(f"不支持的知识单元状态：{status}")
        # 第 2 步：取单元，不存在直接抛错
        unit = self._session.get(KnowledgeUnit, unit_id)
        if unit is None:
            raise KnowledgeUnitNotFound(f"知识单元不存在：id={unit_id}")
        # 第 3 步：改状态并提交
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

        :param permissions: 元素为 ``{"target_type": str, "target_id": int}``，
            四类实体可任意混合（2.9.4 的 OR 逻辑）。
        :return: 实际写入的权限条数（按三元组去重）。
        :raise KnowledgeUnitNotFound: 知识单元不存在。
        :raise ValueError: ``target_type`` 非法或 target_id 无法转成整数。
        """
        # 第 1 步：确认知识单元存在
        if self._session.get(KnowledgeUnit, unit_id) is None:
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
        # 第 1 步：去重，避免重复 id 让后面的 IN 查询做无用功
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
