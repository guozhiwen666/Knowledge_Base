"""鉴权依赖：登录态校验与 RBAC 操作权限拦截（对应 5.1 的"以 FastAPI 依赖形式挂载"）。

用法：

    @router.get("/xxx", dependencies=[Depends(require_permission("menu:org"))])
    def handler(user: CurrentUser = Depends(get_current_user)): ...

**为什么校验收敛在这里**：接口层每个路由只声明"需要哪个权限码"，
不允许自己解令牌或自己查 role_permissions —— 一旦有两处判权，
权限就会在两处之间漂移，这是最容易出安全事故的写法。

**令牌校验后仍要查库**：令牌里只有 ``role_ids``，权限码要按角色现查
``role_permissions``。这样角色权限改了立即生效，不必等令牌过期。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from core.config import get_settings
from core.container import get_container
from core.db import get_session
from core.response import PermissionDeniedError, UnauthorizedError
from services.authentication_and_authorization_module.auth import AuthService, TokenInvalid
from services.data_permission_engine.permission import DataPermissionEngine

__all__ = [
    "CurrentUser",
    "get_auth_service",
    "get_current_user",
    "get_permission_engine",
    "require_permission",
]

# auto_error=False：未带令牌时不由 FastAPI 直接抛 403，交给本模块统一返回 401
_bearer = HTTPBearer(auto_error=False)


class CurrentUser(NamedTuple):
    """当前登录用户（从令牌载荷里解出，不含敏感字段）。"""

    user_id: int
    username: str
    department_id: int | None
    role_ids: list[int]


def get_auth_service(session: Session = Depends(get_session)) -> AuthService:
    """构造认证服务。密钥与有效期来自配置；缓存用于令牌吊销黑名单。"""
    settings = get_settings()
    return AuthService(
        session,
        secret=settings.jwt_secret,
        token_ttl_seconds=settings.jwt_expire_minutes * 60,
        cache=get_container().cache,
        blacklist_prefix=settings.token_blacklist_prefix,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    auth: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    """解析 ``Authorization: Bearer <token>`` 并返回当前用户。

    :raise UnauthorizedError: 没带令牌、令牌非法或已过期（对应 8.1 的 401）。
    """
    # 第 1 步：取令牌。缺失或不是 Bearer 一律按未登录处理
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError()
    # 第 2 步：校验签名与有效期，失败统一转成 401
    try:
        payload = auth.decode_token(credentials.credentials)
    except TokenInvalid as exc:
        raise UnauthorizedError(str(exc)) from exc
    # 第 3 步：吊销黑名单检查（登出后即使未过期也立即失效）
    try:
        if auth.is_token_blacklisted(credentials.credentials):
            raise UnauthorizedError("令牌已吊销，请重新登录")
    except UnauthorizedError:
        raise
    except Exception:  # noqa: BLE001 - 黑名单查不到不应阻断正常请求
        pass
    # 第 3 步：从载荷还原用户身份
    return CurrentUser(
        user_id=int(payload.get("user_id")),
        username=payload.get("username") or "",
        department_id=payload.get("department_id"),
        role_ids=list(payload.get("role_ids") or []),
    )


def require_permission(permission_code: str) -> Callable:
    """生成一个"要求指定操作权限码"的依赖。

    :param permission_code: 5.1 中由路由声明的权限码，例如 ``menu:org``。
    :raise PermissionDeniedError: 已登录但角色不含该权限码（对应 8.1 的 403）。
    """

    def _dependency(
        user: CurrentUser = Depends(get_current_user),
        auth: AuthService = Depends(get_auth_service),
    ) -> CurrentUser:
        # 第 1 步：按用户角色现查 role_permissions，多角色取并集
        if not auth.has_operation_permission(user.role_ids, permission_code):
            raise PermissionDeniedError(f"缺少操作权限：{permission_code}")
        # 第 2 步：通过则把用户继续往下传，省掉路由里再取一次
        return user

    return _dependency


def get_permission_engine(
    session: Session = Depends(get_session),
) -> DataPermissionEngine:
    """构造数据权限引擎依赖，边界规则取自配置（第 14 章 #9 / #10）。

    集中在这里而不是让每个路由自己 new，是为了保证**同一份配置只被解释一次** ——
    部门继承与管理员绕过这两个开关若在两处各读一遍，迟早出现
    "对外鉴权接口继承了、AI 链路没继承"这种两边判定不一致的问题。
    """
    settings = get_settings()
    return DataPermissionEngine(
        session,
        inherit_departments=settings.dept_permission_inherit,
        admin_role_codes=settings.admin_role_codes,
    )
