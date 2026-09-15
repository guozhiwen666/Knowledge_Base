"""数据模型层（data models）。

本包按《技术方案设计文档》第 7 章，把 2.9.7 定义的 10 张业务表落成
SQLAlchemy 2.x 声明式模型，并额外提供 7.5 的 Milvus 集合结构定义。

模块划分：

============================  ==================================================
模块                          内容
============================  ==================================================
``base``                      ``Base`` 基类、时间戳混入、长文本列类型
``enums``                     各枚举字段的取值集合
``org``                       ``departments`` / ``users`` / ``roles`` /
                              ``user_roles`` / ``role_permissions``
``knowledge``                 ``knowledge_units`` / ``unit_permissions``
``qa``                        ``qa_access_logs``
``settlement``                ``faqs`` / ``knowledge_gaps``
``vector``                    Milvus 集合 ``kb_unit_chunks`` 的结构定义
============================  ==================================================

导入约定：应用内统一用包内绝对路径导入，例如
``from app.models import KnowledgeUnit``，便于以 ``backend`` 为工作目录
用 ``uvicorn app.main:app`` 启动。

注意：``vector`` 模块**不在此处导入**。它依赖 pymilvus，而关系库模型依赖
SQLAlchemy，两者是不同的第三方依赖；若一并导入，则未装 pymilvus 的环境
连 ORM 模型都用不了。需要向量结构时显式导入
``from app.models.vector import COLLECTION_NAME``。
"""

from __future__ import annotations

# 基础与枚举
from models.base import (
    Base,
    CreatedAtMixin,
    LongTextType,
    MediumTextType,
    TimestampMixin,
)
from models.enums import (
    GLOBAL_TARGET_ID,
    FaqSourceType,
    FaqStatus,
    FileType,
    KnowledgeGapStatus,
    PermissionType,
    TargetType,
    UserStatus,
)

# 数据模型
from models.knowledge import KnowledgeUnit, UnitPermission
from models.org import Department, Role, RolePermission, User, UserRole
from models.qa import QaAccessLog
from models.settlement import Faq, KnowledgeGap

__all__ = [
    # 基础
    "Base",
    "CreatedAtMixin",
    "TimestampMixin",
    "LongTextType",
    "MediumTextType",
    # 枚举
    "GLOBAL_TARGET_ID",
    "TargetType",
    "PermissionType",
    "FaqSourceType",
    "FaqStatus",
    "KnowledgeGapStatus",
    "FileType",
    "UserStatus",
    # 表模型
    "Department",
    "User",
    "Role",
    "UserRole",
    "RolePermission",
    "KnowledgeUnit",
    "UnitPermission",
    "QaAccessLog",
    "Faq",
    "KnowledgeGap",
]
