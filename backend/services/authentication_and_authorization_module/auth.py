"""认证鉴权模块（2.9.6 / 文档 5.1）。

职责与实现落点：

    用户认证            -> :meth:`AuthService.authenticate`
    JWT 令牌签发与校验   -> :meth:`AuthService.issue_token` / :meth:`AuthService.decode_token`
    RBAC 操作权限拦截    -> :meth:`AuthService.load_permissions` /
                          :meth:`AuthService.has_operation_permission`
    login 接口产出物     -> :meth:`AuthService.login`（8.2 响应结构）
    密码重置 / 口令哈希   -> :meth:`AuthService.reset_password` /
                          :func:`hash_password` / :func:`verify_password`

**口令哈希用标准库**：需求只写"不可逆哈希比对"、未指定算法，故用
``hashlib.pbkdf2_hmac``（NIST 推荐、零第三方依赖），存储格式为
``pbkdf2_sha256$迭代次数$盐$哈希``。**JWT 用第三方库**：令牌签名属安全敏感代码，
手写 HMAC 拼装容易埋下难查的漏洞，故用主流的 PyJWT（已写入 pyproject.toml）。

**刻意不做的事**：2.9.8 没有注册、自主改密、登出接口，故不实现 ——
改动口令的唯一入口是管理员发起的"重置密码"（2.9.3 用户管理）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Department, RolePermission, User, UserRole
from services.authentication_and_authorization_module.passwords import (
    INITIAL_PASSWORD,
    hash_password,
    verify_password,
)

__all__ = [
    "InvalidCredentials",
    "UserDisabled",
    "TokenInvalid",
    "INITIAL_PASSWORD",
    "hash_password",
    "verify_password",
    "AuthService",
]

# ---- JWT 参数 ----
TOKEN_ALGORITHM = "HS256"
DEFAULT_TOKEN_TTL_SECONDS = 12 * 3600


class InvalidCredentials(ValueError):
    """用户名或口令不正确。接口层据此返回 401。"""


class UserDisabled(ValueError):
    """账号已停用（``users.status = 0``）。

    与 :class:`InvalidCredentials` 分开，是为了让接口层能给出不同提示；
    若安全要求不需要区分，接口层统一按 401 处理即可。
    """


class TokenInvalid(ValueError):
    """令牌无效或已过期。接口层据此返回 401。"""


class AuthService:
    """认证鉴权服务。

    依赖注入：会话由外部传入，服务内部不自建连接。
    事务边界：``reset_password`` 自行 commit，其余方法均为只读。
    """

    def __init__(
        self,
        session: Session,
        *,
        secret: str,
        token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
        algorithm: str = TOKEN_ALGORITHM,
        cache: object | None = None,
        blacklist_prefix: str = "kb:token:blacklist:",
    ) -> None:
        """:param session: SQLAlchemy 会话。
        :param secret: JWT 签名密钥，由配置提供，禁止硬编码在代码里。
        :param token_ttl_seconds: 令牌有效期，默认 12 小时。
        :param algorithm: JWT 签名算法，默认 HS256。
        :param cache: 可选缓存客户端（提供 get/set/delete），用于令牌黑名单（登出吊销）。
        :param blacklist_prefix: 黑名单键前缀。
        """
        self._session = session
        self._secret = secret
        self._ttl = token_ttl_seconds
        self._algorithm = algorithm
        self._cache = cache
        self._blacklist_prefix = blacklist_prefix

    # ------------------------------------------------------------------ 认证

    def authenticate(self, username: str, password: str) -> User:
        """校验用户名与口令，并确认账号处于启用态。

        :raise InvalidCredentials: 用户不存在或口令不匹配。
        :raise UserDisabled: 账号已停用。
        """
        # 第 1 步：按用户名取用户；查不到与口令错误统一抛同一种异常，
        # 避免通过报错差异枚举出系统中存在哪些用户名
        user = self._load_user(username)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentials("用户名或口令不正确")
        # 第 2 步：校验启用态（users.status = 1）
        if user.status != 1:
            raise UserDisabled("账号已停用")
        return user

    def login(self, username: str, password: str) -> dict:
        """``POST /api/auth/login`` 的服务侧实现。

        返回结构对齐 8.2：``access_token``、``user_info``、``permissions``。
        """
        # 第 1 步：认证
        user = self.authenticate(username, password)
        # 第 2 步：取角色，令牌载荷与权限查询都要用
        role_ids = self._load_role_ids(user.id)
        # 第 3 步：签发令牌
        token = self.issue_token(user, role_ids=role_ids)
        # 第 4 步：汇总操作权限码，供前端渲染动态菜单与按钮级控制（2.9.5）
        permissions = self.load_permissions(role_ids)
        # 第 5 步：组装用户信息（含部门名称，用于个人中心展示）
        return {
            "access_token": token,
            "user_info": {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "department_id": user.department_id,
                "department_name": self._load_department_name(user.department_id),
                "role_ids": role_ids,
            },
            "permissions": permissions,
        }

    # ------------------------------------------------------------------ 令牌

    def issue_token(self, user: User, role_ids: list[int] | None = None) -> str:
        """签发 JWT。

        载荷字段严格按 5.1：``user_id``、``username``、``department_id``、
        ``role_ids``、``exp``。不额外塞入权限码 —— 权限会变，令牌不该缓存权限，
        校验时按 ``role_ids`` 现查 ``role_permissions``。
        """
        # 第 1 步：角色列表未传时按用户现查
        if role_ids is None:
            role_ids = self._load_role_ids(user.id)
        # 第 2 步：组装载荷，含过期时间
        now = datetime.now(timezone.utc)
        payload = {
            "user_id": user.id,
            "username": user.username,
            "department_id": user.department_id,
            "role_ids": list(role_ids),
            "jti": uuid.uuid4().hex,  # 唯一标识，登出吊销黑名单以此为准
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self._ttl)).timestamp()),
        }
        # 第 3 步：签名
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def decode_token(self, token: str) -> dict:
        """校验并解出 JWT 载荷。

        :raise TokenInvalid: 签名不合法、已过期或格式错误。
        """
        # 第 1 步：校验签名与 exp（PyJWT 会一并校验过期时间）
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.PyJWTError as exc:
            # 统一收敛成本模块的异常类型，接口层只需认识一种
            raise TokenInvalid(f"令牌无效：{exc}") from exc
        # 第 2 步：兜住载荷缺关键字段的情况（例如密钥换了但令牌格式没变）
        if "user_id" not in payload:
            raise TokenInvalid("令牌缺少 user_id")
        return payload

    # ------------------------------------------------------------------ 吊销

    def is_token_blacklisted(self, token: str) -> bool:
        """令牌是否已被登出吊销（黑名单）。

        缓存不可用时一律视为"未吊销"，避免把正常用户挡在门外。
        """
        if self._cache is None:
            return False
        try:
            payload = self.decode_token(token)
        except TokenInvalid:
            return True  # 非法令牌本身就是无效的
        jti = payload.get("jti")
        if not jti:
            return False
        try:
            return bool(self._cache.get(f"{self._blacklist_prefix}{jti}"))
        except Exception:  # noqa: BLE001 - 查黑名单失败不应阻断请求
            return False

    def blacklist_token(self, token: str) -> None:
        """把令牌加入吊销黑名单，TTL 设为其剩余有效期（过期后自动失效）。

        :raise TokenInvalid: 令牌本身非法（前端传了坏令牌）。
        """
        if self._cache is None:
            raise RuntimeError("未配置缓存，无法吊销令牌（请启用 Redis）")
        payload = self.decode_token(token)
        jti = payload.get("jti")
        if not jti:
            return
        remaining = int(payload.get("exp", 0)) - int(datetime.now(timezone.utc).timestamp())
        remaining = max(remaining, 1)
        try:
            self._cache.set(f"{self._blacklist_prefix}{jti}", "1", ttl=remaining)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"写入令牌黑名单失败：{exc}") from exc

    # ------------------------------------------------------------------ 权限

    def load_permissions(self, role_ids: list[int]) -> list[str]:
        """汇总给定角色持有的全部权限码（去重后排序）。

        多角色取并集（OR 逻辑），与 6.1 的操作权限判定一致。
        """
        # 第 1 步：无角色直接返回空列表，省掉一次查询
        if not role_ids:
            return []
        # 第 2 步：一次 IN 查询取回全部权限码
        rows = self._session.execute(
            select(RolePermission.permission_code).where(
                RolePermission.role_id.in_(role_ids)
            )
        ).scalars()
        # 第 3 步：去重并排序，保证同一用户每次返回顺序稳定（便于前端 diff）
        return sorted({code for code in rows if code})

    def has_operation_permission(
        self, role_ids: list[int], permission_code: str
    ) -> bool:
        """判断给定角色集合是否持有某个权限码。

        接口层以 FastAPI 依赖形式在路由上声明 ``permission_code``，
        调用本方法完成 RBAC 拦截（5.1）。
        """
        # 第 1 步：无权限码或角色，直接拒绝
        if not permission_code or not role_ids:
            return False
        # 第 2 步：在角色权限表内查该权限码，命中任意一条即通过
        found = self._session.execute(
            select(RolePermission.id)
            .where(
                RolePermission.role_id.in_(role_ids),
                RolePermission.permission_code == permission_code,
            )
            .limit(1)
        ).scalar_one_or_none()
        return found is not None

    # ------------------------------------------------------------------ 重置

    def reset_password(self, user_id: int, new_password: str | None = None) -> str:
        """管理员重置用户口令（2.9.3 用户管理的"重置密码"）。

        :param user_id: 目标用户。
        :param new_password: 新口令；不传则重置为 :data:`INITIAL_PASSWORD`。
        :return: 实际写入的新口令**明文** —— 只在这一次返回给管理员，
            库里存的是哈希；不返回的话管理员无从得知该用什么口令登录。
        :raise LookupError: 用户不存在。
        :raise ValueError: 显式传入的新口令为空串（会把账号锁死）。
        """
        # 第 1 步：取用户，不存在直接抛错
        user = self._session.get(User, user_id)
        if user is None:
            raise LookupError(f"用户不存在：id={user_id}")
        # 第 2 步：确定新口令。显式传空串属于误用，直接拒绝 ——
        # 空口令哈希出来照样能存，但谁都登不上，是纯粹的坑
        if new_password is not None and not new_password.strip():
            raise ValueError("新口令不能为空")
        effective = new_password or INITIAL_PASSWORD
        # 第 3 步：写入新口令哈希（明文不入库）
        user.password_hash = hash_password(effective)
        # 第 4 步：提交并回传明文，供接口一次性展示
        self._session.commit()
        return effective

    # ------------------------------------------------------------------ 内部

    def _load_user(self, username: str) -> User | None:
        """按用户名取用户，不存在返回 ``None``。"""
        return self._session.execute(
            select(User).where(User.username == username).limit(1)
        ).scalar_one_or_none()

    def _load_role_ids(self, user_id: int) -> list[int]:
        """取用户的全部角色 id（可能为空）。"""
        rows = self._session.execute(
            select(UserRole.role_id).where(UserRole.user_id == user_id)
        ).scalars()
        return [role_id for role_id in rows if role_id is not None]

    def _load_department_name(self, department_id: int | None) -> str | None:
        """取部门名称；用户未归属部门时返回 ``None``。"""
        if department_id is None:
            return None
        return self._session.execute(
            select(Department.name).where(Department.id == department_id)
        ).scalar_one_or_none()
