"""知识单元管理服务 · 原始文件存储与向量同步。

对应《技术方案设计文档》5.3 的第 1、6 项职责：

    1. 文档上传：原始文件落 MinIO
    6. 向量化同步：切片向量写入 Milvus，集合主键携带 unit_id，删除知识单元时同步删除向量

提供两个纯技术向的封装，都不含业务规则（业务规则在服务主模块 knowledge.py）：

* :class:`ObjectStorage` —— MinIO 原始文件的上传与删除；
* :class:`VectorStore`   —— Milvus 集合的创建、切片向量写入、按知识单元删除。

关于向量生成：文档第 14 章把 Embedding 模型选型列为待确认项，
因此本模块**不绑死任何模型**，只接受一个由外部注入的向量化函数
:data:`EmbeddingFn`，模型换掉时这里一行都不用改。
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from models.vector import (
    CATEGORY_MAX_LENGTH,
    COLLECTION_NAME,
    FIELD_CATEGORY,
    FIELD_CHUNK_ID,
    FIELD_EMBEDDING,
    FIELD_STATUS,
    FIELD_UNIT_ID,
    build_collection_schema,
    build_index_params,
    build_unit_filter,
)

__all__ = ["EmbeddingFn", "ObjectStorage", "VectorStore"]

# 向量化函数签名：输入一批文本，输出与之一一对应的向量。
# 用 Callable 而不是绑死某个 SDK，是为了把"用哪个 Embedding 模型"这个
# 待确认项留在调用方，而不是固化进本模块。
EmbeddingFn = Callable[[Sequence[str]], Sequence[Sequence[float]]]


class ObjectStorage:
    """MinIO 原始文件存储封装。

    只做"存原始文件"和"删原始文件"两件事。文件解析出的正文写入 MySQL，
    原始文件留在对象存储，便于后续追溯来源（对应 2.9.3 的"来源文件名"展示）。
    """

    def __init__(self, client: Any, bucket: str) -> None:
        """:param client: 已构造好的 ``minio.Minio`` 客户端。
        :param bucket: 存放原始文件的桶名。
        """
        self._client = client
        self._bucket = bucket

    @staticmethod
    def build_object_key(unit_code: str, file_name: str) -> str:
        """生成对象键。

        用知识单元编号做前缀目录，便于按单元定位与清理；
        文件名只取最后一段，避免目录上传时把相对路径写进对象键。
        """
        return f"knowledge_units/{unit_code}/{Path(file_name).name}"

    def put_object(self, object_key: str, data: bytes) -> str:
        """上传原始文件。

        :return: 对象键，由调用方决定是否持久化（本表未设存储路径字段，
            因此当前只用于日志与失败回滚）。
        """
        # 统一按二进制流上传，content-type 交给 MinIO 按扩展名推断
        self._client.put_object(
            self._bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/octet-stream",
        )
        return object_key

    def remove_object(self, object_key: str) -> None:
        """删除原始文件。用于导入失败时的回滚，避免留下孤儿文件。"""
        self._client.remove_object(self._bucket, object_key)


class VectorStore:
    """Milvus 切片向量存储封装。

    集合结构见 ``app.models.vector``（集合名 ``kb_unit_chunks``）。
    每次写入都先按 ``unit_id`` 清掉旧切片再写新切片，
    这样"内容变更后重新切片"与"重复导入"都天然幂等，不必额外判重。
    """

    def __init__(
        self,
        client: Any,
        embedding_fn: EmbeddingFn,
        embedding_dim: int,
    ) -> None:
        """:param client: 已构造好的 ``pymilvus.MilvusClient``。
        :param embedding_fn: 外部注入的向量化函数，模型选型待确认。
        :param embedding_dim: 向量维度，必须与 ``embedding_fn`` 输出的维度一致。
            刻意不设默认值 —— 维度取决于待确认的 Embedding 模型。
        """
        self._client = client
        self._embedding_fn = embedding_fn
        self._dim = embedding_dim

    def ensure_collection(self) -> None:
        """集合不存在时创建（含向量索引）。

        幂等：已存在则直接返回，可安全地在每次导入前调用。
        """
        if self._client.has_collection(COLLECTION_NAME):
            return
        # 集合结构来自 app.models.vector，字段名与维度都在那里统一维护
        self._client.create_collection(
            collection_name=COLLECTION_NAME,
            schema=build_collection_schema(self._dim),
            index_params=build_index_params(),
        )

    def upsert_chunks(
        self,
        unit_id: int,
        unit_code: str,
        category: str | None,
        status: str,
        chunks: Sequence[str],
    ) -> int:
        """写入某知识单元的全部切片向量。

        :param unit_id: 知识单元主键，写入 Milvus 的 ``unit_id`` 标量字段。
        :param unit_code: 知识单元编号，用于拼主键 ``chunk_id``。
        :param category: 分类，可为空。
        :param status: 知识单元状态（与 MySQL 侧取值保持一致）。
        :param chunks: 切片文本列表。
        :return: 实际写入的切片数量。
        """
        # 先删旧切片：保证重复导入或内容更新时不会残留旧向量
        self.delete_by_unit(unit_id)
        if not chunks:
            return 0

        self.ensure_collection()
        # 批量向量化：一次调用把整篇切片交给 Embedding 函数，减少往返
        vectors = self._embedding_fn(list(chunks))
        rows = []
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            rows.append(
                {
                    # 主键由业务侧生成，在同一知识单元内按序号递增，便于定位问题切片
                    FIELD_CHUNK_ID: f"{unit_code}-{index:04d}",
                    FIELD_UNIT_ID: unit_id,
                    # Milvus 标量字段不接受空值，分类为空时写空串占位
                    FIELD_CATEGORY: (category or "")[:CATEGORY_MAX_LENGTH],
                    FIELD_STATUS: status,
                    FIELD_EMBEDDING: list(vector),
                }
            )
        self._client.insert(collection_name=COLLECTION_NAME, data=rows)
        return len(rows)

    def delete_by_unit(self, unit_id: int) -> None:
        """按知识单元删除其全部切片向量（5.3 第 6 项：删除知识单元时同步删除向量）。"""
        if not self._client.has_collection(COLLECTION_NAME):
            return
        self._client.delete(
            collection_name=COLLECTION_NAME,
            filter=build_unit_filter(unit_id),
        )
