"""认证鉴权模块 · 口令哈希（2.9.6 / 文档 5.1 的"不可逆哈希比对"）。

单独成文件的两个理由：

1. **依赖隔离**：组织架构服务新建用户时只需要"把口令哈希一下"，
   不该为了这个动作连带把 JWT 依赖（PyJWT）拖进来；
2. **职责单一**：口令哈希的存储格式与强度参数是本模块唯一的关注点，
   与令牌签发、权限判定放在一起会让 ``auth`` 越滚越大。

哈希算法需求未指定，采用标准库 ``hashlib.pbkdf2_hmac``（NIST 推荐、零第三方依赖），
存储格式 ``pbkdf2_sha256$迭代次数$盐$哈希``：盐与迭代次数随哈希一起存，
将来调整强度时旧口令仍可正常校验。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

__all__ = [
    "PBKDF2_ALGORITHM",
    "PBKDF2_ITERATIONS",
    "INITIAL_PASSWORD",
    "hash_password",
    "verify_password",
]

# 口令哈希参数。需求未指定算法与强度，参数集中在此便于统一调整。
PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 240_000
SALT_BYTES = 16

# 管理员重置口令后的初始口令。需求未规定取值（2.9.3 只写"重置密码"），
# 因此集中在这一处，并提示部署时必须替换。
INITIAL_PASSWORD = "Kb@123456"


def hash_password(raw_password: str) -> str:
    """把明文口令哈希成可入库的字符串。

    生成随机盐后用 PBKDF2-HMAC-SHA256 迭代派生，返回
    ``pbkdf2_sha256$迭代次数$盐(hex)$哈希(hex)`` 四段式字符串。

    :param raw_password: 明文口令。
    :return: 可写入 ``users.password_hash`` 的字符串。
    """
    # 第 1 步：生成密码学安全的随机盐
    salt = secrets.token_bytes(SALT_BYTES)
    # 第 2 步：派生密钥
    derived = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM, raw_password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    # 第 3 步：拼成自描述字符串，便于校验时取回参数
    return f"pbkdf2_{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(raw_password: str, password_hash: str) -> bool:
    """校验明文口令与库中哈希是否匹配。

    用 ``hmac.compare_digest`` 做常量时间比较，避免通过响应时间差异逐字节猜哈希。

    :return: 匹配返回 ``True``；格式非法或算法不支持返回 ``False``
        （不抛异常，避免把库内数据问题暴露成 500）。
    """
    # 第 1 步：拆出自描述参数
    try:
        algorithm_part, iterations_part, salt_hex, hash_hex = password_hash.split("$")
        algorithm = algorithm_part.removeprefix("pbkdf2_")
        expected = bytes.fromhex(hash_hex)
        salt = bytes.fromhex(salt_hex)
        iterations = int(iterations_part)
    except (ValueError, AttributeError):
        return False
    # 第 2 步：用库中记录的盐与迭代次数重新派生，再做常量时间比较
    derived = hashlib.pbkdf2_hmac(
        algorithm, raw_password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(derived, expected)
