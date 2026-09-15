"""AI 鉴权检索服务 · 混合召回（2.9.6 / 文档 5.5 的"混合召回"）。

对应 5.5「混合召回：向量检索（Milvus）+ 关键字检索（MySQL 全文/``LIKE``），合并排序」，
以及 3.1 中 A3 RecallAgent 的三个工具：``vector_search`` / ``keyword_search`` / ``merge_rank``。

**本模块只负责"召回"，不负责"能不能看"**：召回产出的是候选集，
是否能访问必须由数据权限引擎判定（见 :mod:`data_permission_engine.permission`）。
这条边界是 3.1 的硬约束 —— 一旦在这里顺手过滤权限，就会出现两处鉴权逻辑，
迟早漂移成两个不同的答案。

**向量检索为什么是注入进来的**：Milvus 的集合与字段细节由知识单元管理服务
（``knowledge_unit_management_service.knowledge_storage``）负责，本模块只声明
"需要一个 输入查询文本 → 输出 (单元 id, 相似度) 的函数"，与 Embedding 模型
注入的做法一致，避免同一套 Milvus 访问代码在两个服务里各写一遍。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import NamedTuple

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from models import KnowledgeUnit

__all__ = [
    "RecalledUnit",
    "VectorSearchFn",
    "RecallOutcome",
    "HybridRetriever",
    "extract_keyword_terms",
]

# 向量检索函数的签名：输入查询文本与返回条数，输出 (知识单元 id, 相似度) 列表。
VectorSearchFn = Callable[[str, int], Sequence[tuple[int, float]]]

# 切片摘取长度。需求未规定摘要片段长度，集中在此便于调整。
SNIPPET_MAX_CHARS = 200

# 合并排序的加权系数。需求只写"合并排序"、未给权重，因此这里取一个明确且可调的
# 方案：向量分与关键字分各自乘系数后相加，两条路都命中的单元再加一点奖励分。
VECTOR_WEIGHT = 1.0
KEYWORD_WEIGHT = 0.8
BOTH_HIT_BONUS = 0.2

# 召回的相关性门槛。**必须有这道门槛**，否则召回永远"非空"：
# 中文 2-gram 会让"公司""规则""的X"这类通用词也命中，
# 实测未命中提问（知识库确实没有相关内容）也能召回 5 条 —— 那样
# 11.4 的"无可用知识单元支撑"就永远判不出来。
#
# 取值来自实测：正确命中的关键词分在 0.43~0.56，噪声在 0.09~0.17；
# 向量分正确命中 0.75，弱相关 0.45 以下。两条阈值分别卡在这两段之间。
# 属需要按语料调整的参数（第 14 章【待确认】）。
KEYWORD_MIN_SCORE = 0.25
VECTOR_MIN_SCORE = 0.5

# 关键字检索最多使用的词数。中文没有分词器，用 2-gram 兜底会产生较多词，
# 因此必须限流，否则一条长提问会生成上百个 LIKE 条件。
MAX_KEYWORD_TERMS = 24

# 2-gram 的长度：中文双字组合的召回效果最稳
KEYWORD_GRAM = 2

# 把标点与空白统一成切分点
_TOKEN_SPLIT_RE = re.compile(r"[^\w\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def extract_keyword_terms(
    question: str,
    *,
    max_terms: int = MAX_KEYWORD_TERMS,
    gram: int = KEYWORD_GRAM,
) -> list[str]:
    """从提问里抽取用于 ``LIKE`` 匹配的关键词。

    **为什么不能直接拿整句去 LIKE**：``content LIKE '%差旅报销的住宿标准是多少%'``
    这种整句匹配在中文里几乎必然落空 —— 知识库里不会出现和提问一模一样的句子。
    早期的实现就是这么写的，导致"向量库不可用时降级为关键字检索"这条降级路径
    实际上等同于"召回为空"。

    这里采用无分词器环境下的可行做法：拉丁文片段按空格切出整词；中文片段切 2-gram
    （``财务内控`` → ``财务``/``务内``/``内控``），只要有一个 gram 命中就算召回。
    结果按出现顺序去重并截断到 ``max_terms``。
    """
    cleaned = _TOKEN_SPLIT_RE.sub(" ", (question or "").lower())
    terms: list[str] = []
    seen: set[str] = set()

    for segment in cleaned.split():
        if _CJK_RE.search(segment):
            # 中文片段：切 2-gram
            for start in range(max(len(segment) - gram + 1, 1)):
                piece = segment[start : start + gram]
                if len(piece) < gram or piece in seen:
                    continue
                seen.add(piece)
                terms.append(piece)
        elif len(segment) >= gram and segment not in seen:
            # 拉丁文/数字片段：整词即可
            seen.add(segment)
            terms.append(segment)
        if len(terms) >= max_terms:
            break

    return terms[:max_terms]


class RecalledUnit(NamedTuple):
    """一条召回候选。

    :param unit_id: 知识单元 id。
    :param title: 标题，用于引用来源展示。
    :param score: 合并后的排序分，仅用于排序与引用展示，不代表权限判定结果。
    :param snippet: 正文摘要片段，供 Prompt 组装与引用卡片使用。
    """

    unit_id: int
    title: str
    score: float
    snippet: str


class RecallOutcome(NamedTuple):
    """一次召回的完整结果。

    :param units: 候选知识单元（已按分数降序）。
    :param degraded: 降级标记，正常为 ``None``；向量库不可用时为 ``"keyword_only"``。
        取值与 3.4 中 ``QAState.degraded`` 的约定保持一致，编排层可直接写回状态。
    """

    units: list[RecalledUnit]
    degraded: str | None


class HybridRetriever:
    """向量 + 关键字混合召回。

    依赖注入：会话与向量检索函数由外部传入。
    本类只读，不写库、不开事务。
    """

    def __init__(
        self,
        session: Session,
        vector_search: VectorSearchFn,
        *,
        default_top_k: int = 5,
        snippet_max_chars: int = SNIPPET_MAX_CHARS,
    ) -> None:
        """:param session: SQLAlchemy 会话（关键字检索与候选水合都用它）。
        :param vector_search: 向量检索函数，见 :data:`VectorSearchFn`。
        :param default_top_k: 未显式传 top_k 时的默认返回条数。
        :param snippet_max_chars: 摘要片段长度上限。
        """
        self._session = session
        self._vector_search = vector_search
        self._default_top_k = default_top_k
        self._snippet_max_chars = snippet_max_chars

    def recall(
        self,
        question: str,
        top_k: int | None = None,
        *,
        keyword_min_score: float = KEYWORD_MIN_SCORE,
        vector_min_score: float = VECTOR_MIN_SCORE,
    ) -> RecallOutcome:
        """执行一次完整混合召回。

        流程：向量召回 ∥ 关键字召回 → **相关性过滤** → 合并排序 → 取 TOP K → 水合。

        :param question: 用户提问原文。
        :param top_k: 返回条数，不传取构造时的默认值。
        :param keyword_min_score: 关键字命中分下限，低于它视为噪声丢弃。
        :param vector_min_score: 向量相似度下限，低于它视为噪声丢弃。
        :return: :class:`RecallOutcome`；向量库异常时降级为纯关键字检索，
            并在 ``degraded`` 中标记 ``"keyword_only"``（3.4 的降级约定）。
        """
        # 第 1 步：入参兜底，空问题直接返回空候选，避免无意义的两次检索
        question = (question or "").strip()
        limit = top_k or self._default_top_k
        if not question or limit <= 0:
            return RecallOutcome([], None)

        # 第 2 步：向量召回。失败即降级 —— 向量库不可用时仍应给出关键字检索结果
        degraded: str | None = None
        vector_hits: list[tuple[int, float]] = []
        try:
            vector_hits = self.vector_recall(question, limit)
        except Exception:
            degraded = "keyword_only"

        # 第 3 步：关键字召回（MySQL 侧，不依赖外部服务）
        keyword_hits = self.keyword_recall(question, limit)

        # 第 4 步：相关性过滤。这一步决定了"召回为空"是否真的意味着"没有支撑"，
        # 而 11.4 的知识缺口判定正建立在这个语义上
        vector_hits = [(uid, s) for uid, s in vector_hits if s >= vector_min_score]
        keyword_hits = [(uid, s) for uid, s in keyword_hits if s >= keyword_min_score]

        # 第 5 步：合并排序并取前 K
        ranked = self.merge_rank(vector_hits, keyword_hits, limit)

        # 第 6 步：补上标题与摘要片段，构成可用的候选集
        return RecallOutcome(self.hydrate_units(ranked), degraded)

    def vector_recall(self, question: str, limit: int) -> list[tuple[int, float]]:
        """向量召回：委托给注入的向量检索函数（Milvus 侧实现由知识单元服务提供）。"""
        hits = self._vector_search(question, limit)
        # 规整成统一类型，避免不同实现返回 tuple / list 混用导致后续比较出错
        return [(int(unit_id), float(score)) for unit_id, score in hits]

    def keyword_recall(self, question: str, limit: int) -> list[tuple[int, float]]:
        """关键字召回：标题或正文命中提问中的任意关键词即算候选。

        实现用 ``LIKE``（5.5 允许"MySQL 全文 / LIKE"）。``LIKE '%词%'`` 无法命中索引，
        在正文列（LONGTEXT）上尤其慢，因此这里做了两层限制：关键词最多
        ``MAX_KEYWORD_TERMS`` 个，命中结果严格限制条数。数据量上来后应换成
        MySQL 全文索引或独立倒排检索，属实现层的后续优化。

        :return: ``(单元 id, 归一化命中分)``；命中分 = 命中关键词数 / 关键词总数，
            取值 0~1，便于与向量相似度一起参与合并排序。
        """
        # 第 1 步：抽关键词。抽不出来（例如提问只有标点）就返回空，避免全表命中
        terms = extract_keyword_terms(question)
        if not terms:
            return []

        # 第 2 步：拼装条件。每条关键词在"标题或正文"任一命中即记一分
        hit_conditions = []
        score_expression = None
        for term in terms:
            pattern = f"%{term}%"
            condition = or_(
                KnowledgeUnit.title.like(pattern),
                KnowledgeUnit.content.like(pattern),
            )
            hit_conditions.append(condition)
            scored = case((condition, 1), else_=0)
            score_expression = (
                scored if score_expression is None else score_expression + scored
            )

        # 第 3 步：按命中关键词数降序取前 limit 条（命中越多越相关）
        rows = self._session.execute(
            select(KnowledgeUnit.id, score_expression.label("hits"))
            .where(or_(*hit_conditions))
            .order_by(score_expression.desc(), KnowledgeUnit.id)
            .limit(limit)
        ).all()

        # 第 4 步：归一化成分数（命中数 / 关键词总数），便于与向量分同量级比较
        total = len(terms)
        return [(int(unit_id), float(hits) / total) for unit_id, hits in rows]

    @staticmethod
    def merge_rank(
        vector_hits: Sequence[tuple[int, float]],
        keyword_hits: Sequence[tuple[int, float]],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        """合并两路召回结果并排序。

        同一单元被两路同时命中时分数相加（再给一点奖励分），
        这样"向量与关键字都指向它"的单元会排到前面 —— 两路一致的信号是强信号。
        """
        # 第 1 步：先累加向量分，同时记下向量命中的 id 集合
        scores: dict[int, float] = {}
        vector_ids: set[int] = set()
        for unit_id, score in vector_hits:
            scores[unit_id] = scores.get(unit_id, 0.0) + score * VECTOR_WEIGHT
            vector_ids.add(unit_id)
        # 第 2 步：再累加关键字分，并对两路都命中的单元追加奖励分
        keyword_ids: set[int] = set()
        for unit_id, score in keyword_hits:
            scores[unit_id] = scores.get(unit_id, 0.0) + score * KEYWORD_WEIGHT
            keyword_ids.add(unit_id)
        for unit_id in keyword_ids & vector_ids:
            scores[unit_id] += BOTH_HIT_BONUS
        # 第 3 步：按分数降序、同分按 id 升序（保证结果稳定可复现）
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        # 第 4 步：截断到 top_k
        return ranked if top_k is None else ranked[:top_k]

    def hydrate_units(self, ranked: Sequence[tuple[int, float]]) -> list[RecalledUnit]:
        """按 id 取回标题与正文，组装成候选列表。

        向量检索只返回 id 与分数，标题和正文必须回关系库取；
        这里刻意用一次 ``IN`` 查询批量取回，然后按传入顺序重排，
        避免为了保序而逐个查库（N+1）。
        """
        # 第 1 步：无候选直接返回
        if not ranked:
            return []
        ids = [unit_id for unit_id, _ in ranked]
        # 第 2 步：一次批量取回
        rows = (
            self._session.execute(
                select(
                    KnowledgeUnit.id, KnowledgeUnit.title, KnowledgeUnit.content
                ).where(KnowledgeUnit.id.in_(ids))
            )
            .all()
        )
        by_id = {row[0]: row for row in rows}
        # 第 3 步：按 ranked 的顺序重建列表，并生成摘要片段
        units: list[RecalledUnit] = []
        for unit_id, score in ranked:
            row = by_id.get(unit_id)
            if row is None:
                # 向量库里残留了已被删除的单元：跳过，不编造标题
                continue
            units.append(
                RecalledUnit(
                    unit_id=unit_id,
                    title=row[1] or "",
                    score=score,
                    snippet=self._build_snippet(row[2]),
                )
            )
        return units

    def _build_snippet(self, content: str | None) -> str:
        """从正文截取摘要片段：折叠空白后按长度上限截断。"""
        # 第 1 步：折叠换行与多余空格，避免片段里全是空白
        flattened = " ".join((content or "").split())
        # 第 2 步：超长则截断并加省略号，方便前端与 Prompt 直接使用
        if len(flattened) <= self._snippet_max_chars:
            return flattened
        return flattened[: self._snippet_max_chars] + "…"
