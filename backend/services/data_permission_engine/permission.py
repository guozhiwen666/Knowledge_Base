"""数据权限引擎（2.9.6 / 文档 5.4）。

职责与实现落点：

    输入 ``user_id`` + ``unit_ids``   -> :meth:`DataPermissionEngine.check_permissions`
    输出允许 / 拒绝两个列表           -> :class:`PermissionResult`
    四维实体 OR 判定                 -> :meth:`DataPermissionEngine._is_allowed`
    鉴权热路径的批量取数              -> :meth:`DataPermissionEngine._load_unit_permissions`

**同一份实现、两个调用方**：5.4 明确本引擎同时被内部 AI 链路（A4 Agent）与
对外接口 ``POST /api/knowledge/check-permissions`` 调用。因此这里只做纯计算，
不掺任何 HTTP 相关逻辑，两边拿到的是同一套结果。

**默认拒绝**：知识单元在 ``unit_permissions`` 里没有任何记录即不可访问（2.9.4）。

**fail-closed**：权限引擎一旦出错（取数失败、数据异常），结果是**全部计入未授权**，
绝不因为异常而放行 —— 放行是安全事故，拒绝只是体验问题。

**刻意不做的事**（需求未写明，见第 14 章【待确认】）：

* **部门树上下级继承**：2.9.4 只说"满足配置的数据权限实体"，未提继承，
  因此仅做精确匹配，父子部门不互相继承；
* **管理员绕过**：需求没有规定系统管理员 / 知识管理员天然可见全部单元，
  因此本引擎一视同仁，管理员同样受默认拒绝约束。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Department, Role, UnitPermission, User, UserRole
from models.enums import TargetType

__all__ = ["PermissionResult", "DataPermissionEngine"]


class PermissionResult(NamedTuple):
    """鉴权结果。

    字段名与 8.4 ``POST /api/knowledge/check-permissions`` 的响应字段一一对应，
    接口层拿到即可直接落响应，不做二次改名。

    :param authorized_unit_ids: 允许访问的知识单元 id 列表（保持传入顺序）。
    :param unauthorized_unit_ids: 拒绝访问的知识单元 id 列表（保持传入顺序）。
    """

    authorized_unit_ids: list[int]
    unauthorized_unit_ids: list[int]


class DataPermissionEngine:
    """四维数据权限引擎。

    依赖注入：会话由外部传入。本类**全部方法只读**，不写库、不开事务。
    """

    def __init__(self, session: Session) -> None:
        """:param session: SQLAlchemy 会话。"""
        self._session = session

    def check_permissions(
        self, user_id: int, unit_ids: Sequence[int]
    ) -> PermissionResult:
        """校验用户对一组知识单元的访问权限。

        :param user_id: 提问用户。
        :param unit_ids: 待鉴权的知识单元 id 列表，重复项会被去重。
        :return: :class:`PermissionResult`；任何异常都返回"全部未授权"。
        """
        # 第 1 步：入参归一化。去重但保持原顺序，让返回结果可预期、可对照
        ordered_ids = list(dict.fromkeys(int(i) for i in unit_ids))
        if not ordered_ids:
            return PermissionResult([], [])

        try:
            # 第 2 步：取用户上下文（所属部门 + 全部角色）
            context = self._load_user_context(user_id)
            if context is None:
                # 用户不存在（或被删）：无法判定身份，按 fail-closed 全部拒绝
                return PermissionResult([], ordered_ids)

            # 第 3 步：一次 IN 查询取回这批判单元的全部权限记录，避免逐个查库
            permissions = self._load_unit_permissions(ordered_ids)

            # 第 4 步：逐个单元做四维 OR 判定
            department_id, role_ids = context
            authorized: list[int] = []
            unauthorized: list[int] = []
            for unit_id in ordered_ids:
                rows = permissions.get(unit_id)
                if rows and self._is_allowed(rows, department_id, role_ids, user_id):
                    authorized.append(unit_id)
                else:
                    # 包含"没有任何权限记录"的情况 —— 默认拒绝
                    unauthorized.append(unit_id)
            return PermissionResult(authorized, unauthorized)
        except Exception:
            # fail-closed：引擎异常时全部计入未授权（文档 3.4 / 6.4）
            return PermissionResult([], ordered_ids)

    def list_unit_permissions(self, unit_id: int) -> list[dict]:
        """列出某知识单元已配置的权限实体（含实体名称）。

        供 8.4 的 ``GET /api/knowledge/units/{id}`` 展示"已配置的数据权限列表"，
        以及前端权限弹窗回填勾选状态。

        仍是只读操作，因此留在本引擎里 —— 权限的**读**统一由这里出口，
        **写**由知识单元管理服务负责。

        :return: ``[{"target_type", "target_id", "target_name"}, ...]``；
            没有配置过任何权限时返回空列表（即 2.9.4 的"默认无权限"）。
        """
        # 第 1 步：取该单元的权限记录
        rows = self._session.execute(
            select(UnitPermission.target_type, UnitPermission.target_id).where(
                UnitPermission.unit_id == unit_id
            )
        ).all()
        if not rows:
            return []

        # 第 2 步：按实体类型分组收集 id，便于对每类只查一次库（避免逐条查名称）
        ids_by_type: dict[str, set[int]] = {}
        for target_type, target_id in rows:
            ids_by_type.setdefault(target_type, set()).add(target_id)

        # 第 3 步：批量取名称
        names: dict[tuple[str, int], str] = {}
        for target_type, model, name_column in (
            (TargetType.DEPARTMENT.value, Department, Department.name),
            (TargetType.ROLE.value, Role, Role.role_name),
            (TargetType.USER.value, User, User.display_name),
        ):
            wanted = ids_by_type.get(target_type)
            if not wanted:
                continue
            found = self._session.execute(
                select(model.id, name_column).where(model.id.in_(list(wanted)))
            ).all()
            for entity_id, entity_name in found:
                names[(target_type, entity_id)] = entity_name or ""

        # 第 4 步：拼装结果。global 没有具体实体，名称固定为"全局公开"
        result: list[dict] = []
        for target_type, target_id in rows:
            if target_type == TargetType.GLOBAL.value:
                target_name = "全局公开"
            else:
                target_name = names.get((target_type, target_id), "")
            result.append(
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "target_name": target_name,
                }
            )
        # 按类型与 id 排序，保证同一单元的返回顺序稳定
        result.sort(key=lambda item: (item["target_type"], item["target_id"]))
        return result

    # ------------------------------------------------------------------ 内部

    def _load_user_context(self, user_id: int) -> tuple[int | None, set[int]] | None:
        """取用户的部门与角色集合。

        :return: ``(department_id, role_ids)``；用户不存在返回 ``None``。
        """
        # 第 1 步：取用户行（一次查询即可拿到部门，同时用于判断用户是否存在）
        user = self._session.execute(
            select(User).where(User.id == user_id)
        ).scalar_one_or_none()
        if user is None:
            return None
        # 第 2 步：取角色集合（可能为空集，空集时 role 维度永不命中）
        role_ids = {
            role_id
            for role_id in self._session.execute(
                select(UserRole.role_id).where(UserRole.user_id == user_id)
            ).scalars()
            if role_id is not None
        }
        return user.department_id, role_ids

    def _load_unit_permissions(
        self, unit_ids: Sequence[int]
    ) -> dict[int, list[tuple[str, int]]]:
        """批量取知识单元的权限记录。

        :return: ``{unit_id: [(target_type, target_id), ...]}``；
            没有任何记录的知识单元不会出现在字典里（即默认拒绝）。
        """
        grouped: dict[int, list[tuple[str, int]]] = {}
        rows = self._session.execute(
            select(
                UnitPermission.unit_id,
                UnitPermission.target_type,
                UnitPermission.target_id,
            ).where(UnitPermission.unit_id.in_(list(unit_ids)))
        ).all()
        for unit_id, target_type, target_id in rows:
            grouped.setdefault(unit_id, []).append((target_type, target_id))
        return grouped

    @staticmethod
    def _is_allowed(
        rows: Iterable[tuple[str, int]],
        department_id: int | None,
        role_ids: set[int],
        user_id: int,
    ) -> bool:
        """判定单个单元是否放行：四类实体命中任意一种即可（OR 逻辑）。

        对应 2.9.4 的四条匹配规则：

        * ``global``：全局公开，直接放行；
        * ``department``：用户所属部门与 ``target_id`` 精确相等（无部门则不匹配）；
        * ``role``：用户任一角色的 id 与 ``target_id`` 相等；
        * ``user``：``target_id`` 就是用户本人。
        """
        for target_type, target_id in rows:
            # 规则 1：全局公开
            if target_type == TargetType.GLOBAL.value:
                return True
            # 规则 2：部门精确匹配（用户无部门时该条不适用）
            if target_type == TargetType.DEPARTMENT.value:
                if department_id is not None and target_id == department_id:
                    return True
                continue
            # 规则 3：角色匹配（多角色取并集）
            if target_type == TargetType.ROLE.value:
                if target_id in role_ids:
                    return True
                continue
            # 规则 4：个人匹配
            if (
                target_type == TargetType.USER.value
                and target_id == user_id
            ):
                return True
            # 其他取值（脏数据）一律忽略，不影响其余规则判定
        return False
