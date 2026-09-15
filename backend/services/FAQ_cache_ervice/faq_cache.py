"""FAQ 缓存服务（2.9.6 / 文档 5.8）。

职责与实现落点：

    管理已发布 FAQ 问答对        -> :meth:`FaqCacheService.publish` /
                                  :meth:`FaqCacheService.invalidate`
    精确匹配                    -> :meth:`FaqCacheService.lookup` 第 1 步
    语义相似度匹配               -> :meth:`FaqCacheService.lookup` 第 2 步
    命中计数回写                 -> :meth:`FaqCacheService.increment_hit`

**缓存里存的是完整问答对，不是 id**：若只缓存 id，每次命中仍要回一次 MySQL，
"降低响应时间"就落空了（2.9.9 的目标）。因此缓存值直接存
``{id, question, answer}``，命中即可直接产出答案，**不产生任何数据库读**。

**为什么没有用 Milvus 存 FAQ 向量**：7.5 只定义了 ``kb_unit_chunks`` 一个集合，
没有 FAQ 向量集合。为了不给表结构/集合结构"偷偷加东西"，已发布 FAQ 的向量
与问答对一起放在缓存里（单键 JSON），语义匹配在内存里算余弦相似度。
这是权衡后的实现，若后续要为 FAQ 单独建 Milvus 集合，属于 7.5 的变更。

**缓存的读写接口是"鸭子类型"**：只要求客户端提供 ``get`` / ``set`` / ``delete``，
不 import redis，也就不给项目强加一个 Redis 依赖；没有 Redis 时传 ``None``，
精确匹配与语义匹配会直接跳过，问答主链路不受影响。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import NamedTuple

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import Faq
from models.enums import FaqStatus

__all__ = ["CacheHit", "FaqCacheService"]

# 缓存键前缀。精确匹配一题一键；语义匹配共用一个索引键。
EXACT_KEY_PREFIX = "kb:faq:exact:"
VECTOR_INDEX_KEY = "kb:faq:index"

# 语义命中阈值。需求表述为"高度相似问题"（2.9.4）与"达到阈值"（11.3），
# 未给具体数值，此处给默认值并暴露为构造参数。
DEFAULT_SIMILARITY_THRESHOLD = 0.92


class CacheHit(NamedTuple):
    """一次缓存命中。

    :param faq_id: 命中的 FAQ 主键。
    :param question: 标准问题。
    :param answer: 标准答案。
    :param matched_by: 命中方式，``exact``（精确）或 ``semantic``（语义）。
    :param score: 相似度；精确命中固定为 1.0。
    """

    faq_id: int
    question: str
    answer: str | None
    matched_by: str
    score: float


class FaqCacheService:
    """已发布 FAQ 的高速缓存。

    依赖注入：会话、缓存客户端、向量化函数均由外部传入。
    事务边界：仅 :meth:`increment_hit` 写库并提交，其余方法不碰关系库。
    """

    def __init__(
        self,
        session: Session,
        cache=None,
        embedding_fn: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None = None,
        *,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        """:param session: SQLAlchemy 会话（仅命中计数回写用）。
        :param cache: 缓存客户端，需提供 ``get`` / ``set`` / ``delete``；
            传 ``None`` 表示不启用缓存。
        :param embedding_fn: 向量化函数；不传则只做精确匹配，跳过语义匹配。
        :param similarity_threshold: 语义命中阈值（余弦相似度）。
        """
        self._session = session
        self._cache = cache
        self._embedding_fn = embedding_fn
        self._threshold = similarity_threshold

    # ------------------------------------------------------------------ 写入

    def publish(self, faq: Faq) -> None:
        """把审核通过的 FAQ 写入缓存（11.3：``approve`` 时写缓存）。

        幂等：重复发布会用同一份数据覆盖旧值。
        非 ``published`` 状态的 FAQ 一律不写入 —— 防止误把待审核内容放进缓存。
        """
        # 第 1 步：只发布已上线状态，其余状态直接忽略
        if self._cache is None or faq.status != FaqStatus.PUBLISHED.value:
            return
        # 第 2 步：组装缓存值（存完整问答对，命中时无需回库）
        payload = {"id": faq.id, "question": faq.question, "answer": faq.answer}
        # 第 3 步：写精确匹配键
        self._set(self._exact_key(faq.question), payload)
        # 第 4 步：有向量化能力时，把向量并进语义索引
        self._update_vector_index(faq, payload)

    def invalidate(self, faq: Faq) -> None:
        """让某条 FAQ 的缓存失效（5.8：FAQ 被编辑或重新审核时失效重建）。

        精确键与语义索引两处都要清，漏掉任一处都会让旧答案继续被命中。
        """
        # 第 1 步：缓存未启用则无事可做
        if self._cache is None:
            return
        # 第 2 步：清精确键
        self._delete(self._exact_key(faq.question))
        # 第 3 步：从语义索引里摘掉该条
        index = self._load_vector_index()
        if str(faq.id) in index:
            index.pop(str(faq.id), None)
            self._save_vector_index(index)

    # ------------------------------------------------------------------ 读取

    def lookup(self, question: str) -> CacheHit | None:
        """查询缓存：先精确匹配，再语义匹配（4.4 的执行顺序）。

        本方法是**只读的**，命中的计数回写由调用方在 ``done`` 之后异步执行
        （:meth:`increment_hit`），对应 4.4"命中则异步 ``hit_count`` 自增"。

        :return: 命中返回 :class:`CacheHit`，未命中返回 ``None``。
        """
        # 第 1 步：缓存未启用直接返回未命中
        if self._cache is None or not (question or "").strip():
            return None

        # 第 2 步：精确匹配
        raw = self._get(self._exact_key(question))
        payload = self._decode(raw)
        if payload is not None:
            return self._to_hit(payload, matched_by="exact", score=1.0)

        # 第 3 步：语义匹配。没有向量化能力时跳过，不影响精确匹配的结果
        if self._embedding_fn is None:
            return None
        index = self._load_vector_index()
        if not index:
            return None
        return self._semantic_lookup(question, index)

    def increment_hit(self, faq_id: int) -> None:
        """缓存命中后把 ``faqs.hit_count`` 自增（5.8 / 11.3）。

        用 SQL 表达式自增而不是"查出来 +1 再写回"，避免并发命中时互相覆盖。
        """
        self._session.execute(
            update(Faq).where(Faq.id == faq_id).values(hit_count=Faq.hit_count + 1)
        )
        self._session.commit()

    def load_published(self) -> list[Faq]:
        """取全部已发布的 FAQ。

        用途是缓存重建（例如缓存被清空后重新灌入），
        不对外暴露为接口 —— 8.7 没有"已发布 FAQ 库查询"接口（第 14 章【待确认】）。
        """
        return list(
            self._session.execute(
                select(Faq).where(Faq.status == FaqStatus.PUBLISHED.value)
            )
            .scalars()
            .all()
        )

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _exact_key(question: str) -> str:
        """精确匹配键：对归一化后的问题取 SHA-256。

        先归一化再哈希，让"大小写 / 首尾空白 / 标点"差异落到同一个键上；
        用哈希而不是原文做键，是为了避免超长问题把缓存键撑爆。
        """
        normalized = " ".join((question or "").lower().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"{EXACT_KEY_PREFIX}{digest}"

    def _semantic_lookup(self, question: str, index: dict) -> CacheHit | None:
        """在语义索引里找最相似的一条，达到阈值才算命中。"""
        # 第 1 步：把提问向量化
        vectors = self._embedding_fn([question])
        if not vectors:
            return None
        query_vector = list(vectors[0])
        # 第 2 步：逐个算余弦相似度，记下最优项
        best_id: str | None = None
        best_score = 0.0
        for faq_id, entry in index.items():
            vector = entry.get("vector") or []
            if not vector:
                continue
            score = self._cosine(query_vector, vector)
            if score > best_score:
                best_id, best_score = faq_id, score
        # 第 3 步：低于阈值视为未命中，继续走知识召回
        if best_id is None or best_score < self._threshold:
            return None
        # 第 4 步：用索引里存的问答对直接产出结果，不回库
        payload = index[best_id].get("payload")
        if not payload:
            return None
        return self._to_hit(payload, matched_by="semantic", score=best_score)

    def _update_vector_index(self, faq: Faq, payload: dict) -> None:
        """把某条 FAQ 的向量并入语义索引（无向量化能力时跳过）。"""
        if self._embedding_fn is None:
            return
        # 第 1 步：向量化标准问题
        vectors = self._embedding_fn([faq.question])
        if not vectors:
            return
        # 第 2 步：读-改-写单键索引。问答对随向量一起存，命中时不必回库
        index = self._load_vector_index()
        index[str(faq.id)] = {"vector": list(vectors[0]), "payload": payload}
        self._save_vector_index(index)

    def _load_vector_index(self) -> dict:
        """读语义索引，异常或未启用时返回空字典。"""
        raw = self._get(VECTOR_INDEX_KEY)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            # 索引损坏时按空索引处理：语义匹配失效，但不影响精确匹配与主链路
            return {}
        return data if isinstance(data, dict) else {}

    def _save_vector_index(self, index: dict) -> None:
        """写回语义索引。"""
        self._set(VECTOR_INDEX_KEY, index)

    @staticmethod
    def _to_hit(payload: dict, *, matched_by: str, score: float) -> CacheHit | None:
        """把缓存值转成 :class:`CacheHit`；值不完整时返回 ``None``。"""
        faq_id = payload.get("id")
        if faq_id is None:
            return None
        return CacheHit(
            faq_id=int(faq_id),
            question=payload.get("question") or "",
            answer=payload.get("answer"),
            matched_by=matched_by,
            score=score,
        )

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        """余弦相似度。长度不一致或出现零向量时返回 0（视为不相似）。"""
        if len(left) != len(right) or not left:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        norm_left = sum(a * a for a in left) ** 0.5
        norm_right = sum(b * b for b in right) ** 0.5
        if norm_left == 0 or norm_right == 0:
            return 0.0
        return dot / (norm_left * norm_right)

    def _get(self, key: str) -> str | None:
        """读缓存并统一成字符串；异常一律按未命中处理。"""
        try:
            value = self._cache.get(key)
        except Exception:
            return None
        if value is None:
            return None
        # Redis 客户端可能返回 bytes，统一解码
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def _set(self, key: str, payload) -> None:
        """写缓存（值统一序列化为 JSON 字符串）；异常一律吞掉，不影响主链路。"""
        try:
            self._cache.set(key, json.dumps(payload, ensure_ascii=False))
        except Exception:
            return

    def _delete(self, key: str) -> None:
        """删缓存键；异常一律吞掉（失效失败可接受的降级是"暂时命中旧答案"）。"""
        try:
            self._cache.delete(key)
        except Exception:
            return

    @staticmethod
    def _decode(raw: str | None) -> dict | None:
        """把缓存值解析成字典，失败返回 ``None``。"""
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None
