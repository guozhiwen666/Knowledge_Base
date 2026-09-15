"""对象存储与向量库客户端工厂（对应 5.3 的 MinIO 与 7.5 的 Milvus）。

把"第三方客户端怎么造、服务层需要的封装对象怎么拼"收在这一处，
服务层只管拿现成对象用（依赖注入），不关心连接细节。

同时暴露 :func:`check_collection_name` —— 校验 `.env` 的 ``CHUNKS_COLLECTION``
与 ``models.vector.COLLECTION_NAME`` 是否一致。两处各写一个集合名迟早会漂移，
让它在启动时就报出来，比等到检索查不到数据再排查划算得多。
"""

from __future__ import annotations

from typing import Any

from core.config import Settings

__all__ = [
    "build_minio_client",
    "build_object_storage",
    "build_milvus_client",
    "build_vector_store",
    "check_collection_name",
]


def build_minio_client(settings: Settings) -> Any:
    """构造 MinIO 客户端，并保证桶存在。

    桶不存在时上传会直接失败，而"桶没建"这种环境问题不该让业务去发现，
    因此这里顺手建桶（已存在则无操作，幂等）。
    """
    from minio import Minio

    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    # 桶名是 MinIO 的全局唯一标识，重复创建会被拒，因此先查后建
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)
    return client


def build_object_storage(settings: Settings) -> Any:
    """构造知识单元管理服务需要的 MinIO 封装对象。"""
    from services.knowledge_unit_management_service.knowledge_storage import (
        ObjectStorage,
    )

    return ObjectStorage(build_minio_client(settings), settings.minio_bucket)


def build_milvus_client(settings: Settings) -> Any:
    """构造 Milvus 客户端（MilvusClient 形态，collection 级 API）。"""
    from pymilvus import MilvusClient

    return MilvusClient(uri=settings.milvus_url)


def build_vector_store(settings: Settings, embedding_fn: Any) -> Any:
    """构造知识单元管理服务需要的向量存储封装对象。

    :param embedding_fn: 向量化函数；维度取自配置的 ``TEXT_EMBEDDING_DIMENSION``，
        必须与模型实际输出维度一致，否则 Milvus 会拒绝写入。
    """
    from services.knowledge_unit_management_service.knowledge_storage import VectorStore

    return VectorStore(
        build_milvus_client(settings),
        embedding_fn,
        embedding_dim=settings.embedding_dim,
    )


def check_collection_name(settings: Settings) -> tuple[bool, str]:
    """校验配置里的集合名与代码常量是否一致。

    :return: ``(是否一致, 说明)``。不一致说明检索会写进一个集合、
        另一处却在查另一个集合 —— 这类问题不报出来就是"查不到数据"的悬案。
    """
    from models.vector import COLLECTION_NAME

    if settings.chunks_collection == COLLECTION_NAME:
        return True, f"向量集合名一致：{COLLECTION_NAME}"
    return False, (
        f"向量集合名不一致：.env 的 CHUNKS_COLLECTION={settings.chunks_collection}，"
        f"而代码常量 COLLECTION_NAME={COLLECTION_NAME}；"
        "实际写入以代码常量为准，请把 .env 改成一致"
    )
