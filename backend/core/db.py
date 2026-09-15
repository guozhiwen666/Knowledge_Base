"""数据库层：引擎、会话工厂、FastAPI 依赖。

对应 2.9.7 的 10 张表与 2.2 的「关系库 MySQL 8.4」。

**expire_on_commit=False 的原因**：服务层的方法内部自行 commit（见各服务的
"事务边界"说明），接口层随后要读对象的属性做响应序列化。若保持默认的
``expire_on_commit=True``，每次读属性都会再打一次 SELECT，一页列表就能打出
几十次查询。这里关掉过期，用"提交后对象仍可读"换掉这批无谓往返。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings

__all__ = ["engine", "SessionLocal", "get_session", "ping_database"]

_settings = get_settings()

# 引擎是惰性的：这里不会真的连库，第一次执行语句时才建连接。
# pool_pre_ping 让连接被 MySQL 主动断开后能自动重连（长连接场景很常见）。
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话，请求结束自动关闭。

    注意不是"每请求一个事务" —— 事务边界由服务层的方法自己控制。
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def ping_database() -> tuple[bool, str]:
    """探活：返回 ``(是否连通, 说明)``。供启动时的自检使用。"""
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar_one()
        return True, f"MySQL {version} 已连接（库 {_settings.db_name}）"
    except Exception as exc:  # noqa: BLE001 - 启动自检需要兜住所有失败
        return False, f"{type(exc).__name__}: {exc}"
