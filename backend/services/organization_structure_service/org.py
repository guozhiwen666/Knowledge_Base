"""组织架构服务（2.9.6 / 文档 5.2）—— 对外唯一入口。

按 5.2 的职责编排，落点对应关系：

    维护 departments 树与成员     -> ``departments.DepartmentService``
    维护 users 与 user_roles      -> ``users.UserService``
    维护 roles 与 role_permissions -> ``roles.RoleService``
    门面（本模块）                -> 只做转发，不掺业务规则

**为什么拆成四个文件**：部门、用户、角色三块各自都有"读 + 写 + 占用检查"，
其中用户与部门还要处理哨兵语义与树形校验，注释密度高。
拆开后每个文件都能守住 300 行，对外仍只有这一个入口类。

**部门成员为什么反查而不建表**：5.2 明确"由 ``users.department_id`` 反查"，
因此成员关系只存在于 ``users`` 表上，本服务不额外维护关联表。

**第 14 章 #5 的落地**：原 8.3 只给了部门树查询、用户新增/编辑、角色列表与
授权五个接口，缺用户列表/详情/删除、部门增删改、角色增删改。
这些能力现在都在这里就位，接口层一一对应。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy.orm import Session

from models import Department, Role, User
from services.organization_structure_service.departments import DepartmentService
from services.organization_structure_service.roles import RoleService
from services.organization_structure_service.users import (
    UsernameTaken,
    UserService,
)

__all__ = ["UsernameTaken", "OrgService"]


class OrgService:
    """组织架构服务门面。

    依赖注入：会话由外部传入。事务边界由三个子服务各自把关 ——
    门面自己不 commit，避免出现"半途提交"。

    **参数为什么用 ``**fields`` 透传**：子服务用的是哨兵语义（"未传即不改"），
    门面若把未传的字段补成 ``None`` 再转发，"没传"就变成了"显式清空"，
    改个显示名会顺手把部门抹掉。因此门面原样转发调用方真正传了的字段。
    """

    def __init__(self, session: Session) -> None:
        """:param session: SQLAlchemy 会话。"""
        self._session = session
        self._departments = DepartmentService(session)
        self._users = UserService(session)
        self._roles = RoleService(session)

    # -------------------------------------------------------------- 部门

    def list_departments_tree(self) -> list[dict]:
        """部门树形列表（``GET /api/org/departments``）。"""
        return self._departments.list_departments_tree()

    def list_department_members(self, department_id: int) -> list[User]:
        """按部门反查成员（5.2：不为成员单独建关联表）。"""
        return self._departments.list_department_members(department_id)

    def create_department(self, name: str, **fields) -> Department:
        """新增部门（``POST /api/org/departments``）。"""
        return self._departments.create_department(name, **fields)

    def update_department(self, department_id: int, **fields) -> Department:
        """编辑部门（``PUT /api/org/departments/{id}``）。"""
        return self._departments.update_department(department_id, **fields)

    def delete_department(self, department_id: int) -> None:
        """删除部门（``DELETE /api/org/departments/{id}``），有子部门或成员则拒绝。"""
        self._departments.delete_department(department_id)

    # -------------------------------------------------------------- 用户

    def list_users(self, **filters) -> tuple[int, list[dict]]:
        """分页查询用户（``GET /api/org/users``）。"""
        return self._users.list_users(**filters)

    def get_user(self, user_id: int) -> User:
        """按主键取用户，不存在抛 ``LookupError``。"""
        return self._users.get_user(user_id)

    def get_user_detail(self, user_id: int) -> dict:
        """取单个用户的详情结构（``GET /api/org/users/{id}``）。"""
        return self._users.get_user_detail(user_id)

    def get_user_role_ids(self, user_id: int) -> list[int]:
        """取用户已分配的角色 id。"""
        return self._users.get_user_role_ids(user_id)

    def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        *,
        department_id: int | None = None,
        role_ids: Iterable[int] | None = None,
        status: int = 1,
    ) -> User:
        """新增用户（``POST /api/org/users``），口令哈希后入库。"""
        return self._users.create_user(
            username,
            display_name,
            password,
            department_id=department_id,
            role_ids=role_ids,
            status=status,
        )

    def update_user(self, user_id: int, **fields) -> User:
        """编辑用户（``PUT /api/org/users/{id}``，含启停用）。"""
        return self._users.update_user(user_id, **fields)

    def delete_user(self, user_id: int) -> None:
        """删除用户（``DELETE /api/org/users/{id}``）。"""
        self._users.delete_user(user_id)

    def reset_password(self, user_id: int, new_password: str | None = None) -> str:
        """重置用户口令（``POST /api/org/users/{id}/reset-password``）。

        :return: 新口令明文，供接口一次性展示给管理员。
        """
        return self._users.reset_password(user_id, new_password)

    # -------------------------------------------------------------- 角色

    def list_roles(self) -> list[dict]:
        """角色列表（``GET /api/org/roles``，含各自已分配的权限）。"""
        return self._roles.list_roles()

    def create_role(self, role_name: str, role_code: str, **fields) -> Role:
        """新增角色（``POST /api/org/roles``）。"""
        return self._roles.create_role(role_name, role_code, **fields)

    def update_role(self, role_id: int, **fields) -> Role:
        """编辑角色（``PUT /api/org/roles/{id}``）。"""
        return self._roles.update_role(role_id, **fields)

    def delete_role(self, role_id: int) -> None:
        """删除角色（``DELETE /api/org/roles/{id}``），仍有用户使用时拒绝。"""
        self._roles.delete_role(role_id)

    def set_role_permissions(
        self, role_id: int, permissions: Sequence[dict]
    ) -> int:
        """角色权限分配（``POST /api/org/roles/{id}/permissions``），全量覆盖式保存。"""
        return self._roles.set_role_permissions(role_id, permissions)
