"""应用级依赖容器：把"一次性构造、全应用共用"的对象收在这里。

为什么需要它：服务层全是依赖注入（``KnowledgeUnitService(session, storage, vectors)``），
而 MinIO 客户端、Milvus 客户端、向量化函数这类东西每请求都重建既浪费又容易出错。
本模块负责构造并缓存它们，接口层通过 FastAPI 依赖取用。

**构造失败不阻断启动**：MinIO / Milvus / 模型是外部依赖，任何一个没起来，
只有用到它的接口该报错，其余接口（登录、组织架构、看板）照样要能用。
因此每个组件单独 try，失败时记 ``None`` + 一条说明，由使用方在运行时给出明确报错。
"""

from __future__ import annotations

from typing import Any

from core.cache import build_cache
from core.config import Settings, get_settings
from core.llm import build_embedding, build_llm_stream
from core.storage import (
    build_milvus_client,
    build_object_storage,
    build_vector_store,
    check_collection_name,
)

__all__ = ["Container", "get_container"]


class Container:
    """应用级单例组件容器。组件按需构造并缓存。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # 启动自检信息：既进日志，也用 /api/health 暴露给排查用
        self.notes: list[str] = []
        # 已确认构造失败的组件名，避免每次访问都重试一遍外部连接
        self._failed_names: set[str] = set()
        self._cache: Any = None
        self._object_storage: Any = None
        self._vector_store: Any = None
        self._embedding: Any = None
        self._llm_stream: Any = None
        self._milvus: Any = None

    # ------------------------------------------------------------------ 缓存

    @property
    def cache(self):
        """缓存客户端（Redis 或内存兜底）。第一次访问时构造。"""
        if self._cache is None:
            self._cache, note = build_cache(self.settings)
            self.notes.append(note)
        return self._cache

    # ------------------------------------------------------------ 对象存储

    @property
    def object_storage(self):
        """MinIO 封装对象；不可用时返回 ``None``。"""
        if self._object_storage is None and "minio" not in self._failed():
            try:
                self._object_storage = build_object_storage(self.settings)
                self.notes.append(f"MinIO {self.settings.minio_endpoint} 已连接")
            except Exception as exc:  # noqa: BLE001 - 单组件失败不阻断启动
                self._mark_failed("minio")
                self.notes.append(f"MinIO 不可用：{type(exc).__name__}: {exc}")
        return self._object_storage

    # -------------------------------------------------------------- 向量化

    @property
    def embedding(self):
        """向量化函数；未配置模型时返回 ``None``。"""
        if self._embedding is None and "embedding" not in self._failed():
            try:
                self._embedding = build_embedding(self.settings)
                self.notes.append(
                    f"向量化模型 {self.settings.embedding_model}"
                    f"（维度 {self.settings.embedding_dim}）"
                )
            except Exception as exc:  # noqa: BLE001
                self._mark_failed("embedding")
                self.notes.append(f"向量化不可用：{type(exc).__name__}: {exc}")
        return self._embedding

    # ------------------------------------------------------------ 向量存储

    @property
    def milvus(self):
        """Milvus 客户端；不可用时返回 ``None``。"""
        if self._milvus is None and "milvus" not in self._failed():
            try:
                self._milvus = build_milvus_client(self.settings)
                self.notes.append(f"Milvus {self.settings.milvus_url} 已连接")
            except Exception as exc:  # noqa: BLE001
                self._mark_failed("milvus")
                self.notes.append(f"Milvus 不可用：{type(exc).__name__}: {exc}")
        return self._milvus

    @property
    def vector_store(self):
        """知识单元管理服务用的向量存储封装；依赖 Milvus 与向量化函数。"""
        if self._vector_store is None and "vector_store" not in self._failed():
            if self.milvus is None or self.embedding is None:
                self._mark_failed("vector_store")
                self.notes.append("向量存储不可用：Milvus 或向量化未就绪")
                return None
            try:
                self._vector_store = build_vector_store(self.settings, self.embedding)
                # 集合名不一致只告警不阻断：以代码常量为准，`.env` 改过来即可
                _, note = check_collection_name(self.settings)
                self.notes.append(note)
            except Exception as exc:  # noqa: BLE001
                self._mark_failed("vector_store")
                self.notes.append(f"向量存储初始化失败：{type(exc).__name__}: {exc}")
        return self._vector_store

    # ---------------------------------------------------------------- 模型

    @property
    def llm_stream(self):
        """流式生成函数；未配置模型时返回 ``None``。"""
        if self._llm_stream is None and "llm_stream" not in self._failed():
            try:
                self._llm_stream = build_llm_stream(self.settings)
                self.notes.append(f"对话模型 {self.settings.llm_model}")
            except Exception as exc:  # noqa: BLE001
                self._mark_failed("llm_stream")
                self.notes.append(f"对话模型不可用：{type(exc).__name__}: {exc}")
        return self._llm_stream

    # ------------------------------------------------------------ 失败记录

    def _failed(self) -> set[str]:
        """已确认构造失败的组件名集合。"""
        return self._failed_names

    def _mark_failed(self, name: str) -> None:
        self._failed_names.add(name)


# 进程内单例。用模块级变量而不是 lru_cache，是为了让测试能整体替换。
_container: Container | None = None


def get_container() -> Container:
    """取应用级容器（FastAPI 依赖）。"""
    global _container
    if _container is None:
        _container = Container()
    return _container
