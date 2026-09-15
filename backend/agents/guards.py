"""A1 AuthGuardAgent：登录态与 AI 访问权限守卫（3.1 / 4.3）。

对应 4.3 主图的第一个节点，也是**唯一的硬门**：

* 用户不存在或已停用 → ``auth_ok = False``，图直接短路结束；
* 已登录但没有 ``ai:chat:access`` 操作权限 → ``ai_permitted = False``，同样短路。

短路发生在**检索之前**：未登录用户连"召回"这个动作都不该触发
（2.9.4：智能体回答问题前必须校验用户登录态）。

**为什么这里还要再查一次库**：令牌只证明"签发时是谁"，用户可能在此期间被停用、
角色可能被收回。每次问答现查一次用户与角色，代价是一次查询，
换来的是"停用即刻生效"。
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from graph.events import EVENT_ERROR, EVENT_SESSION, emit, record_trace
from models import User, UserRole

__all__ = ["AuthGuardAgent", "AI_ACCESS_PERMISSION"]

# AI 问答访问的权限码（6.1 的 permission_type = ai）
AI_ACCESS_PERMISSION = "ai:chat:access"


class AuthGuardAgent:
    """A1：校验登录态、载入部门与角色、校验 AI 访问权限。"""

    def __init__(
        self,
        session: Session,
        *,
        permission_code: str = AI_ACCESS_PERMISSION,
    ) -> None:
        """:param session: 数据库会话。
        :param permission_code: AI 访问对应的操作权限码。
        """
        self._session = session
        self._permission_code = permission_code

    def __call__(self, state: dict) -> dict:
        """执行守卫检查，返回状态增量。"""
        started = time.monotonic()
        user_id = state.get("user_id")

        # 第 1 步：下发会话事件（4.8：会话新建时触发），前端据此拿到 session_id
        emit(EVENT_SESSION, {"session_id": state.get("session_id") or ""})

        # 第 2 步：取用户，校验存在性与启用态
        user = (
            self._session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
            if user_id
            else None
        )
        if user is None:
            emit(EVENT_ERROR, {"code": 401, "message": "用户不存在或登录已失效"})
            return self._deny(started, "用户不存在", auth_ok=False)
        if user.status != 1:
            emit(EVENT_ERROR, {"code": 403, "message": "账号已停用"})
            return self._deny(started, "账号已停用", auth_ok=False)

        # 第 3 步：载入角色（多角色取并集，与 6.1 一致）
        role_ids = [
            role_id
            for role_id in self._session.execute(
                select(UserRole.role_id).where(UserRole.user_id == user.id)
            ).scalars()
            if role_id is not None
        ]

        # 第 4 步：校验 AI 访问操作权限
        permitted = self._has_ai_permission(role_ids)
        if not permitted:
            emit(EVENT_ERROR, {"code": 403, "message": "缺少 AI 问答访问权限"})
            return {
                **self._deny(started, "缺少 AI 访问权限", auth_ok=True),
                "department_id": user.department_id,
                "role_ids": role_ids,
            }

        # 第 5 步：全部通过，把身份信息交给下游节点
        return {
            "auth_ok": True,
            "ai_permitted": True,
            "user_id": user.id,
            "department_id": user.department_id,
            "role_ids": role_ids,
            "trace": [
                record_trace(
                    "A1 AuthGuardAgent",
                    int((time.monotonic() - started) * 1000),
                    f"user={user.username} roles={role_ids}",
                )
            ],
        }

    # ------------------------------------------------------------------ 内部

    def _has_ai_permission(self, role_ids: list[int]) -> bool:
        """按角色现查 ``role_permissions`` 判定 AI 访问权限。"""
        if not role_ids:
            return False
        from models import RolePermission

        found = self._session.execute(
            select(RolePermission.id)
            .where(
                RolePermission.role_id.in_(role_ids),
                RolePermission.permission_code == self._permission_code,
            )
            .limit(1)
        ).scalar_one_or_none()
        return found is not None

    def _deny(self, started: float, detail: str, *, auth_ok: bool) -> dict:
        """构造拒绝结果。"""
        return {
            "auth_ok": auth_ok,
            "ai_permitted": False,
            "response_time_ms": int((time.monotonic() - started) * 1000),
            "trace": [record_trace("A1 AuthGuardAgent", 0, f"拒绝：{detail}")],
        }
