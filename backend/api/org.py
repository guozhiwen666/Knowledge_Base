"""组织架构接口（8.3 + 第 14 章 #5 补齐）。

实现 8.3 列出的 5 个接口：

    GET    /api/org/departments              部门树形列表
    POST   /api/org/users                    用户新增
    PUT    /api/org/users/{id}               用户编辑（含启停用）
    GET    /api/org/roles                    角色列表（含已分配权限）
    POST   /api/org/roles/{id}/permissions   角色权限分配

并补齐 2.9.3 要求但 8.3 未列出的 11 个接口（第 14 章 #5 确认为"补充新建接口"）：

    GET    /api/org/users                        用户列表（分页 + 筛选）
    GET    /api/org/users/{id}                   用户详情
    DELETE /api/org/users/{id}                   用户删除
    POST   /api/org/users/{id}/reset-password    重置口令
    POST   /api/org/departments                  部门新增
    PUT    /api/org/departments/{id}             部门编辑
    DELETE /api/org/departments/{id}             部门删除
    GET    /api/org/departments/{id}/members     部门成员列表
    POST   /api/org/roles                        角色新增
    PUT    /api/org/roles/{id}                   角色编辑
    DELETE /api/org/roles/{id}                   角色删除

**重置口令为什么独立成接口**：8.3 原文写"重置密码走 PUT 的密码重置语义"，
但 8.3 列的 PUT 请求字段里**并没有 password**。语义含混又缺字段，
按原样实现只能靠约定俗成；因此拆成独立路由，请求体与响应都明确 ——
响应回一次新口令明文（库里只有哈希），管理员必须拿到它才用得上。

**权限保护上的一个取舍**：三个只读接口（部门树、部门成员、角色列表）只要求登录，
不要求 ``menu:org``。原因是 2.9.5 的「数据权限配置组件」需要勾选部门与角色，
而使用它的知识管理员按 2.9.2 并不拥有组织架构页的菜单权限；
若给只读接口也挂 ``menu:org``，知识管理员配权限时就会拉不到部门与角色列表。
写接口一律严格要求 ``menu:org``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from core.db import get_session
from core.response import ok
from core.security import CurrentUser, get_current_user, require_permission
from schemas.requests import (
    DepartmentCreateRequest,
    DepartmentUpdateRequest,
    RoleCreateRequest,
    RolePermissionsRequest,
    RoleUpdateRequest,
    UserCreateRequest,
    UserResetPasswordRequest,
    UserUpdateRequest,
)
from services.organization_structure_service.org import OrgService

__all__ = ["router"]

router = APIRouter(prefix="/api/org", tags=["组织架构"])


def _service(session: Session = Depends(get_session)) -> OrgService:
    """组织架构服务依赖。"""
    return OrgService(session)


# ---------------------------------------------------------------------- 部门


@router.get("/departments", summary="部门树形列表")
def list_departments(
    _: CurrentUser = Depends(get_current_user),
    service: OrgService = Depends(_service),
) -> dict:
    """返回嵌套的部门树（节点含 ``children``）。"""
    return ok(service.list_departments_tree())


@router.post("/departments", summary="部门新增")
def create_department(
    payload: DepartmentCreateRequest,
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """新增部门。父部门不存在会抛错（422），不会建出一个挂不上树的节点。"""
    department = service.create_department(
        payload.name,
        parent_id=payload.parent_id,
        leader_id=payload.leader_id,
        sort_order=payload.sort_order,
    )
    return ok(_department_row(department))


@router.put("/departments/{id}", summary="部门编辑")
def update_department(
    payload: DepartmentUpdateRequest,
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """编辑部门。未传的字段不改；把部门挂到自己或自己的下级会被拒绝。"""
    # 只把显式传了的字段交给服务层（未传即不改）
    provided = payload.model_dump(exclude_unset=True)
    department = service.update_department(id, **provided)
    return ok(_department_row(department))


@router.delete("/departments/{id}", summary="部门删除")
def delete_department(
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """删除部门。**不做级联删除** —— 有子部门或成员时返回 422。
    级联会把成员静默变成"无部门"，那种数据变更比一个明确的报错难处理得多。
    """
    service.delete_department(id)
    return ok({"deleted": 1})


@router.get("/departments/{id}/members", summary="部门成员列表")
def list_department_members(
    id: int = Path(ge=1),
    _: CurrentUser = Depends(get_current_user),
    service: OrgService = Depends(_service),
) -> dict:
    """按部门反查成员（5.2：成员关系由 ``users.department_id`` 反推）。"""
    members = service.list_department_members(id)
    return ok(
        {
            "department_id": id,
            "total": len(members),
            "items": [
                {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "status": user.status,
                }
                for user in members
            ],
        }
    )


# ---------------------------------------------------------------------- 用户


@router.get("/users", summary="用户列表（分页 + 筛选）")
def list_users(
    keyword: str | None = Query(default=None, description="登录名或显示名模糊匹配"),
    department_id: int | None = Query(default=None),
    status: int | None = Query(default=None, description="1 启用 / 0 停用"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """分页查询用户，每项带部门名与角色（供列表直接渲染，不必前端再拼）。"""
    total, items = service.list_users(
        keyword=keyword,
        department_id=department_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return ok({"total": total, "items": items, "page": page, "page_size": page_size})


@router.get("/users/{id}", summary="用户详情")
def get_user(
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """用户详情，结构同列表项（含 ``role_ids``，供编辑表单回填）。"""
    return ok(service.get_user_detail(id))


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
    return ok(_user_row(user))


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
    provided = payload.model_dump(exclude_unset=True)
    user = service.update_user(id, **provided)
    return ok(_user_row(user))


@router.delete("/users/{id}", summary="用户删除")
def delete_user(
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """删除用户并清掉其角色关联。

    ``unit_permissions`` 里指向该用户的授权**不删** —— 它表达的是
    "这份知识授权给这个岗位的人"，静默删掉会让接替者莫名其妙看不到东西。
    """
    service.delete_user(id)
    return ok({"deleted": 1})


@router.post("/users/{id}/reset-password", summary="重置用户口令")
def reset_password(
    payload: UserResetPasswordRequest,
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """重置口令。

    :return: ``{"user_id": ..., "new_password": ...}`` —— 明文只在这一处出现一次，
        库里存的是 PBKDF2 哈希。前端应展示后提示管理员尽快转达并修改。
    """
    new_password = service.reset_password(id, payload.new_password)
    return ok({"user_id": id, "new_password": new_password})


# ---------------------------------------------------------------------- 角色


@router.get("/roles", summary="角色列表（含已分配权限）")
def list_roles(
    _: CurrentUser = Depends(get_current_user),
    service: OrgService = Depends(_service),
) -> dict:
    """返回角色及其权限码列表，供角色管理页与权限弹窗使用。"""
    return ok(service.list_roles())


@router.post("/roles", summary="角色新增")
def create_role(
    payload: RoleCreateRequest,
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """新增角色。编码重复会抛错（422）。"""
    role = service.create_role(
        payload.role_name, payload.role_code, description=payload.description
    )
    return ok(_role_row(role))


@router.put("/roles/{id}", summary="角色编辑")
def update_role(
    payload: RoleUpdateRequest,
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """编辑角色。``role_code`` 被管理员绕过名单引用，改动前请确认配置同步。"""
    provided = payload.model_dump(exclude_unset=True)
    role = service.update_role(id, **provided)
    return ok(_role_row(role))


@router.delete("/roles/{id}", summary="角色删除")
def delete_role(
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:org")),
    service: OrgService = Depends(_service),
) -> dict:
    """删除角色并清掉其权限分配。仍有用户使用该角色时返回 422。"""
    service.delete_role(id)
    return ok({"deleted": 1})


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


# ---------------------------------------------------------------------- 内部


def _user_row(user) -> dict:
    """用户行的响应结构（不包含 ``password_hash``）。"""
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "department_id": user.department_id,
        "status": user.status,
    }


def _department_row(department) -> dict:
    """部门行的响应结构，字段与部门树节点保持一致。"""
    return {
        "id": department.id,
        "parent_id": department.parent_id,
        "name": department.name,
        "leader_id": department.leader_id,
        "sort_order": department.sort_order,
    }


def _role_row(role) -> dict:
    """角色行的响应结构（不含权限列表，权限走 ``GET /api/org/roles``）。"""
    return {
        "id": role.id,
        "role_name": role.role_name,
        "role_code": role.role_code,
        "description": role.description,
    }
