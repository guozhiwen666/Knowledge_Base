"""配置层：从 `.env` 读取全部运行参数。

对应《技术方案设计文档》2.2 技术选型与 2.3 部署拓扑中涉及的每个外部依赖，
所有连接信息集中在这一处，服务层与接口层都不许直接读环境变量 ——
这样换环境只改 `.env`，不用翻代码。

**为什么不用 pydantic-settings**：依赖里没有它，而本项目对配置的需求就是
"读字符串 + 给默认值 + 拼连接串"，用标准库 ``dataclass`` + ``python-dotenv``
已经够用，没必要为此再加一个依赖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

__all__ = ["Settings", "get_settings", "PROJECT_ROOT", "BACKEND_ROOT"]

# 项目根目录与后端根目录（后端以 backend/ 为导入根与工作目录）
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent


def _env(name: str, default: str = "") -> str:
    """取一个环境变量并去掉首尾空白（.env 里常带多余空格）。"""
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    """取整型环境变量；解析失败时退回默认值，避免一个手滑的配置让整个服务起不来。"""
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """取浮点型环境变量；解析失败时退回默认值。"""
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """全量运行配置。字段分组与 2.3 部署拓扑的组件一一对应。"""

    # ---- 应用 ----
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # ---- CORS（生产须锁定前端来源，逗号分隔；为空则回退 "*" 并告警）----
    cors_allow_origins: str = ""

    # ---- 登录限流（P1 安全加固）----
    # 同一用户名 / 同一 IP 在 lock_window 秒内的失败上限，超过返回 429
    login_max_attempts: int = 5
    login_lock_window_seconds: int = 300
    # 令牌黑名单前缀（登出时把 jti 写入缓存，TTL=令牌剩余有效期）
    token_blacklist_prefix: str = "kb:token:blacklist:"

    # ---- MySQL（2.9.7 的 10 张表）----
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "knowledge_base"
    db_charset: str = "utf8mb4"

    # ---- JWT（5.1 令牌签发）----
    jwt_secret: str = ""
    jwt_expire_minutes: int = 120

    # ---- Milvus（7.5 向量集合）----
    milvus_url: str = "http://127.0.0.1:19530"
    chunks_collection: str = "kb_unit_chunks"
    embedding_dim: int = 1024

    # ---- MinIO（5.3 原始文件）----
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "knowledge-base"
    minio_secure: bool = False

    # ---- Redis（FAQ 缓存 / 实时计数 / 会话）----
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # ---- LLM（5.5 流式回答生成）----
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen-flash"
    llm_temperature: float = 0.1

    # ---- Embedding（7.5 向量维度来源）----
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_batch_size: int = 10

    # ---- 本地存储兜底目录 ----
    storage_root: str = "storage"

    # ---- 业务阈值（第 14 章 #8 的确认值）----
    # FAQ 挖掘的推荐触发频次。低于该频次的问题模式不生成推荐项。
    faq_min_frequency: int = 50
    # 召回相似度阈值。低于该值的候选不计入 recalled_unit_ids_json，
    # 于是"召回列表为空"等价于"没有达到阈值的内容支撑"，即 11.4 的知识缺口。
    # 注意：#8 确认值是 0.8，但实测 text-embedding-v4 + COSINE 下正确命中的
    # 相似度只有 0.7383~0.7913（无关提问最高 0.4129），0.8 会把真实命中全部误杀。
    # 这里的默认值保留确认值，运行值由 `.env` 的 RECALL_SIMILARITY_THRESHOLD 覆盖。
    recall_similarity_threshold: float = 0.8

    # ---- 数据权限（第 14 章 #9 / #10 的确认值）----
    # 部门树上下级继承：为真时，用户所属部门的下级部门（含自身）授权均可命中。
    dept_permission_inherit: bool = True
    # 天然绕过数据权限校验的角色编码（第 14 章 #10 确认为"是"）。
    admin_role_codes: tuple[str, ...] = ("sys_admin", "kb_admin")

    # ---- 多轮会话（第 14 章 #12 的确认值：保留 10 轮）----
    conversation_context_rounds: int = 10

    # 组合字段（由上面几组拼出来，放在 dataclass 里做一次缓存）
    notes: tuple[str, ...] = field(default_factory=tuple)

    # ---------------------------------------------------------------- 派生值

    @property
    def database_url(self) -> str:
        """SQLAlchemy 连接串。固定用同步驱动 pymysql（服务层是同步实现）。"""
        from urllib.parse import quote_plus

        # 口令里可能含 @ : / 等字符，必须转义后再拼连接串
        password = quote_plus(self.db_password)
        return (
            f"mysql+pymysql://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset={self.db_charset}"
        )

    @property
    def database_url_without_schema(self) -> str:
        """不带库名的连接串，用于"建库"这一步（库还不存在时连不上带库名的串）。"""
        from urllib.parse import quote_plus

        password = quote_plus(self.db_password)
        return (
            f"mysql+pymysql://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/?charset={self.db_charset}"
        )

    @property
    def jwt_enabled(self) -> bool:
        """是否配置了 JWT 密钥。没配就让启动直接报错，而不是发不可校验的令牌。"""
        return bool(self.jwt_secret)

    @property
    def storage_path(self) -> Path:
        """本地存储目录的绝对路径（相对路径按项目根解析）。"""
        path = Path(self.storage_root)
        return path if path.is_absolute() else PROJECT_ROOT / path


def _load_settings() -> Settings:
    """真正读一次 .env 并构造配置对象。"""
    # 第 1 步：定位 .env。优先项目根，其次 backend/，都没有则退化为纯环境变量
    for candidate in (PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
            break

    # 第 2 步：逐项读取。凡是 .env 里没有的都用默认值，保证"至少能起来"
    settings = Settings(
        app_host=_env("APP_HOST", "127.0.0.1"),
        app_port=_env_int("APP_PORT", 8000),
        cors_allow_origins=_env("CORS_ALLOW_ORIGINS", ""),
        login_max_attempts=_env_int("LOGIN_MAX_ATTEMPTS", 5),
        login_lock_window_seconds=_env_int("LOGIN_LOCK_WINDOW_SECONDS", 300),
        db_host=_env("DB_HOST", "127.0.0.1"),
        db_port=_env_int("DB_PORT", 3306),
        db_user=_env("DB_USER", "root"),
        db_password=_env("DB_PASSWORD"),
        # 库名默认 knowledge_base；.env 若残留别的项目库名会被这里纠正（见 startup 校验）
        db_name=_env("DB_NAME", "knowledge_base") or "knowledge_base",
        db_charset=_env("DB_CHARSET", "utf8mb4"),
        jwt_secret=_env("JWT_SECRET_KEY"),
        jwt_expire_minutes=_env_int("JWT_EXPIRE_MINUTES", 120),
        milvus_url=_env("MILVUS_URL", "http://127.0.0.1:19530"),
        chunks_collection=_env("CHUNKS_COLLECTION", "kb_unit_chunks"),
        embedding_dim=_env_int("TEXT_EMBEDDING_DIMENSION", 1024),
        minio_endpoint=_env("MINIO_ENDPOINT", "127.0.0.1:9000"),
        minio_access_key=_env("MINIO_ACCESS_KEY"),
        minio_secret_key=_env("MINIO_SECRET_KEY"),
        minio_bucket=_env("MINIO_BUCKET_NAME", "knowledge-base"),
        minio_secure=_env("MINIO_SECURE", "").lower() in ("1", "true", "yes"),
        redis_host=_env("REDIS_HOST", "127.0.0.1"),
        redis_port=_env_int("REDIS_PORT", 6379),
        redis_db=_env_int("REDIS_DB", 0),
        redis_password=_env("REDIS_PASSWORD"),
        llm_base_url=_env("OPENAI_BASE_URL"),
        llm_api_key=_env("OPENAI_API_KEY"),
        llm_model=_env("LLM_DEFAULT_MODEL", "qwen-flash"),
        llm_temperature=_env_float("LLM_DEFAULT_TEMPERATURE", 0.1),
        embedding_base_url=_env("OPENAI_BASE_URL"),
        # 向量化与对话走同一个兼容模式网关（实测该网关同时提供 /embeddings）。
        # 注意：.env 里的 TEXT_EMBEDDING_API_KEY 实测无效（401），因此优先用
        # OPENAI_API_KEY；要用别的 key 就设 EMBEDDING_API_KEY 覆盖它。
        embedding_api_key=(
            _env("EMBEDDING_API_KEY")
            or _env("OPENAI_API_KEY")
            or _env("TEXT_EMBEDDING_API_KEY")
        ),
        embedding_model=_env("TEXT_EMBEDDING_MODEL", "text-embedding-v4"),
        embedding_batch_size=_env_int("TEXT_EMBEDDING_BATCH_SIZE", 10),
        storage_root=_env("STORAGE_ROOT_DIR", "storage"),
        faq_min_frequency=_env_int("FAQ_MIN_FREQUENCY", 50),
        recall_similarity_threshold=_env_float("RECALL_SIMILARITY_THRESHOLD", 0.8),
        dept_permission_inherit=_env("DEPT_PERMISSION_INHERIT", "true").lower()
        in ("1", "true", "yes"),
        admin_role_codes=tuple(
            code.strip()
            for code in _env("ADMIN_ROLE_CODES", "sys_admin,kb_admin").split(",")
            if code.strip()
        ),
        conversation_context_rounds=_env_int("CONVERSATION_CONTEXT_ROUNDS", 10),
    )
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置。用 lru_cache 保证 .env 只解析一次。"""
    return _load_settings()
