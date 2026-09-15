"""数据模型中出现的枚举取值集合。

取值来源严格限定为以下三处，不新增任何取值：

* 2.9.4 数据权限规则 —— ``target_type`` 的四类实体；
* 2.9.7 各表字段说明 —— FAQ 状态三态、知识缺口状态三态、``source_type`` 等；
* 《技术方案设计文档》第 7.3 章 DDL 的字段注释 —— ``permission_type``、``file_type``。

关于存储形式：7.3 的 DDL 把上述列一律声明为 ``VARCHAR`` 而非 MySQL ``ENUM``
（便于后续增删取值而不改表结构），所以本模块的枚举**不用于映射列类型**，
只作为业务层的取值常量与入参校验依据。
"""

from __future__ import annotations

from enum import Enum, IntEnum

__all__ = [
    "GLOBAL_TARGET_ID",
    "TargetType",
    "PermissionType",
    "FaqSourceType",
    "FaqStatus",
    "KnowledgeGapStatus",
    "FileType",
    "UserStatus",
]


# 2.9.4：全局权限实体没有具体目标，``unit_permissions.target_id`` 固定填 0。
GLOBAL_TARGET_ID = 0


class TargetType(str, Enum):
    """``unit_permissions.target_type`` 的取值（2.9.4 数据权限规则）。

    四类实体可混合配置，满足任意一种即为可访问（OR 逻辑）。
    """

    GLOBAL = "global"          # 全局公开，target_id 固定为 GLOBAL_TARGET_ID
    DEPARTMENT = "department"  # 按部门授权，target_id = departments.id
    ROLE = "role"              # 按角色授权，target_id = roles.id
    USER = "user"              # 按个人授权，target_id = users.id


class PermissionType(str, Enum):
    """``role_permissions.permission_type`` 的取值（7.3 DDL 字段注释）。

    对应 2.9.4 操作权限规则的三种权限类别。
    """

    MENU = "menu"            # 功能菜单访问权限
    OPERATION = "operation"  # 知识单元增删改查等操作权限
    AI = "ai"                # AI 问答访问权限


class FaqSourceType(str, Enum):
    """``faqs.source_type`` 的取值（2.9.7）。

    区分该 FAQ 是人工录入还是沉淀引擎自动挖掘出来的推荐项。
    """

    MANUAL = "manual"            # 人工录入
    AUTO_MINED = "auto_mined"    # 沉淀引擎从历史对话挖掘


class FaqStatus(str, Enum):
    """``faqs.status`` 的取值（2.9.7 + 2.9.9 审核发布规则）。

    状态流转：``PENDING_REVIEW`` --审核通过--> ``PUBLISHED``（同时写入缓存）；
    ``PENDING_REVIEW`` --审核驳回--> ``REJECTED``。
    """

    PENDING_REVIEW = "pending_review"  # 待审核
    PUBLISHED = "published"            # 已发布上线
    REJECTED = "rejected"              # 已驳回


class KnowledgeGapStatus(str, Enum):
    """``knowledge_gaps.status`` 的取值（2.9.7 + 2.9.9 知识缺口识别规则）。"""

    UNRESOLVED = "unresolved"  # 未解决，等待补充知识
    RESOLVED = "resolved"      # 已由新知识单元补全
    IGNORED = "ignored"        # 已忽略，不纳入统计


class FileType(str, Enum):
    """``knowledge_units.file_type`` 的取值（7.3 DDL 字段注释）。

    与 2.9.3 知识导入中心声明的支持格式一一对应：
    PDF、Markdown、Word、TXT，四类之外不接收。
    """

    PDF = "pdf"        # PDF 文档
    MARKDOWN = "md"    # Markdown 文档
    WORD = "docx"      # Word 文档
    TXT = "txt"        # 纯文本文档


class UserStatus(IntEnum):
    """``users.status`` 的取值（7.3 DDL 字段注释：1 启用、0 停用）。"""

    ENABLED = 1   # 启用
    DISABLED = 0  # 停用
