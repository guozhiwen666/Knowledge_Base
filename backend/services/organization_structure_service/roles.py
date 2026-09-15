"""组织架构服务 · 角色（2.9.6 / 文档 5.2 的 roles / role_permissions 部分）。

从 ``org.py`` 拆出来的原因同 ``departments.py``：角色这里有
"列表带权限 + 增删改 + 权限全量覆盖"，且删除前要做占用检查，
单独成文件后注释密度能保持住。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from models import Role, RolePermission, UserRole

__all__ = ["RoleService"]


class _Unset:
    """占位类型：区分"调用方没传这个字段"与"调用方显式传了 None"。"""

    __slots__ = ()


# 单例哨兵，比较时一律用 ``is`` / ``is not``
_UNSET = _Unset()


class RoleService:
    """角色的读写操作。

    依赖注入：会话由外部传入。事务边界：每个写方法自行 commit。
    """

    def __init__(self, session: Session) -> None:
        """:param session: SQLAlchemy 会话。"""
        self._session = session

    # ------------------------------------------------------------------ 读

    def list_roles(self) -> list[dict]:
        """返回角色列表及各自已分配的权限（``GET /api/org/roles``）。

        每个元素含 ``id``、``role_name``、``role_code``、``description``、
        ``permissions``（``permission_code`` + ``permission_type`` 列表）。
        """
        # 第 1 步：取全部角色
        roles = self._session.execute(
            select(Role).order_by(Role.id)
        ).scalars().all()
        # 第 2 步：一次取回全部角色权限记录，避免按角色逐个查（N+1）
        permissions_by_role: dict[int, list[dict]] = {}
        for row in self._session.execute(select(RolePermission)).scalars():
            permissions_by_role.setdefault(row.role_id, []).append(
                {
                    "permission_code": row.permission_code,
                    "permission_type": row.permission_type,
                }
            )
        # 第 3 步：拼装返回结构
        return [
            {
                "id": role.id,
                "role_name": role.role_name,
                "role_code": role.role_code,
                "description": role.description,
                "permissions": permissions_by_role.get(role.id, []),
            }
            for role in roles
        ]

    # ------------------------------------------------------------------ 写

    def create_role(
        self,
        role_name: str,
        role_code: str,
        *,
        description: str | None = None,
    ) -> Role:
        """新增角色（``POST /api/org/roles``）。

        :raise ValueError: 角色名或编码为空，或编码已存在。
        """
        # 第 1 步：名称与编码非空（两列都非空，且 role_code 上有唯一约束）
        if not role_name or not role_name.strip():
            raise ValueError("角色名称不能为空")
        if not role_code or not role_code.strip():
            raise ValueError("角色编码不能为空")
        # 第 2 步：编码查重。表上有唯一约束，这里提前拦以获得明确报错
        exists = self._session.execute(
            select(Role.id).where(Role.role_code == role_code).limit(1)
        ).scalar_one_or_none()
        if exists is not None:
            raise ValueError(f"角色编码已存在：{role_code}")
        # 第 3 步：建行并提交
        role = Role(role_name=role_name, role_code=role_code, description=description)
        self._session.add(role)
        self._session.commit()
        return role

    def update_role(
        self,
        role_id: int,
        *,
        role_name: str | _Unset = _UNSET,
        role_code: str | _Unset = _UNSET,
        description: str | None | _Unset = _UNSET,
    ) -> Role:
        """编辑角色（``PUT /api/org/roles/{id}``）。

        字段按"未传即不改"处理。改 ``role_code`` 要重新查重 ——
        它被第 14 章 #10 的管理员绕过名单引用，编码写错会让绕过失效。

        :raise LookupError: 角色不存在。
        :raise ValueError: 名称为空或编码与他者重复。
        """
        # 第 1 步：取角色
        role = self._session.get(Role, role_id)
        if role is None:
            raise LookupError(f"角色不存在：id={role_id}")
        # 第 2 步：逐字段套用变更
        if not isinstance(role_name, _Unset):
            if not role_name or not role_name.strip():
                raise ValueError("角色名称不能为空")
            role.role_name = role_name
        if not isinstance(role_code, _Unset):
            if not role_code or not role_code.strip():
                raise ValueError("角色编码不能为空")
            duplicated = self._session.execute(
                select(Role.id)
                .where(Role.role_code == role_code, Role.id != role_id)
                .limit(1)
            ).scalar_one_or_none()
            if duplicated is not None:
                raise ValueError(f"角色编码已存在：{role_code}")
            role.role_code = role_code
        if not isinstance(description, _Unset):
            role.description = description
        # 第 3 步：提交
        self._session.commit()
        return role

    def delete_role(self, role_id: int) -> None:
        """删除角色（``DELETE /api/org/roles/{id}``），连带清掉其权限分配。

        **仍有用户挂着该角色时拒绝删除**：``user_roles`` 里若残留指向已删角色的记录，
        用户的权限码会静默变少（少几个菜单），排查起来要翻三张表。

        :raise LookupError: 角色不存在。
        :raise ValueError: 仍有用户使用该角色。
        """
        # 第 1 步：取角色
        role = self._session.get(Role, role_id)
        if role is None:
            raise LookupError(f"角色不存在：id={role_id}")
        # 第 2 步：占用检查
        user_count = self._session.execute(
            select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id)
        ).scalar_one()
        if user_count:
            raise ValueError(f"仍有 {user_count} 个用户使用该角色，请先调整用户角色")
        # 第 3 步：清权限分配，避免留下指向已删角色的孤儿权限记录
        self._session.execute(
            delete(RolePermission)
            .where(RolePermission.role_id == role_id)
            .execution_options(synchronize_session=False)
        )
        # 第 4 步：删角色并提交
        self._session.delete(role)
        self._session.commit()

    def set_role_permissions(self, role_id: int, permissions: Sequence[dict]) -> int:
        """配置角色的操作权限（``POST /api/org/roles/{id}/permissions``）。

        采用**全量覆盖**语义：先清空该角色现有权限、再写入传入集合。这样界面上的
        勾选状态与库内记录必然一致 —— 差量更新一旦算错差集，就会出现
        "界面上取消了、实际还有权限"这种最危险的偏差。

        :param permissions: 元素为 ``{"permission_code": str, "permission_type": str}``。
        :return: 写入的权限条数（已按权限码去重）。
        :raise LookupError: 角色不存在。
        :raise ValueError: 存在缺少 ``permission_code`` 的元素。
        """
        # 第 1 步：校验角色存在
        if self._session.get(Role, role_id) is None:
            raise LookupError(f"角色不存在：id={role_id}")
        # 第 2 步：先清空现有权限，保证覆盖语义
        self._session.execute(
            delete(RolePermission)
            .where(RolePermission.role_id == role_id)
            .execution_options(synchronize_session=False)
        )
        # 第 3 步：按权限码去重后写入（表上有 uk_role_perm 唯一约束）
        unique: dict[str, str] = {}
        for item in permissions:
            code = (item or {}).get("permission_code")
            if not code:
                raise ValueError("permission_code 不能为空")
            unique[code] = (item or {}).get("permission_type") or ""
        for code, permission_type in unique.items():
            self._session.add(
                RolePermission(
                    role_id=role_id,
                    permission_code=code,
                    permission_type=permission_type,
                )
            )
        # 第 4 步：提交并返回实际写入条数
        self._session.commit()
        return len(unique)
