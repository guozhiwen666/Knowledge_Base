"""知识沉淀相关表模型。

对应 2.9.7 中的 2 张表：

    faqs               FAQ 表（含审核状态与缓存命中计数）
    knowledge_gaps     知识缺口表

这两张表是 2.9.9「知识沉淀」闭环的两端：

* ``faqs`` 承载"有答案的问题" —— 采集、去重、推荐、审核、发布、缓存命中计数；
* ``knowledge_gaps`` 承载"没有答案的问题" —— 未命中或低置信度提问的聚合结果，
  用于反向驱动知识管理员补全知识单元。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, MediumTextType, TimestampMixin
from models.enums import FaqStatus, KnowledgeGapStatus

__all__ = ["Faq", "KnowledgeGap"]


class Faq(TimestampMixin, Base):
    """FAQ 表 ``faqs``（2.9.3 知识沉淀管理页 + 2.9.9 挖掘与审核发布规则）。

    ``source_type`` 区分来源：``manual``（人工录入）/ ``auto_mined``（沉淀引擎推荐）。
    ``status`` 为三态流转，取值见 ``models.enums.FaqStatus``：

    * 沉淀引擎新生成的推荐项 → ``pending_review``
    * 管理员审核通过 → ``published``，同时写入缓存，审核人与审核时间落
      ``reviewer_id`` / ``reviewed_at``
    * 管理员驳回 → ``rejected``

    ``hit_count`` 记录缓存命中次数，是 2.9.3「已发布 FAQ 库」展示缓存生效状态的依据。
    """

    __tablename__ = "faqs"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    question: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="标准问题"
    )
    answer: Mapped[str | None] = mapped_column(
        MediumTextType, nullable=True, comment="标准答案（MEDIUMTEXT）"
    )
    category: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="分类"
    )
    related_unit_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="关联知识单元，对应 knowledge_units.id"
    )
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="manual / auto_mined"
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=text(f"'{FaqStatus.PENDING_REVIEW.value}'"),
        comment="pending_review / published / rejected",
    )
    hit_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), comment="缓存命中次数"
    )
    reviewer_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="审核人，对应 users.id"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="审核时间"
    )

    __table_args__ = (
        Index("idx_faq_status", "status"),
        Index("idx_faq_source", "source_type"),
        Index("idx_faq_unit", "related_unit_id"),
        {"comment": "FAQ 表"},
    )


class KnowledgeGap(TimestampMixin, Base):
    """知识缺口表 ``knowledge_gaps``（2.9.3 知识缺口列表 + 2.9.9 缺口识别规则）。

    按 2.9.9 判定条件写入：提问召回相似度低于设定阈值，**或**无可用知识单元支撑。
    同模式提问不做多行记录，而是就地累加：``ask_count`` 累加、``last_asked_at``
    刷新、``sample_questions_json`` 保留原始提问样本。

    ``status`` 取值见 ``models.enums.KnowledgeGapStatus``，其中 ``resolved``
    表示已补全新知识单元，并把该单元 id 写入 ``resolved_unit_id``
    （对应 2.9.3「一键创建关联知识单元补全需求」）。
    """

    __tablename__ = "knowledge_gaps"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    question_pattern: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="问题模式 / 聚类代表问题"
    )
    sample_questions_json: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True, comment="原始提问样本列表"
    )
    ask_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), comment="提问频次"
    )
    last_asked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近提问时间"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text(f"'{KnowledgeGapStatus.UNRESOLVED.value}'"),
        comment="unresolved / resolved / ignored",
    )
    resolved_unit_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="补全后的知识单元，对应 knowledge_units.id"
    )

    __table_args__ = (
        Index("idx_gap_status", "status"),
        Index("idx_gap_count", "ask_count"),
        {"comment": "知识缺口表"},
    )
