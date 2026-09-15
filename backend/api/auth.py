"""认证接口（8.2 + 上线安全加固）。

8.2 原本只列了 1 个登录接口。为满足上线安全要求（P1-6 / P1-7），
这里额外补充了**登录限流**与**登出吊销**两项基础设施能力：

* ``POST /api/auth/login``   —— 原 8.2 接口，增加失败计数 + 滑动窗口限流
* ``POST /api/auth/logout``  —— 新增：把当前令牌 jti 写入黑名单，立即失效

需求未列登出/限流，但属于生产必须的安全能力；接口纪律之外的安全加固不计入 8.8 的 41 个业务路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import get_settings
from core.container import get_container
from core.response import (
    AppError,
    PermissionDeniedError,
    TooManyRequestsError,
    UnauthorizedError,
    ok,
)
from core.security import get_auth_service
from schemas.requests import LoginRequest
from services.authentication_and_authorization_module.auth import (
    AuthService,
    InvalidCredentials,
    TokenInvalid,
    UserDisabled,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/auth", tags=["认证"])

# 与 core/security.py 同款 Bearer 解析器（auto_error=False，缺令牌交给业务判断）
_bearer = HTTPBearer(auto_error=False)

# 限流键前缀
_FAIL_PREFIX_USER = "kb:login:fail:"
_FAIL_PREFIX_IP = "kb:login:fail:ip:"


@router.post("/login", summary="用户名口令登录（带限流）")
def login(
    payload: LoginRequest,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """校验口令并签发 JWT；连续失败触发限流（429）。

    服务层抛的是领域异常，这里翻译成 8.1 规定的 HTTP 语义：
    口令错误 → 401，账号停用 → 403，触发限流 → 429。
    """
    settings = get_settings()
    cache = get_container().cache
    ip = request.client.host if request.client else "unknown"
    user_key = f"{_FAIL_PREFIX_USER}{payload.username}"
    ip_key = f"{_FAIL_PREFIX_IP}{ip}"

    # 第 0 步：限流前置检查（缓存不可用时跳过，不阻断登录）
    try:
        user_fail = int(cache.get(user_key) or 0)
        ip_fail = int(cache.get(ip_key) or 0)
        ip_limit = settings.login_max_attempts * 3
        if user_fail >= settings.login_max_attempts or ip_fail >= ip_limit:
            raise TooManyRequestsError("登录失败次数过多，请稍后再试")
    except TooManyRequestsError:
        raise
    except Exception:  # noqa: BLE001 - 限流探活失败不应阻断登录
        pass

    # 第 1 步：认证 + 签发令牌 + 汇总权限码（全在服务层完成）
    try:
        data = auth.login(payload.username, payload.password)
    except InvalidCredentials as exc:
        _record_login_failure(cache, settings, user_key, ip_key)
        raise UnauthorizedError(str(exc)) from exc
    except UserDisabled as exc:
        _record_login_failure(cache, settings, user_key, ip_key)
        raise PermissionDeniedError(str(exc)) from exc

    # 第 2 步：成功则清零失败计数
    try:
        cache.delete(user_key)
        cache.delete(ip_key)
    except Exception:  # noqa: BLE001
        pass

    return ok(data)


@router.post("/logout", summary="登出（吊销当前令牌）")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """把当前令牌加入黑名单，立即失效（TTL=令牌剩余有效期）。

    未带令牌或令牌已失效都按"无需处理"返回成功，幂等。
    """
    if credentials is None or not credentials.credentials:
        return ok(message="无需登出")
    try:
        auth.blacklist_token(credentials.credentials)
    except TokenInvalid:
        return ok(message="令牌已失效")
    except RuntimeError as exc:
        raise AppError(str(exc), code=500, http_status=500) from exc
    return ok(message="已登出")


def _record_login_failure(
    cache, settings, user_key: str, ip_key: str
) -> None:
    """失败计数 +1，并设滑动窗口 TTL（缓存不可用时静默跳过）。"""
    try:
        cache.incrby(user_key, 1, ttl=settings.login_lock_window_seconds)
        cache.incrby(ip_key, 1, ttl=settings.login_lock_window_seconds)
    except Exception:  # noqa: BLE001 - 限流记录失败不应影响登录响应
        pass
