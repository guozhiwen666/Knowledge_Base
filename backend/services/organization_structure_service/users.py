"""组织架构服务 · 用户（2.9.6 / 文档 5.2 的 users / user_roles 部分）。

从 ``org.py`` 拆出来的原因同 ``departments.py``：用户这里有
"分页查询带角色 + 新增 + 部分更新 + 删除 + 重置口令"，
且"部分更新"要处理哨兵语义，单独成文件后注释密度能保持住。

**用户编辑为什么用哨兵**：2.9.3 要求用户管理支持"启停用"这一独立动作，
它必然只改 ``status`` 一个字段。若把更新做成整体覆盖，前端用一行陈旧数据
就能把角色关联悄悄冲掉，因此采用"未传即不改"的语义。

**口令哈希从哪来**：只依赖 ``passwords`` 模块，**不 import ``auth``** ——
一旦导入 auth 就会把 PyJWT 拖进组织架构服务，而后者根本用不到令牌。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from models import Department, Role, User, UserRole
from models.enums import UserStatus
from services.authentication_and_authorization_module.passwords import (
    INITIAL_PASSWORD,
    hash_password,
)

__all__ = ["UsernameTaken", "UserService"]


class UsernameTaken(ValueError):
    """登录名已被占用（``users.username`` 上有唯一约束 ``uk_users_username``）。"""


class _Unset:
    """占位类型：区分"调用方没传这个字段"与"调用方显式传了 None"。"""

    __slots__ = ()


# 单例哨兵，比较时一律用 ``is`` / ``is not``
_UNSET = _Unset()


class UserService:
    """用户的读写操作。

    依赖注入：会话由外部传入。事务边界：每个写方法自行 commit；
    ``create_user`` / ``update_user`` 内部先 flush 拿主键、再同步角色关联，
    最后统一提交，保证用户与其角色关联要么都成功、要么都不落库。
    """

    def __init__(self, session: Session) -> None:
        """:param session: SQLAlchemy 会话。"""
        self._session = session

    # ------------------------------------------------------------------ 读

    def list_users(
        self,
        *,
        keyword: str | None = None,
        department_id: int | None = None,
        status: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        """分页查询用户（``GET /api/org/users``）。

        ``keyword`` 同时匹配登录名与显示名（模糊），另两个条件精确匹配。

        :return: ``(total, items)``；每项含 ``id``、``username``、``display_name``、
            ``department_id``、``department_name``、``status``、``role_ids``、
            ``role_names``、``created_at``。
        """
        # 第 1 步：入参兜底，避免负数 offset 与过大的页
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)

        # 第 2 步：拼装过滤条件
        conditions = []
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(User.username.like(pattern), User.display_name.like(pattern))
            )
        if department_id is not None:
            conditions.append(User.department_id == department_id)
        if status is not None:
            conditions.append(User.status == status)

        # 第 3 步：总数
        count_stmt = select(func.count()).select_from(User)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = self._session.execute(count_stmt).scalar_one()

        # 第 4 步：当页数据
        list_stmt = select(User)
        if conditions:
            list_stmt = list_stmt.where(*conditions)
        users = (
            self._session.execute(
                list_stmt.order_by(User.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        if not users:
            return total, []

        # 第 5 步：批量补部门名与角色，避免逐行查库（一页 20 行就是 40 次查询）
        rows = self._decorate(users)
        return total, rows

    def get_user(self, user_id: int) -> User:
        """按主键取用户。

        :raise LookupError: 用户不存在。
        """
        user = self._session.get(User, user_id)
        if user is None:
            raise LookupError(f"用户不存在：id={user_id}")
        return user

    def get_user_detail(self, user_id: int) -> dict:
        """取单个用户的详情结构（``GET /api/org/users/{id}``）。

        :raise LookupError: 用户不存在。
        """
        user = self.get_user(user_id)
        return self._decorate([user])[0]

    def get_user_role_ids(self, user_id: int) -> list[int]:
        """取用户已分配的角色 id（按 id 升序）。"""
        rows = self._session.execute(
            select(UserRole.role_id)
            .where(UserRole.user_id == user_id)
            .order_by(UserRole.role_id)
        ).scalars()
        return [int(role_id) for role_id in rows if role_id is not None]

    # ------------------------------------------------------------------ 写

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

    def delete_user(self, user_id: int) -> None:
        """删除用户（``DELETE /api/org/users/{id}``），连带清掉其角色关联。

        ``unit_permissions`` 中指向该用户的记录**不删**：它表达的是
        "这份知识授权给这个岗位的人"，用户离职后岗位可能由他人接替，
        静默删掉会让接替者莫名其妙看不到东西。要撤销请走知识单元的权限配置。

        :raise LookupError: 用户不存在。
        """
        # 第 1 步：取用户
        user = self._session.get(User, user_id)
        if user is None:
            raise LookupError(f"用户不存在：id={user_id}")
        # 第 2 步：清角色关联，避免留下指向已删用户的孤儿记录
        self._session.execute(
            delete(UserRole)
            .where(UserRole.user_id == user_id)
            .execution_options(synchronize_session=False)
        )
        # 第 3 步：删用户并提交
        self._session.delete(user)
        self._session.commit()

    def reset_password(self, user_id: int, new_password: str | None = None) -> str:
        """重置用户口令（``POST /api/org/users/{id}/reset-password``）。

        :return: 实际写入的新口令**明文**，只在这一次返回给管理员 ——
            库里存的是哈希，不回传的话管理员无从得知该用什么口令登录。
        :raise LookupError: 用户不存在。
        :raise ValueError: 显式传入的新口令为空串（会把账号锁死）。
        """
        # 第 1 步：取用户
        user = self.get_user(user_id)
        # 第 2 步：确定新口令。显式传空串属于误用，直接拒绝 ——
        # 空口令哈希出来照样能存，但谁都登不上，是纯粹的坑
        if new_password is not None and not new_password.strip():
            raise ValueError("新口令不能为空")
        effective = new_password or INITIAL_PASSWORD
        # 第 3 步：写哈希并提交
        user.password_hash = hash_password(effective)
        self._session.commit()
        return effective

    # ------------------------------------------------------------------ 内部

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

    def _decorate(self, users: list[User]) -> list[dict]:
        """给用户行补上部门名与角色名。

        两次批量查询搞定整页，不做 N+1。
        """
        user_ids = [user.id for user in users]

        # 第 1 步：批量取角色映射
        role_rows = self._session.execute(
            select(UserRole.user_id, Role.id, Role.role_name)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids))
        ).all()
        roles_by_user: dict[int, tuple[list[int], list[str]]] = {}
        for user_id, role_id, role_name in role_rows:
            ids, names = roles_by_user.setdefault(int(user_id), ([], []))
            ids.append(int(role_id))
            names.append(role_name or "")

        # 第 2 步：批量取部门名
        dept_ids = {user.department_id for user in users if user.department_id}
        dept_names: dict[int, str] = {}
        if dept_ids:
            dept_names = {
                int(row[0]): (row[1] or "")
                for row in self._session.execute(
                    select(Department.id, Department.name).where(
                        Department.id.in_(list(dept_ids))
                    )
                ).all()
            }

        # 第 3 步：拼装。角色那两列从同一次取值里来，别让默认值建两遍
        rows: list[dict] = []
        for user in users:
            role_ids, role_names = roles_by_user.get(user.id, ([], []))
            rows.append(
                {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "department_id": user.department_id,
                    "department_name": dept_names.get(user.department_id or 0, ""),
                    "status": user.status,
                    "role_ids": role_ids,
                    "role_names": role_names,
                    "created_at": user.created_at,
                }
            )
        return rows
