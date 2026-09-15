"""组织架构服务（2.9.6 / 文档 5.2）。

职责与实现落点：

    维护 departments 树          -> :meth:`OrgService.list_departments_tree`
    维护 users（归属 / 启停用）   -> :meth:`OrgService.create_user` /
                                   :meth:`OrgService.update_user`
    维护 user_roles 映射         -> :meth:`OrgService._replace_user_roles`
    维护 role_permissions 分配   -> :meth:`OrgService.set_role_permissions`
    角色列表（含已分配权限）      -> :meth:`OrgService.list_roles`
    部门成员关联                 -> :meth:`OrgService.list_department_members`

**部门成员为什么单独建表**：不建。5.2 明确"由 ``users.department_id`` 反查"，
因此成员关系只存在于 ``users`` 表上，本服务不额外维护关联表。

**部门与用户的增删改为什么不齐**：8.4 只给出了部门树查询、用户新增、用户编辑
三个写读接口，**没有**部门增删改、用户删除、用户列表接口（见第 14 章【待确认】）。
没有对外接口就没有调用方，本服务不提前实现无处可调的方法。

**用户编辑为什么用哨兵**：2.9.3 要求用户管理支持"启停用"这一独立动作，它必然只改
``status`` 一个字段。若把更新做成整体覆盖，前端用一行陈旧数据就能把角色关联悄悄冲掉，
因此采用"未传即不改"的语义（见下方 ``_Unset`` 哨兵）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models import Department, Role, RolePermission, User, UserRole
from models.enums import UserStatus
from services.authentication_and_authorization_module.passwords import hash_password

__all__ = ["UsernameTaken", "OrgService"]


class UsernameTaken(ValueError):
    """登录名已被占用（``users.username`` 上有唯一约束 ``uk_users_username``）。"""


class _Unset:
    """占位类型：区分"调用方没传这个字段"与"调用方显式传了 None"。

    与 ``knowledge_unit_management_service.knowledge`` 内的同名哨兵保持一致 ——
    每个服务模块自带一份，不跨模块耦合。
    """

    __slots__ = ()


# 单例哨兵，比较时一律用 ``is`` / ``is not``
_UNSET = _Unset()


class OrgService:
    """组织架构服务。

    依赖注入：会话由外部传入。事务边界：每个写方法自行 commit；
    ``create_user`` 与 ``update_user`` 内部先 flush 拿主键、再同步角色关联，
    最后统一提交，保证用户与其角色关联要么都成功、要么都不落库。
    """

    def __init__(self, session: Session) -> None:
        """:param session: SQLAlchemy 会话。"""
        self._session = session

    # -------------------------------------------------------------- 部门

    def list_departments_tree(self) -> list[dict]:
        """返回部门树形列表（``GET /api/org/departments``）。

        节点字段与 8.3 一致：``id``、``parent_id``、``name``、``leader_id``、
        ``sort_order``，外加子节点列表 ``children``。
        """
        # 第 1 步：一次取全表并按 sort_order 排序，避免递归查库
        rows = (
            self._session.execute(
                select(Department).order_by(Department.sort_order, Department.id)
            )
            .scalars()
            .all()
        )
        # 第 2 步：在内存里组装成树
        return self._build_tree(rows)

    @staticmethod
    def _build_tree(rows: Sequence[Department]) -> list[dict]:
        """把扁平的部门列表组装成嵌套树。

        顶级部门的 ``parent_id`` 取值在需求中未明确（第 14 章【待确认】），
        可能是 ``NULL`` 也可能是 ``0``，因此两种写法都按顶级处理；
        另外父节点不存在（脏数据）的节点也提升为顶级，避免整棵子树丢失。
        """
        # 第 1 步：建索引，同时初始化每个节点的 children
        nodes: dict[int, dict] = {}
        for row in rows:
            nodes[row.id] = {
                "id": row.id,
                "parent_id": row.parent_id,
                "name": row.name,
                "leader_id": row.leader_id,
                "sort_order": row.sort_order,
                "children": [],
            }
        # 第 2 步：挂接子节点，识别顶级节点
        roots: list[dict] = []
        for row in rows:
            node = nodes[row.id]
            parent_id = row.parent_id
            if parent_id in (None, 0) or parent_id not in nodes or parent_id == row.id:
                roots.append(node)
            else:
                nodes[parent_id]["children"].append(node)
        return roots

    def list_department_members(self, department_id: int) -> list[User]:
        """按部门反查成员（5.2：不为成员单独建关联表）。

        :param department_id: 部门 id。
        :return: 该部门的用户列表，按 id 升序。
        """
        return list(
            self._session.execute(
                select(User)
                .where(User.department_id == department_id)
                .order_by(User.id)
            )
            .scalars()
            .all()
        )

    # -------------------------------------------------------------- 用户

    def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        *,
        department_id: int | None = None,
        role_ids: Iterable[int] | None = None,
        status: int = UserStatus.ENABLED.value,
    ) -> User:
        """新增用户（``POST /api/org/users``）。

        口令在本方法内哈希后入库，明文不落库（5.1）。

        :raise UsernameTaken: 登录名已存在。
        :raise ValueError: 登录名或显示名为空。
        """
        # 第 1 步：入参校验，唯一约束之外的校验放在这里做，报错更友好
        if not username or not username.strip():
            raise ValueError("登录名不能为空")
        if not display_name or not display_name.strip():
            raise ValueError("显示名不能为空")
        # 第 2 步：查重（表上有 uk_users_username，这里提前拦以获得明确报错）
        exists = self._session.execute(
            select(User.id).where(User.username == username).limit(1)
        ).scalar_one_or_none()
        if exists is not None:
            raise UsernameTaken(f"登录名已存在：{username}")
        # 第 3 步：建用户行，口令先哈希
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            department_id=department_id,
            status=status,
        )
        self._session.add(user)
        # 第 4 步：flush 拿到自增主键，角色关联表需要它
        self._session.flush()
        # 第 5 步：同步角色关联（多对多）
        self._replace_user_roles(user.id, role_ids or [])
        # 第 6 步：统一提交
        self._session.commit()
        return user

    def update_user(
        self,
        user_id: int,
        *,
        display_name: str | _Unset = _UNSET,
        department_id: int | None | _Unset = _UNSET,
        role_ids: Iterable[int] | _Unset = _UNSET,
        status: int | _Unset = _UNSET,
    ) -> User:
        """编辑用户（``PUT /api/org/users/{id}``）。

        字段按"未传即不改"处理：只传 ``status`` 就是启停用，
        只传 ``role_ids`` 就是重排角色，互不影响。

        :raise LookupError: 用户不存在。
        :raise ValueError: 显示名被显式置空（该列非空）。
        """
        # 第 1 步：取用户，不存在直接抛错
        user = self._session.get(User, user_id)
        if user is None:
            raise LookupError(f"用户不存在：id={user_id}")
        # 第 2 步：逐字段套用变更，显示名做非空校验
        if not isinstance(display_name, _Unset):
            if not display_name:
                raise ValueError("显示名不能为空")
            user.display_name = display_name
        if not isinstance(department_id, _Unset):
            user.department_id = department_id
        if not isinstance(status, _Unset):
            user.status = status
        # 第 3 步：角色关联是另一张表，显式传了才动（避免误清空）
        if not isinstance(role_ids, _Unset):
            self._replace_user_roles(user.id, role_ids)
        # 第 4 步：统一提交（用户行与角色关联同属一次事务）
        self._session.commit()
        return user

    def _replace_user_roles(self, user_id: int, role_ids: Iterable[int]) -> None:
        """同步用户角色关联：先清后插（5.2 允许的两种做法之一）。

        选"先清后插"而不是差量更新，是因为它天然幂等，
        不必关心"哪些要加、哪些要减"的差集计算，出错面更小。
        去重后插入，避免撞 ``uk_user_roles`` 唯一约束。
        """
        # 第 1 步：清掉该用户现有的全部角色关联
        self._session.execute(
            delete(UserRole)
            .where(UserRole.user_id == user_id)
            .execution_options(synchronize_session=False)
        )
        # 第 2 步：去重后逐条插入
        for role_id in sorted({int(r) for r in role_ids}):
            self._session.add(UserRole(user_id=user_id, role_id=role_id))

    # -------------------------------------------------------------- 角色

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

    def set_role_permissions(
        self, role_id: int, permissions: Sequence[dict]
    ) -> int:
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
