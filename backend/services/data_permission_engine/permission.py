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

**两条已裁决的边界**（第 14 章 #9 / #10 确认列）：

* **部门树上下级继承 = 继承**：原 6.4 表格按"不继承"实现，现已确认改为继承。
  语义是"用户所属部门的**下级**部门授权同样命中"，即父部门的人能看到子部门的知识，
  反之（子部门的人看父部门的知识）不成立。因此匹配范围 = 用户部门自身 ∪ 其全部祖先部门。
* **管理员绕过 = 是**：系统管理员与知识管理员角色天然可见全部知识单元。
  绕过名单由配置项 ``admin_role_codes`` 给出（默认 ``sys_admin`` / ``kb_admin``），
  不写死在代码里 —— 换一套角色编码不用改代码。
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


class _UserContext(NamedTuple):
    """鉴权所需的用户上下文（一次取数，避免 N+1）。

    :param user_id: 用户 id。
    :param department_scope: 部门匹配范围 = 用户部门自身 ∪ 其祖先部门（#9 继承）。
        用户无部门时为空集。
    :param role_ids: 用户全部角色 id。
    :param role_codes: 用户全部角色编码，用于 #10 的管理员绕过判定。
    """

    user_id: int
    department_scope: frozenset[int]
    role_ids: frozenset[int]
    role_codes: frozenset[str]


class DataPermissionEngine:
    """四维数据权限引擎。

    依赖注入：会话由外部传入。本类**全部方法只读**，不写库、不开事务。
    """

    def __init__(
        self,
        session: Session,
        *,
        inherit_departments: bool = True,
        admin_role_codes: Iterable[str] = (),
    ) -> None:
        """:param session: SQLAlchemy 会话。
        :param inherit_departments: 部门树是否上下级继承（第 14 章 #9，默认继承）。
        :param admin_role_codes: 天然绕过数据权限的角色编码（第 14 章 #10）。
        """
        self._session = session
        self._inherit_departments = inherit_departments
        self._admin_role_codes = frozenset(admin_role_codes)

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
            # 第 2 步：取用户上下文（部门匹配范围 + 角色 id + 角色编码）
            context = self._load_user_context(user_id)
            if context is None:
                # 用户不存在（或被删）：无法判定身份，按 fail-closed 全部拒绝
                return PermissionResult([], ordered_ids)

            # 第 3 步：管理员绕过的唯一出口（第 14 章 #10）。
            # 放在四维判定之前，语义是"不参与数据权限校验"，而不是"额外多了一条授权规则"
            if self._is_admin(context):
                return PermissionResult(ordered_ids, [])

            # 第 4 步：一次 IN 查询取回这批判单元的全部权限记录，避免逐个查库
            permissions = self._load_unit_permissions(ordered_ids)

            # 第 5 步：逐个单元做四维 OR 判定
            authorized: list[int] = []
            unauthorized: list[int] = []
            for unit_id in ordered_ids:
                rows = permissions.get(unit_id)
                if rows and self._is_allowed(rows, context):
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

    def _load_user_context(self, user_id: int) -> _UserContext | None:
        """取用户上下文：部门匹配范围 + 角色 id + 角色编码。

        :return: :class:`_UserContext`；用户不存在返回 ``None``。
        """
        # 第 1 步：取用户行（一次查询即可拿到部门，同时用于判断用户是否存在）
        user = self._session.execute(
            select(User).where(User.id == user_id)
        ).scalar_one_or_none()
        if user is None:
            return None
        # 第 2 步：取角色。一次 JOIN 把 id 与编码一起取回，供绕过判定复用
        role_rows = self._session.execute(
            select(Role.id, Role.role_code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        ).all()
        role_ids = frozenset(int(row[0]) for row in role_rows if row[0] is not None)
        role_codes = frozenset(str(row[1]) for row in role_rows if row[1])
        # 第 3 步：算出部门匹配范围（#9 继承确认后不再是单个 id）
        scope = self._department_scope(user.department_id)
        return _UserContext(user_id, scope, role_ids, role_codes)

    def _is_admin(self, context: _UserContext) -> bool:
        """判断是否属于"天然绕过数据权限"的管理员（第 14 章 #10）。

        绕过名单来自配置项 ``admin_role_codes``，不写死在代码里。
        名单为空集时本方法恒为假 —— 即"关闭管理员绕过"。
        """
        if not self._admin_role_codes:
            return False
        return bool(self._admin_role_codes & context.role_codes)

    def _department_scope(self, department_id: int | None) -> frozenset[int]:
        """算出部门维度的匹配范围。

        #9 确认为**继承**：用户所属部门的下级部门授权同样命中，因此范围是
        "用户部门自身 ∪ 其全部祖先部门"。例如用户属"技术部"（父为"总部"），
        则授权给"技术部"或"总部"的知识单元都能看到。

        继承关闭时退化为"只匹配自身"，即原 6.4 表格的口径。
        父链一次性取回后在内存里遍历，避免逐级查库。
        """
        if department_id is None:
            # 无部门的用户：department 维度永不命中（6.4 明确规定）
            return frozenset()
        if not self._inherit_departments:
            return frozenset({department_id})

        # 第 1 步：一次取回全部部门的父子关系（部门是小表，全量取回远快于逐级查库）
        parents = {
            int(row[0]): row[1]
            for row in self._session.execute(
                select(Department.id, Department.parent_id)
            ).all()
        }
        # 第 2 步：沿父链上溯，用 seen 防脏数据造成的环
        scope: set[int] = set()
        current: int | None = department_id
        while current is not None and current not in scope:
            scope.add(current)
            parent = parents.get(current)
            if parent is None or parent == 0:
                break
            current = int(parent)
        return frozenset(scope)

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
    def _is_allowed(rows: Iterable[tuple[str, int]], context: _UserContext) -> bool:
        """判定单个单元是否放行：四类实体命中任意一种即可（OR 逻辑）。

        对应 2.9.4 的四条匹配规则（部门那条按 #9 改为范围匹配）：

        * ``global``：全局公开，直接放行；
        * ``department``：``target_id`` 落在用户的部门匹配范围内（无部门则不匹配）；
        * ``role``：用户任一角色的 id 与 ``target_id`` 相等；
        * ``user``：``target_id`` 就是用户本人。
        """
        for target_type, target_id in rows:
            # 规则 1：全局公开
            if target_type == TargetType.GLOBAL.value:
                return True
            # 规则 2：部门范围匹配（#9 继承确认后含祖先部门；无部门时范围为空集）
            if target_type == TargetType.DEPARTMENT.value:
                if target_id in context.department_scope:
                    return True
                continue
            # 规则 3：角色匹配（多角色取并集）
            if target_type == TargetType.ROLE.value:
                if target_id in context.role_ids:
                    return True
                continue
            # 规则 4：个人匹配
            if (
                target_type == TargetType.USER.value
                and target_id == context.user_id
            ):
                return True
            # 其他取值（脏数据）一律忽略，不影响其余规则判定
        return False
