"""知识单元与数据权限相关表模型。

对应 2.9.7 中的 2 张表：

    knowledge_units    知识单元表
    unit_permissions   知识单元数据权限实体表

字段定义对齐《技术方案设计文档》7.3 DDL。其中 ``unit_permissions`` 是
2.9.4 四维数据权限（global / department / role / user）的唯一落地表，
也是鉴权热路径 ``POST /api/knowledge/check-permissions`` 直接查询的表。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, CreatedAtMixin, LongTextType, TimestampMixin
from models.enums import GLOBAL_TARGET_ID

__all__ = ["KnowledgeUnit", "UnitPermission"]


class KnowledgeUnit(TimestampMixin, Base):
    """知识单元表 ``knowledge_units``。

    按 2.9.4 规则：每个独立导入的文档或手册作为一个知识单元存储，
    并拥有唯一标识。``unit_code`` 承担该唯一标识职责（对应接口与前端展示的
    "知识单元编号"），``content`` 存放解析后的正文全文。

    ``status`` 的取值集合在需求中未写明（文档第 14 章列为待确认项），
    因此这里只按 7.3 的 DDL 声明为 ``VARCHAR(32)`` 且默认 ``'active'``，
    不擅自定义枚举，避免把未确认的取值固化下来。
    """

    __tablename__ = "knowledge_units"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    unit_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="知识单元唯一编号"
    )
    title: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="标题"
    )
    content: Mapped[str | None] = mapped_column(
        LongTextType, nullable=True, comment="正文内容（LONGTEXT）"
    )
    summary: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="摘要"
    )
    category: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="分类"
    )
    source_file_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="来源文件名"
    )
    file_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="pdf / md / docx / txt"
    )
    file_size: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="文件字节数"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'"), comment="状态"
    )
    creator_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="创建人，对应 users.id"
    )

    __table_args__ = (
        UniqueConstraint("unit_code", name="uk_ku_code"),
        Index("idx_ku_category", "category"),
        Index("idx_ku_status", "status"),
        Index("idx_ku_creator", "creator_id"),
        Index("idx_ku_created", "created_at"),
        {"comment": "知识单元表"},
    )


class UnitPermission(CreatedAtMixin, Base):
    """知识单元数据权限实体表 ``unit_permissions``（2.9.4 数据权限规则）。

    一行代表"某知识单元授予了某个权限实体"。四类实体由 ``target_type`` 区分，
    取值见 ``models.enums.TargetType``：

    ==================  ==========================================
    ``target_type``     ``target_id`` 含义
    ==================  ==========================================
    ``global``          固定为 0（无具体实体，表示全局公开）
    ``department``      ``departments.id``
    ``role``            ``roles.id``
    ``user``            ``users.id``
    ==================  ==========================================

    两条规则直接体现在这张表的用法上：

    1. 默认无权限 —— 某知识单元在此表中没有任何记录时，除管理员外不可访问；
    2. OR 逻辑 —— 同一知识单元可存在多行，命中任意一行即可访问。

    该表为只追加的关系表，仅有 ``created_at``，无 ``updated_at``。
    """

    __tablename__ = "unit_permissions"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    unit_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="知识单元 id，对应 knowledge_units.id"
    )
    target_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="global / department / role / user"
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text(str(GLOBAL_TARGET_ID)),
        comment="目标实体 id，target_type=global 时固定为 0",
    )

    __table_args__ = (
        UniqueConstraint("unit_id", "target_type", "target_id", name="uk_unit_perm"),
        Index("idx_unit_perm_unit", "unit_id"),
        Index("idx_unit_perm_target", "target_type", "target_id"),
        {"comment": "知识单元数据权限表"},
    )
