"""统一响应包装与异常处理（对应 8.1 通用约定）。

约定（照抄 8.1）：

* 响应体 ``{"code": 0, "message": "ok", "data": {...}}``
* 错误码：401 未登录/令牌失效、403 无操作权限、404 资源不存在、422 参数错误、500 服务异常

**设计取舍**：业务异常统一继承 :class:`AppError` 并自带 HTTP 状态码，
服务层只管抛自己的领域异常（``KnowledgeUnitNotFound`` / ``LookupError`` 等），
由这里的一层映射翻译成 HTTP —— 服务层不 import FastAPI，保持可单测。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

__all__ = [
    "AppError",
    "UnauthorizedError",
    "PermissionDeniedError",
    "NotFoundError",
    "BadRequestError",
    "ok",
    "fail",
    "register_exception_handlers",
]


class AppError(Exception):
    """业务异常基类：自带业务码与 HTTP 状态码。"""

    def __init__(self, message: str, *, code: int = 1, http_status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


class UnauthorizedError(AppError):
    """未登录或令牌失效，对应 8.1 的 401。"""

    def __init__(self, message: str = "未登录或登录已失效") -> None:
        super().__init__(message, code=401, http_status=401)


class PermissionDeniedError(AppError):
    """已登录但无该操作权限，对应 8.1 的 403。"""

    def __init__(self, message: str = "无操作权限") -> None:
        super().__init__(message, code=403, http_status=403)


class NotFoundError(AppError):
    """资源不存在，对应 8.1 的 404。"""

    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(message, code=404, http_status=404)


class BadRequestError(AppError):
    """参数错误，对应 8.1 的 422。"""

    def __init__(self, message: str = "参数错误") -> None:
        super().__init__(message, code=422, http_status=422)


def ok(data: Any = None, message: str = "ok") -> dict:
    """成功响应。所有接口的成功返回都必须经过它，保证包装格式一致。"""
    return {"code": 0, "message": message, "data": data}


def fail(code: int, message: str) -> dict:
    """失败响应（用于需要返回 200 但业务失败的分支，一般走异常更清晰）。"""
    return {"code": code, "message": message, "data": None}


def register_exception_handlers(app: FastAPI) -> None:
    """把各类异常统一翻译成 8.1 的响应格式。"""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        # 业务异常：按异常自带的 HTTP 状态码返回，业务码同步带上
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI 的参数校验失败：按 8.1 归到 422，并把字段级错误带回去便于前端定位
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "参数校验失败",
                "data": {"errors": exc.errors()},
            },
        )

    @app.exception_handler(LookupError)
    async def _lookup_error(_: Request, exc: LookupError) -> JSONResponse:
        # 服务层用 LookupError 表示"资源不存在"（如 KnowledgeUnitNotFound），落到 404
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": str(exc) or "资源不存在", "data": None},
        )

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        # 服务层的入参类异常（标题为空、非法审核动作等）落到 422
        return JSONResponse(
            status_code=422,
            content={"code": 422, "message": str(exc) or "参数错误", "data": None},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # 兜底：未预期的异常统一 500，不外泄堆栈
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "message": f"服务异常：{type(exc).__name__}",
                "data": None,
            },
        )
