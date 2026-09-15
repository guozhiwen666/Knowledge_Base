"""组织架构接口（8.3）。

实现 8.3 列出的 5 个接口：

    GET  /api/org/departments              部门树形列表
    POST /api/org/users                    用户新增
    PUT  /api/org/users/{id}               用户编辑（含启停用）
    GET  /api/org/roles                    角色列表（含已分配权限）
    POST /api/org/roles/{id}/permissions   角色权限分配

**权限保护上的一个取舍**：两个只读接口（部门树、角色列表）只要求登录，
不要求 ``menu:org``。原因是 2.9.5 的「数据权限配置组件」需要勾选部门与角色，
而使用它的知识管理员按 2.9.2 并不拥有组织架构页的菜单权限；
若给只读接口也挂 ``menu:org``，知识管理员配权限时就会拉不到部门与角色列表。
写接口（用户增改、角色授权）仍然严格要求 ``menu:org``。

**未实现的接口**：8.4 没有部门增删改、用户删除、用户列表接口（第 14 章【待确认】），
因此这里不提供 —— 没有对外契约就不擅自造端点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from core.db import get_session
from core.response import ok
from core.security import CurrentUser, get_current_user, require_permission
from schemas.requests import (
    RolePermissionsRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from services.organization_structure_service.org import OrgService

__all__ = ["router"]

router = APIRouter(prefix="/api/org", tags=["组织架构"])


def _service(session: Session = Depends(get_session)) -> OrgService:
    """组织架构服务依赖。"""
    return OrgService(session)


@router.get("/departments", summary="部门树形列表")
def list_departments(
    _: CurrentUser = Depends(get_current_user),
    service: OrgService = Depends(_service),
) -> dict:
    """返回嵌套的部门树（节点含 ``children``）。"""
    return ok(service.list_departments_tree())


@router.post("/users", summary="用户新增")
def create_user(
    payload: UserCreateRequest,
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """新增用户。口令在服务层哈希后入库，明文不落库。"""
    # 第 1 步：写库（登录名重复会抛 UsernameTaken，由全局异常处理翻成 422）
    user = service.create_user(
        payload.username,
        payload.display_name,
        payload.password,
        department_id=payload.department_id,
        role_ids=payload.role_ids,
        status=payload.status,
    )
    # 第 2 步：返回关键字段（不回传 password_hash）
    return ok(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "department_id": user.department_id,
            "status": user.status,
        }
    )


@router.put("/users/{id}", summary="用户编辑（含启停用）")
def update_user(
    payload: UserUpdateRequest,
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """编辑用户。

    ``model_dump(exclude_unset=True)`` 拿到"调用方真正传了的字段"，
    只把传了的字段交给服务层 —— 这样"只改 status"不会顺手清掉角色关联。
    """
    # 第 1 步：取出显式传入的字段名
    provided = payload.model_dump(exclude_unset=True)

    # 第 2 步：逐字段构造参数。没传的字段直接不出现在 kwargs 里，
    # 服务层用哨兵默认值判定为"不改"
    kwargs: dict = {}
    for field in ("display_name", "department_id", "role_ids", "status"):
        if field in provided:
            kwargs[field] = getattr(payload, field)

    # 第 3 步：写库
    user = service.update_user(id, **kwargs)
    return ok(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "department_id": user.department_id,
            "status": user.status,
        }
    )


@router.get("/roles", summary="角色列表（含已分配权限）")
def list_roles(
    _: CurrentUser = Depends(get_current_user),
    service: OrgService = Depends(_service),
) -> dict:
    """返回角色及其权限码列表，供角色管理页与权限弹窗使用。"""
    return ok(service.list_roles())


@router.post("/roles/{id}/permissions", summary="角色权限分配")
def set_role_permissions(
    payload: RolePermissionsRequest,
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """全量覆盖式保存角色权限（8.3）。

    :return: ``{"saved": n}``，n 为去重后实际写入的权限条数。
    """
    saved = service.set_role_permissions(
        id, [item.model_dump() for item in payload.permissions]
    )
    return ok({"saved": saved})
