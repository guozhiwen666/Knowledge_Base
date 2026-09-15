"""请求体模型（Pydantic）。

只定义**请求**模型：响应统一走 ``core.response.ok()`` 的 ``{code, message, data}``
包装，字段名直接取自服务层返回值，再定义一套 Response 模型只会多一层映射与漂移风险。

**关于"未传即不改"**：用户编辑与知识单元编辑都是部分更新，用
``model_dump(exclude_unset=True)`` 拿到"调用方真正传了的字段"，
再逐字段转成服务层的哨兵参数 —— 这样"没传"与"显式传 null"能区分开
（例如只改 ``status`` 就不会把 ``department_id`` 冲掉）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "LoginRequest",
    "UserCreateRequest",
    "UserUpdateRequest",
    "RolePermissionItem",
    "RolePermissionsRequest",
    "UnitPermissionItem",
    "UnitPermissionsRequest",
    "UnitDeleteRequest",
    "UnitUpdateRequest",
    "CheckPermissionsRequest",
    "ChatRequest",
    "FaqReviewRequest",
]


class LoginRequest(BaseModel):
    """``POST /api/auth/login``（8.2：请求字段 username、password）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserCreateRequest(BaseModel):
    """``POST /api/org/users``（8.3 的请求字段）。"""

    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    department_id: int | None = None
    role_ids: list[int] = Field(default_factory=list)
    status: int = 1


class UserUpdateRequest(BaseModel):
    """``PUT /api/org/users/{id}``。

    四个字段全部可选；只传 ``status`` 就是启停用，只传 ``role_ids`` 就是重排角色。
    """

    display_name: str | None = None
    department_id: int | None = None
    role_ids: list[int] | None = None
    status: int | None = None


class RolePermissionItem(BaseModel):
    """角色权限项（``permission_type`` 取 menu / operation / ai）。"""

    permission_code: str = Field(min_length=1, max_length=128)
    permission_type: str = Field(min_length=1, max_length=32)


class RolePermissionsRequest(BaseModel):
    """``POST /api/org/roles/{id}/permissions``：全量覆盖式保存。"""

    permissions: list[RolePermissionItem] = Field(default_factory=list)


class UnitPermissionItem(BaseModel):
    """数据权限实体项。``target_type`` 取 global / department / role / user。"""

    target_type: str = Field(min_length=1, max_length=16)
    target_id: int = 0


class UnitPermissionsRequest(BaseModel):
    """``POST /api/knowledge/units/{id}/permissions``：全量覆盖式保存。"""

    permissions: list[UnitPermissionItem] = Field(default_factory=list)


class UnitDeleteRequest(BaseModel):
    """``DELETE /api/knowledge/units``（8.4：请求字段 unit_ids）。"""

    unit_ids: list[int] = Field(default_factory=list)


class UnitUpdateRequest(BaseModel):
    """``PUT /api/knowledge/units/{id}``。

    **不含 tags / attachments**：8.4 列了这两个字段，但 2.9.7 的 ``knowledge_units``
    表没有对应列（第 14 章【待确认】）。没有存储位置就不接收入参，
    免得接口"答应了"却存不下来。
    """

    title: str | None = None
    content: str | None = None
    category: str | None = None
    summary: str | None = None


class CheckPermissionsRequest(BaseModel):
    """``POST /api/knowledge/check-permissions``（8.4 的请求字段）。"""

    user_id: int
    unit_ids: list[int] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """``POST /api/ai/chat/stream``（8.5 的请求字段）。"""

    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)


class FaqReviewRequest(BaseModel):
    """``POST /api/settlement/faqs/{id}/review``（8.7 的请求字段）。"""

    action: str = Field(min_length=1, max_length=16)
    edited_answer: str | None = None
