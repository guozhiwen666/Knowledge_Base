"""缓存客户端：Redis 优先、进程内兜底（对应 2.2 的 Redis 与 5.8 的 FAQ 缓存）。

**为什么要做两套实现**：Redis 承担三件事 —— FAQ 缓存（4.4/5.8）、看板当日实时
计数（5.6）、LangGraph 会话持久化（4.7）。但它是**可降级**的：Redis 不在时
主问答链路照样要能跑完，只是少了缓存加速与实时计数。

因此这里给出统一的最小接口（``get`` / ``set`` / ``delete`` / ``incrby``），
Redis 可用就用 Redis，不可用就退回进程内字典，并在启动日志里**如实说明**用了哪一个
—— 悄悄降级比直接报错更难排查。
"""

from __future__ import annotations

import threading

from core.config import Settings

__all__ = ["MemoryCache", "RedisCache", "build_cache", "CacheUnavailable"]


class CacheUnavailable(RuntimeError):
    """缓存客户端不可用。"""


class MemoryCache:
    """进程内缓存兜底实现。

    只保证接口一致与线程安全，不做淘汰、不做持久化、多进程不共享。
    用途是"Redis 没起来时主链路仍然可跑"，不是生产方案。
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        """读一个键，不存在返回 ``None``。"""
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        """写一个键。"""
        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> None:
        """删一个键，不存在也不报错。"""
        with self._lock:
            self._data.pop(key, None)

    def incrby(self, key: str, amount: int) -> int:
        """原子自增；键不存在按 0 起算。非数字值按 0 起算，避免脏数据把计数打挂。"""
        with self._lock:
            try:
                current = int(self._data.get(key, 0))
            except (TypeError, ValueError):
                current = 0
            current += int(amount)
            self._data[key] = str(current)
            return current

    def clear(self) -> None:
        """清空（测试与缓存重建用）。"""
        with self._lock:
            self._data.clear()


class RedisCache:
    """Redis 实现。构造时就 ping 一次，连不上直接抛，由调用方决定降级。"""

    def __init__(self, settings: Settings, *, socket_timeout: float = 2.0) -> None:
        # 延迟导入：没装 redis 包的环境也能 import 本模块（只用兜底实现）
        import redis

        self._client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )
        # 构造即探活：把"连不上"暴露在启动阶段，而不是第一次写缓存时
        self._client.ping()

    def get(self, key: str) -> str | None:
        """读一个键。"""
        return self._client.get(key)

    def set(self, key: str, value: str) -> None:
        """写一个键（不设 TTL：FAQ 缓存靠 5.8 的失效逻辑重建，不靠过期）。"""
        self._client.set(key, value)

    def delete(self, key: str) -> None:
        """删一个键。"""
        self._client.delete(key)

    def incrby(self, key: str, amount: int) -> int:
        """原子自增，返回自增后的值。"""
        return int(self._client.incrby(key, int(amount)))


def _port_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    """快速探测端口是否可连。

    为什么要有这一步：直接构造 redis 客户端并 ping，遇到"端口被静默丢包"
    （而不是明确拒绝）时会一直等到操作系统超时 —— 实测让启动多花 30 秒。
    先做一次 1 秒的 socket 探测，既能快速失败，报错信息也更准确。
    """
    import socket

    probe = socket.socket()
    probe.settimeout(timeout)
    try:
        probe.connect((host, int(port)))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        probe.close()


def build_cache(settings: Settings) -> tuple[object, str]:
    """按可用性选择缓存实现。

    :return: ``(缓存客户端, 说明文本)``。说明文本会打进启动日志，
        让"当前用的是 Redis 还是内存兜底"一眼可见。
    """
    # 第 1 步：端口都连不上就没必要去构造客户端（避免长时间等待）
    if not _port_reachable(settings.redis_host, settings.redis_port):
        return MemoryCache(), (
            f"Redis {settings.redis_host}:{settings.redis_port} 端口不可达，"
            "已降级为进程内缓存；FAQ 缓存与实时计数仍可用，但不跨进程共享、重启即失"
        )

    # 第 2 步：端口通再构造并 ping，捕获认证失败等其它错误
    try:
        return RedisCache(settings), (
            f"Redis {settings.redis_host}:{settings.redis_port} 已连接"
        )
    except Exception as exc:  # noqa: BLE001 - 降级需要兜住所有失败
        return MemoryCache(), (
            f"Redis 不可用（{type(exc).__name__}），已降级为进程内缓存；"
            "FAQ 缓存与实时计数仍可用，但不跨进程共享、重启即失"
        )
