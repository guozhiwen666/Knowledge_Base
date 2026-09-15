"""认证接口（8.2）。

只实现 8.2 列出的 1 个接口。需求没有注册、自主改密、登出接口，因此不实现。

响应即 8.2 的三件产出物：``access_token``、``user_info``、``permissions``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response import PermissionDeniedError, UnauthorizedError, ok
from core.security import get_auth_service
from schemas.requests import LoginRequest
from services.authentication_and_authorization_module.auth import (
    AuthService,
    InvalidCredentials,
    UserDisabled,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/login", summary="用户名口令登录")
def login(payload: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> dict:
    """校验口令并签发 JWT。

    服务层抛的是领域异常，这里翻译成 8.1 规定的 HTTP 语义：
    口令错误 → 401，账号停用 → 403。
    """
    # 第 1 步：认证 + 签发令牌 + 汇总权限码（全在服务层完成）
    try:
        data = auth.login(payload.username, payload.password)
    except InvalidCredentials as exc:
        raise UnauthorizedError(str(exc)) from exc
    except UserDisabled as exc:
        raise PermissionDeniedError(str(exc)) from exc
    # 第 2 步：按 8.1 的统一格式返回
    return ok(data)
