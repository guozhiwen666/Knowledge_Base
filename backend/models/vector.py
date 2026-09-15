"""向量库集合结构定义（Milvus）。

对应《技术方案设计文档》7.5 向量集合设计。本模块只描述**结构**：
集合名、字段、索引与删除表达式，不含向量写入与检索的业务逻辑
（那属于知识单元管理服务与 AI 鉴权检索服务的职责）。

集合结构一览：

===================  ==================  ==================================
字段                 类型                说明
===================  ==================  ==================================
``chunk_id``         VARCHAR(64)         主键，切片唯一标识
``unit_id``          INT64               所属知识单元，对应 MySQL ``knowledge_units.id``
``category``         VARCHAR(64)         知识单元分类，供按分类过滤
``status``           VARCHAR(32)         知识单元状态，供按状态过滤
``embedding``        FLOAT_VECTOR        切片向量，维度由 Embedding 模型决定
===================  ==================  ==================================
"""

from __future__ import annotations

from pymilvus import CollectionSchema, DataType, FieldSchema

__all__ = [
    "COLLECTION_NAME",
    "FIELD_CHUNK_ID",
    "FIELD_UNIT_ID",
    "FIELD_CATEGORY",
    "FIELD_STATUS",
    "FIELD_EMBEDDING",
    "CHUNK_ID_MAX_LENGTH",
    "CATEGORY_MAX_LENGTH",
    "STATUS_MAX_LENGTH",
    "INDEX_TYPE_IVF_FLAT",
    "INDEX_TYPE_HNSW",
    "METRIC_TYPE_COSINE",
    "build_collection_schema",
    "build_index_params",
    "build_unit_filter",
]

# 集合名（7.5）
COLLECTION_NAME = "kb_unit_chunks"

# 字段名常量。业务层引用这些常量而不是裸字符串，
# 避免字段改名时漏改某处导致检索静默失效。
FIELD_CHUNK_ID = "chunk_id"
FIELD_UNIT_ID = "unit_id"
FIELD_CATEGORY = "category"
FIELD_STATUS = "status"
FIELD_EMBEDDING = "embedding"

# 变长字段长度上限。chunk_id 由业务侧生成 UUID（36 字符），留出余量取 64。
CHUNK_ID_MAX_LENGTH = 64
CATEGORY_MAX_LENGTH = 64
STATUS_MAX_LENGTH = 32

# 索引类型与度量方式（7.5：IVF_FLAT 或 HNSW，度量 COSINE）
INDEX_TYPE_IVF_FLAT = "IVF_FLAT"
INDEX_TYPE_HNSW = "HNSW"
METRIC_TYPE_COSINE = "COSINE"


def build_collection_schema(embedding_dim: int) -> CollectionSchema:
    """构造 ``kb_unit_chunks`` 的集合结构。

    :param embedding_dim: 向量维度。**刻意不设默认值** —— 7.5 明确写的是
        "维度随所选 Embedding 模型而定"，具体选型在文档第 14 章列为待确认项，
        因此这里不固化任何数字，由调用方在确定模型后显式传入，
        避免一个想当然的维度悄悄写进索引结构。
    :return: 可直接交给 ``Collection(COLLECTION_NAME, schema)`` 的集合结构对象。
    """
    # 逐个字段构造，顺序与文档 7.5 的表格保持一致
    fields = [
        # 主键：切片唯一标识。7.5 允许"自增或 UUID"，此处按 UUID 字符串主键定义，
        # 因此 auto_id=False，主键值由业务侧写入。
        FieldSchema(
            name=FIELD_CHUNK_ID,
            dtype=DataType.VARCHAR,
            is_primary=True,
            auto_id=False,
            max_length=CHUNK_ID_MAX_LENGTH,
            description="切片主键，UUID",
        ),
        # 标量字段 1：所属知识单元，删除同步时按该字段过滤
        FieldSchema(
            name=FIELD_UNIT_ID,
            dtype=DataType.INT64,
            description="所属知识单元 id",
        ),
        # 标量字段 2：分类，供按分类过滤检索
        FieldSchema(
            name=FIELD_CATEGORY,
            dtype=DataType.VARCHAR,
            max_length=CATEGORY_MAX_LENGTH,
            description="知识单元分类",
        ),
        # 标量字段 3：状态，供按状态过滤检索
        FieldSchema(
            name=FIELD_STATUS,
            dtype=DataType.VARCHAR,
            max_length=STATUS_MAX_LENGTH,
            description="知识单元状态",
        ),
        # 向量字段：切片向量，维度由入参决定
        FieldSchema(
            name=FIELD_EMBEDDING,
            dtype=DataType.FLOAT_VECTOR,
            dim=embedding_dim,
            description="切片向量",
        ),
    ]
    return CollectionSchema(fields=fields, description="知识单元切片向量集合")


def build_index_params(
    index_type: str = INDEX_TYPE_IVF_FLAT,
    field_name: str = FIELD_EMBEDDING,
):
    """构造向量字段的索引参数。

    **必须返回 ``IndexParams`` 对象而不是字典**：pymilvus 3.x 的
    ``MilvusClient.create_collection(index_params=...)`` 会做类型检查，
    传 ``dict`` 直接抛
    ``ParamError: expected type [IndexParams], got type [dict]``（已实测）。
    早期 2.x 能吃字典，这里以 3.x 的契约为准。

    :param index_type: ``INDEX_TYPE_IVF_FLAT`` 或 ``INDEX_TYPE_HNSW``。
    :param field_name: 向量字段名，默认 ``embedding``。
    :return: 可直接传给 ``create_collection`` 的 ``IndexParams`` 对象。
    :raise ValueError: 传入未支持的索引类型时抛出，避免静默降级。
    """
    # 延迟导入：pymilvus 是可选依赖，不在模块顶层引入
    from pymilvus.milvus_client.index import IndexParams

    params = IndexParams()
    if index_type == INDEX_TYPE_HNSW:
        # HNSW：M 控制图的出度，efConstruction 控制建图时的搜索宽度
        params.add_index(
            field_name=field_name,
            index_type=INDEX_TYPE_HNSW,
            metric_type=METRIC_TYPE_COSINE,
            params={"M": 16, "efConstruction": 200},
        )
        return params
    if index_type == INDEX_TYPE_IVF_FLAT:
        # IVF_FLAT：nlist 为聚类簇数，即建索引时的倒排单元数量
        params.add_index(
            field_name=field_name,
            index_type=INDEX_TYPE_IVF_FLAT,
            metric_type=METRIC_TYPE_COSINE,
            params={"nlist": 128},
        )
        return params
    raise ValueError(f"不支持的索引类型：{index_type}")


def build_unit_filter(unit_id: int) -> str:
    """构造按知识单元删除切片的过滤表达式。

    对应 7.5 "删除同步：删除知识单元时按 unit_id 过滤删除全部切片"，
    返回值可直接用于 ``collection.delete(expr)``。

    :param unit_id: 知识单元 id。
    :return: Milvus 过滤表达式，例如 ``unit_id == 1001``。
    """
    return f"{FIELD_UNIT_ID} == {unit_id}"
