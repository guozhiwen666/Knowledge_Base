"""组织架构与账号权限相关表模型。

对应 2.9.7 中与组织、账号、角色、权限映射相关的 5 张表：

    departments      部门表
    users            用户表
    roles            角色表
    user_roles       用户-角色关联表
    role_permissions 角色权限表

列的命名、类型、长度、可空性、唯一约束与索引名，全部对齐
《技术方案设计文档》7.3 DDL，不额外增删字段。

关于外键：7.3 的 DDL 没有定义任何 ``FOREIGN KEY`` 约束，只对关联列建了普通索引
（这是写入吞吐优先的常规做法），因此本模块同样不声明外键，表间关联由
业务层的 JOIN 维护。若后续需要 ORM 关系属性，再单独评估。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, CreatedAtMixin, TimestampMixin

__all__ = ["Department", "User", "Role", "UserRole", "RolePermission"]


class Department(TimestampMixin, Base):
    """部门表 ``departments``（2.9.3 部门管理：维护树形结构、负责人与成员关联）。

    树形结构由 ``parent_id`` 自引用表达；7.3 未规定顶级部门的 ``parent_id``
    取值（文档第 14 章列为待确认项），此处保持可空，以同时兼容 NULL 与 0 两种写法。
    部门与成员的关联不单独建表，由 ``users.department_id`` 反查。
    """

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="上级部门 id，顶级部门为 NULL"
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="部门名称"
    )
    leader_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="部门负责人，对应 users.id"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="同级排序"
    )

    __table_args__ = (
        Index("idx_departments_parent", "parent_id"),
        {"comment": "部门表"},
    )


class User(TimestampMixin, Base):
    """用户表 ``users``（2.9.3 用户管理：部门归属、角色关联、启停用）。

    ``status`` 取值见 ``models.enums.UserStatus``：1 启用、0 停用。
    停用用户不允许登录，因此在认证阶段即被拒绝，不进入数据权限判定环节。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="登录名"
    )
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="密码哈希，禁止存明文"
    )
    display_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="显示名"
    )
    department_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="所属部门，对应 departments.id"
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, server_default=text("1"), comment="1 启用 / 0 停用"
    )

    __table_args__ = (
        UniqueConstraint("username", name="uk_users_username"),
        Index("idx_users_department", "department_id"),
        {"comment": "用户表"},
    )


class Role(TimestampMixin, Base):
    """角色表 ``roles``（2.9.2 三类角色：系统管理员 / 知识管理员 / 普通用户）。

    ``role_code`` 唯一，是业务侧判角色用的稳定编码；``role_name`` 只用于展示，
    允许改名而不影响已分配的权限。
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    role_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="角色名称"
    )
    role_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="角色编码，业务判断依据"
    )
    description: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="角色描述"
    )

    __table_args__ = (
        UniqueConstraint("role_code", name="uk_roles_code"),
        {"comment": "角色表"},
    )


class UserRole(CreatedAtMixin, Base):
    """用户-角色关联表 ``user_roles``（多对多映射）。

    该表为只追加的关系表，仅有 ``created_at``，无 ``updated_at``。
    用户可同时拥有多个角色，数据权限与操作权限判定时取并集。
    """

    __tablename__ = "user_roles"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="用户 id，对应 users.id"
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="角色 id，对应 roles.id"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uk_user_roles"),
        Index("idx_user_roles_role", "role_id"),
        {"comment": "用户角色关联表"},
    )


class RolePermission(CreatedAtMixin, Base):
    """角色权限表 ``role_permissions``（2.9.3 角色管理：配置操作权限树）。

    ``permission_type`` 取值见 ``models.enums.PermissionType``：
    ``menu``（菜单访问）/ ``operation``（知识单元增删改查）/ ``ai``（AI 问答访问）。
    判定规则为 OR：用户任一角色持有该权限码即放行。
    """

    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="角色 id，对应 roles.id"
    )
    permission_code: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="权限编码"
    )
    permission_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="menu / operation / ai"
    )

    __table_args__ = (
        UniqueConstraint("role_id", "permission_code", name="uk_role_perm"),
        Index("idx_role_perm_role", "role_id"),
        {"comment": "角色权限表"},
    )
