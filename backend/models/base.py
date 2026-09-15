"""数据模型公共基础设施。

本模块只提供三样东西，供 ``models`` 包内其他模块复用，不含任何业务表定义：

1. ``Base`` —— 全项目统一的 SQLAlchemy 2.x 声明式基类；
2. 时间戳混入 ``CreatedAtMixin`` / ``TimestampMixin`` —— 对应 2.9.7 中
   各表 ``created_at``、``updated_at`` 的两种不同写法；
3. 长文本列类型 ``LongTextType`` / ``MediumTextType`` —— 对齐 DDL 中的
   ``LONGTEXT`` 与 ``MEDIUMTEXT``。

列的命名、类型、可空性、默认值全部来自《技术方案设计文档》第 7.3 章 DDL，
不新增任何字段；文档未写明的字段一律不补。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text, func, text
from sqlalchemy.dialects.mysql import LONGTEXT, MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base",
    "CreatedAtMixin",
    "TimestampMixin",
    "LongTextType",
    "MediumTextType",
]


class Base(DeclarativeBase):
    """全局声明式基类。

    采用 SQLAlchemy 2.x 的 ``Mapped[...] + mapped_column()`` 写法，
    好处是列类型能被类型检查器（mypy / pyright）推导出来，
    不使用 1.x 时代的 ``Column(...)`` 写法，避免两种风格混用。

    本基类不定义任何公共列：因为 2.9.7 里并非所有表都有 ``updated_at``
    （如 ``user_roles``、``unit_permissions`` 只有 ``created_at``），
    公共列若放在基类上反而要到处覆盖，放在混入里更贴合原始设计。
    """


# MySQL 方言下的长文本列类型。
# SQLAlchemy 的 ``Text`` 在 MySQL 上只会生成 ``TEXT``，而 7.3 的 DDL 要求
# ``LONGTEXT`` / ``MEDIUMTEXT``，因此用 ``with_variant`` 在 MySQL 方言上替换；
# 其他方言（例如本地单测可能使用的 SQLite）自动退回 ``TEXT``，便于跑通建表校验。
# 注：这两个是标准类型实例，可安全地被多个列共享。
LongTextType = Text().with_variant(LONGTEXT(), "mysql")
MediumTextType = Text().with_variant(MEDIUMTEXT(), "mysql")


class CreatedAtMixin:
    """仅含 ``created_at`` 的时间戳混入。

    适用于 2.9.7 中只定义 ``created_at`` 的 4 张表：
    ``user_roles``、``role_permissions``、``unit_permissions``、``qa_access_logs``。
    这些表都是只追加的关系表或日志表，不记录更新时间。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),  # 生成 DEFAULT now()，与 DDL 的 CURRENT_TIMESTAMP 等价
        comment="创建时间",
    )


class TimestampMixin(CreatedAtMixin):
    """含 ``created_at`` + ``updated_at`` 的时间戳混入。

    适用于 2.9.7 中同时定义两个时间戳的 6 张表：
    ``departments``、``users``、``roles``、``knowledge_units``、``faqs``、``knowledge_gaps``。
    """

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        # server_onupdate 负责生成 DDL 中的 ON UPDATE CURRENT_TIMESTAMP
        server_onupdate=text("CURRENT_TIMESTAMP"),
        # onupdate 负责 ORM 侧更新时也刷新该列；两者同时保留，
        # 是为了让绕过数据库默认值（例如用 SQLite 做本地测试）时行为也一致
        onupdate=func.now(),
        comment="更新时间",
    )
