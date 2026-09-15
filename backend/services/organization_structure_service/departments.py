"""组织架构服务 · 部门（2.9.6 / 文档 5.2 的 departments 部分）。

从 ``org.py`` 拆出来的原因：门面还要承担用户与角色两块，
而部门这里有"树形组装 + 成员反查 + 增删改 + 删除前的占用检查"，
注释密度高，留在原文件会把单文件推过 300 行。

**部门成员为什么反查而不是建关联表**：5.2 明确"由 ``users.department_id`` 反查"，
因此成员关系只存在于 ``users`` 表上，本服务不额外维护关联表。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Department, User

__all__ = ["DepartmentService"]


class _Unset:
    """占位类型：区分"调用方没传这个字段"与"调用方显式传了 None"。"""

    __slots__ = ()


# 单例哨兵，比较时一律用 ``is`` / ``is not``
_UNSET = _Unset()


class DepartmentService:
    """部门的读写操作。

    依赖注入：会话由外部传入。事务边界：每个写方法自行 commit。
    """

    def __init__(self, session: Session) -> None:
        """:param session: SQLAlchemy 会话。"""
        self._session = session

    # ------------------------------------------------------------------ 读

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

        顶级部门的 ``parent_id`` 取值在需求中未明确，可能是 ``NULL`` 也可能是 ``0``，
        因此两种写法都按顶级处理；另外父节点不存在（脏数据）的节点也提升为顶级，
        避免整棵子树丢失。
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

    # ------------------------------------------------------------------ 写

    def create_department(
        self,
        name: str,
        *,
        parent_id: int | None = None,
        leader_id: int | None = None,
        sort_order: int = 0,
    ) -> Department:
        """新增部门（``POST /api/org/departments``）。

        :raise ValueError: 部门名为空，或父部门不存在。
        """
        # 第 1 步：名称非空（name 列非空）
        if not name or not name.strip():
            raise ValueError("部门名称不能为空")
        # 第 2 步：父部门必须真实存在 —— 指向一个不存在的父节点会让这个部门
        # 永远挂不到树上（组装树时会被当顶级节点提上来，位置就不对了）
        if parent_id not in (None, 0) and self._session.get(Department, parent_id) is None:
            raise ValueError(f"父部门不存在：id={parent_id}")
        # 第 3 步：建行并提交
        department = Department(
            name=name,
            parent_id=parent_id or None,
            leader_id=leader_id,
            sort_order=sort_order,
        )
        self._session.add(department)
        self._session.commit()
        return department

    def update_department(
        self,
        department_id: int,
        *,
        name: str | _Unset = _UNSET,
        parent_id: int | None | _Unset = _UNSET,
        leader_id: int | None | _Unset = _UNSET,
        sort_order: int | _Unset = _UNSET,
    ) -> Department:
        """编辑部门（``PUT /api/org/departments/{id}``）。

        字段按"未传即不改"处理：只改负责人不会顺手把上级部门改掉。

        :raise LookupError: 部门不存在。
        :raise ValueError: 部门名为空、父部门不存在，或把部门挂到自己/自己后代名下。
        """
        # 第 1 步：取部门
        department = self._session.get(Department, department_id)
        if department is None:
            raise LookupError(f"部门不存在：id={department_id}")
        # 第 2 步：逐字段套用变更
        if not isinstance(name, _Unset):
            if not name or not name.strip():
                raise ValueError("部门名称不能为空")
            department.name = name
        if not isinstance(parent_id, _Unset):
            self._check_parent(department_id, parent_id)
            department.parent_id = parent_id or None
        if not isinstance(leader_id, _Unset):
            department.leader_id = leader_id
        if not isinstance(sort_order, _Unset):
            department.sort_order = sort_order
        # 第 3 步：提交
        self._session.commit()
        return department

    def delete_department(self, department_id: int) -> None:
        """删除部门（``DELETE /api/org/departments/{id}``）。

        **有下级部门或在职成员时拒绝删除**，而不是级联删掉 ——
        级联会连带把成员挪成"无部门"状态（连他们自己都不知道），
        这种静默的数据变更比一个明确的报错难处理得多。

        :raise LookupError: 部门不存在。
        :raise ValueError: 该部门下还有子部门或成员。
        """
        # 第 1 步：取部门
        department = self._session.get(Department, department_id)
        if department is None:
            raise LookupError(f"部门不存在：id={department_id}")
        # 第 2 步：占用检查 —— 子部门
        child_count = self._session.execute(
            select(func.count())
            .select_from(Department)
            .where(Department.parent_id == department_id)
        ).scalar_one()
        if child_count:
            raise ValueError(f"该部门下还有 {child_count} 个子部门，请先处理子部门")
        # 第 3 步：占用检查 —— 成员
        member_count = self._session.execute(
            select(func.count()).select_from(User).where(User.department_id == department_id)
        ).scalar_one()
        if member_count:
            raise ValueError(f"该部门下还有 {member_count} 名成员，请先调整成员所属部门")
        # 第 4 步：删除并提交
        self._session.delete(department)
        self._session.commit()

    # ------------------------------------------------------------------ 内部

    def _check_parent(self, department_id: int, parent_id: int | None) -> None:
        """校验新的上级部门合法：必须存在，且不能是自己或自己的后代。

        自环会让组装树时的"识别顶级节点"把这些节点提到最外面，
        整棵子树的位置就不对了，因此在这里挡住。
        """
        if parent_id in (None, 0):
            return
        target = int(parent_id)
        if target == department_id:
            raise ValueError("上级部门不能是自己")
        # 一次取回全部父子关系，在内存里上溯，避免逐级查库
        parents = {
            int(row[0]): row[1]
            for row in self._session.execute(
                select(Department.id, Department.parent_id)
            ).all()
        }
        if target not in parents:
            raise ValueError(f"父部门不存在：id={parent_id}")
        current: int | None = target
        seen: set[int] = set()
        while current is not None and current not in seen:
            if current == department_id:
                raise ValueError("上级部门不能是自己的下级部门")
            seen.add(current)
            parent = parents.get(current)
            if parent is None or parent == 0:
                break
            current = int(parent)
