"""知识沉淀挖掘服务 · 挖掘算法（2.9.6 / 文档 5.7 前半段 + 4.5 子图）。

对应 4.5 沉淀子图的两条并行产出：

    高频相似问题 → 推荐 FAQ 项（``source_type = auto_mined``，``status = pending_review``）
    未命中提问   → 知识缺口项（``knowledge_gaps``，按 ``question_pattern`` 聚合）

**聚类的实现方式**：9.9 / 11.3 要求"语义去重、提问向量聚类"，但**没有指定算法**。
因此这里把聚类抽成可注入的 :data:`ClusterFn`，并给一个**确定性默认实现**：
按归一化后的问题文本精确分组。默认实现不依赖任何模型、可测试、结果稳定；
换成向量聚类只需在构造时换掉 ``cluster_fn``，本模块其余逻辑无需改动。

**两个必须说明的数据约束**（都是需求与表结构对不上的地方）：

1. **"召回相似度低于阈值"怎么落地**：11.4 的缺口判定条件之一是"相似度低于阈值"，
   但 ``qa_access_logs`` **没有相似度列**。解决办法是在召回侧就把低于阈值的候选丢掉
   （见 ``retrieval`` 的 ``KEYWORD_MIN_SCORE`` / ``VECTOR_MIN_SCORE``），
   于是"召回列表为空"同时覆盖"未命中"与"全部低于阈值"两种情况。
   本模块据此判定，**不使用授权列表** —— 用授权列表会把"权限被拒"和
   "FAQ 命中缓存"两类记录误判成知识缺口（详见 :meth:`_cluster_logs` 第 2 步）；
2. **推荐频次没有存储列**：8.7 的推荐列表要展示"推荐频次"，但 ``faqs`` 表没有频次列，
   只能在查询时回 ``qa_access_logs`` 现算（见 ``settlement.list_recommendations``）。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Faq, KnowledgeGap, QaAccessLog
from models.enums import FaqSourceType, FaqStatus, KnowledgeGapStatus

__all__ = [
    "ClusterFn",
    "normalize_question",
    "default_cluster",
    "QuestionMiner",
]

# 聚类函数签名：输入一批提问，输出与之一一对应的簇编号。
# 不绑死算法，是为了让"换成向量聚类"这件事只影响注入点。
ClusterFn = Callable[[Sequence[str]], Sequence[int]]

# 挖掘窗口默认天数。需求未规定回溯范围，集中在此便于调整。
DEFAULT_WINDOW_DAYS = 30

# 推荐 FAQ 的触发阈值（11.3"频次达到阈值"）。第 14 章 #8 的确认值为 50。
#
# 这个量级意味着"偶发提问不会变成推荐 FAQ"—— 只有真正反复被问到的才值得
# 沉淀成标准问答。演示数据因此需要刻意造出足够频次（见 ``scripts/seed_data.py``）。
# 实际运行值由配置层的 ``FAQ_MIN_FREQUENCY`` 经 ``SettlementService`` 注入，
# 这里只作为"未配置时的默认"。
DEFAULT_MIN_FREQUENCY = 50

# 缺口样本保留条数上限，避免一条模式攒下上千条样本把 JSON 列撑爆
MAX_SAMPLE_QUESTIONS = 20

# 归一化时剔除的字符：中英文标点与多余空白。
# 目的是让"差旅报销标准是什么？"与"差旅报销标准是什么"归到同一模式。
_PUNCTUATION_RE = re.compile(r"[\s，。！？；：、,\.!\?;:'\"“”‘’（）()【】\[\]《》<>—\-_]+")


def normalize_question(question: str) -> str:
    """问题归一化：折叠空白、转小写、去掉标点。

    只做"形式归一"，不做语义改写 —— 语义层面的合并交给 :data:`ClusterFn`。
    """
    # 第 1 步：统一小写并剔除标点与空白
    cleaned = _PUNCTUATION_RE.sub("", (question or "").lower())
    # 第 2 步：仍为空说明原问题只有标点，返回空串由调用方过滤
    return cleaned


def default_cluster(questions: Sequence[str]) -> list[int]:
    """默认聚类：按归一化文本精确分组（确定性、无外部依赖）。

    同一归一化文本归为同一簇，返回的簇编号与入参顺序一一对应。
    """
    # 第 1 步：按归一化文本分配递增簇号
    mapping: dict[str, int] = {}
    labels: list[int] = []
    for question in questions:
        key = normalize_question(question)
        if key not in mapping:
            mapping[key] = len(mapping)
        labels.append(mapping[key])
    return labels


class _Cluster:
    """一个聚类簇的累计统计（模块内部使用）。"""

    __slots__ = ("pattern", "samples", "units")

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        # 样本提问原文，保留用于知识缺口的 sample_questions_json
        self.samples: list[str] = []
        # 该簇提问命中的知识单元计数，用于推关联知识单元
        self.units: Counter = Counter()


class QuestionMiner:
    """高频问题挖掘与知识缺口识别。

    依赖注入：会话由外部传入。事务边界：两个挖掘方法各自 commit。
    """

    def __init__(
        self,
        session: Session,
        *,
        cluster_fn: ClusterFn = default_cluster,
        min_frequency: int = DEFAULT_MIN_FREQUENCY,
    ) -> None:
        """:param session: SQLAlchemy 会话。
        :param cluster_fn: 聚类函数，默认按归一化文本分组。
        :param min_frequency: 推荐 FAQ 的频次阈值。
        """
        self._session = session
        self._cluster_fn = cluster_fn
        self._min_frequency = min_frequency

    # ------------------------------------------------------------ 推荐 FAQ

    def mine_faq_recommendations(
        self, window_days: int = DEFAULT_WINDOW_DAYS
    ) -> list[Faq]:
        """挖掘高频相似问题并生成待审核 FAQ 推荐项（11.3）。

        已有同问题的 FAQ 记录（不论状态）不再重复生成，保证反复执行幂等。

        :return: 本次新建的推荐项列表。
        """
        # 第 1 步：取窗口期内的有效提问，并按归一化文本聚类
        clusters = self._cluster_logs(window_days)
        if not clusters:
            return []

        # 第 2 步：取回已存在的 FAQ 问题集合。除了原文集合，还要建一份**归一化**集合：
        # 库里存的可能是"差旅报销标准是什么？"，而本次簇的模式是"差旅报销标准是什么"，
        # 只比原文会漏判，导致同一个问题被反复推荐
        existing = set(self._session.execute(select(Faq.question)).scalars())
        existing_normalized = {normalize_question(q) for q in existing if q}

        created: list[Faq] = []
        for cluster in clusters:
            # 第 3 步：频次未达阈值则跳过（11.3 的触发条件）
            if len(cluster.samples) < self._min_frequency:
                continue
            # 第 4 步：去重。用簇内出现次数最多的原始提问作为标准问题
            question = Counter(cluster.samples).most_common(1)[0][0]
            if question in existing or cluster.pattern in existing_normalized:
                continue
            # 第 5 步：写入推荐项。answer 留空 —— 建议答案的生成方式需求未规定
            # （11.3 只规定审核时可用 edited_answer 覆盖），不擅自生成占位答案
            faq = Faq(
                question=question,
                answer=None,
                category=None,
                related_unit_id=self._most_related_unit(cluster),
                source_type=FaqSourceType.AUTO_MINED.value,
                status=FaqStatus.PENDING_REVIEW.value,
            )
            self._session.add(faq)
            existing.add(question)
            created.append(faq)

        # 第 6 步：统一提交
        self._session.commit()
        return created

    # ------------------------------------------------------------ 知识缺口

    def detect_knowledge_gaps(
        self, window_days: int = DEFAULT_WINDOW_DAYS
    ) -> list[KnowledgeGap]:
        """识别未命中知识库的提问并聚合为知识缺口（11.4）。

        判定口径：该条日志的 ``recalled_unit_ids_json`` 为空。
        按 11.4，满足条件即记录，**不设频次门槛**（频次只展示、不过滤）。

        同模式缺口不做多行记录，而是就地累加 ``ask_count``、刷新
        ``last_asked_at``、补齐样本；已在 ``resolved`` / ``ignored`` 的记录
        只刷新频次与时间，**不回退状态** —— 否则管理员刚补的知识又被自动打回未解决。

        :return: 本次涉及到的缺口记录列表。
        """
        # 第 1 步：取窗口期内的提问，筛出"没有任何召回候选"的那些
        clusters = self._cluster_logs(window_days, only_unhit=True)
        if not clusters:
            return []

        # 第 2 步：取回已有的缺口记录，按问题模式建索引
        existing = {
            gap.question_pattern: gap
            for gap in self._session.execute(select(KnowledgeGap)).scalars()
        }

        touched: list[KnowledgeGap] = []
        now = datetime.now()
        for cluster in clusters:
            # 第 3 步：已有记录则累加频次、刷新时间、补样本
            gap = existing.get(cluster.pattern)
            if gap is not None:
                gap.ask_count = int(gap.ask_count or 0) + len(cluster.samples)
                gap.last_asked_at = now
                gap.sample_questions_json = self._merge_samples(
                    gap.sample_questions_json, cluster.samples
                )
                touched.append(gap)
                continue
            # 第 4 步：新缺口，按 unresolved 初始态写入
            gap = KnowledgeGap(
                question_pattern=cluster.pattern,
                sample_questions_json=self._merge_samples(None, cluster.samples),
                ask_count=len(cluster.samples),
                last_asked_at=now,
                status=KnowledgeGapStatus.UNRESOLVED.value,
            )
            self._session.add(gap)
            existing[cluster.pattern] = gap
            touched.append(gap)

        # 第 5 步：统一提交
        self._session.commit()
        return touched

    # ------------------------------------------------------------ 内部

    def _cluster_logs(
        self, window_days: int, *, only_unhit: bool = False
    ) -> list[_Cluster]:
        """读取窗口期日志并聚类。

        :param only_unhit: 为 ``True`` 时只保留没有任何召回候选的记录（缺口口径）。
        :return: 簇列表，已按样本数降序（高频问题排前面）。
        """
        # 第 1 步：时间窗口条件。同时取"召回"与"授权"两列：
        # 召回列用于判定缺口（召回为空 = 没有达到阈值的内容支撑），
        # 授权列用于推关联知识单元（真正提供了答案的那个单元）
        start = datetime.now() - timedelta(days=max(int(window_days), 1))
        stmt = select(
            QaAccessLog.question,
            QaAccessLog.authorized_unit_ids_json,
            QaAccessLog.recalled_unit_ids_json,
        ).where(QaAccessLog.created_at >= start, QaAccessLog.question.isnot(None))
        rows = self._session.execute(stmt).all()

        # 第 2 步：过滤出有效提问；缺口口径下再筛掉"召回为空"之外的记录。
        #
        # **这里为什么看召回而不是授权**：用授权列会把两类记录误判成知识缺口 ——
        # (1) 用户因数据权限被拒（知识其实存在，是权限问题不是知识空白）；
        # (2) FAQ 缓存命中的轮次（命中缓存会跳过鉴权，授权列表必然为空）。
        # 两者都不该进知识缺口清单去让管理员补知识。
        pairs: list[tuple[str, list[int]]] = []
        for question, authorized_ids, recalled_ids in rows:
            if not normalize_question(question or ""):
                continue
            if only_unhit and self._to_ids(recalled_ids):
                continue
            pairs.append((question, self._to_ids(authorized_ids)))
        if not pairs:
            return []

        # 第 3 步：聚类。簇号由注入的函数给出，再按簇号聚合样本与命中单元
        labels = self._cluster_fn([question for question, _ in pairs])
        buckets: dict[int, _Cluster] = {}
        for (question, unit_ids), label in zip(pairs, labels, strict=True):
            cluster = buckets.get(label)
            if cluster is None:
                cluster = _Cluster(pattern=normalize_question(question))
                buckets[label] = cluster
            cluster.samples.append(question)
            cluster.units.update(unit_ids)

        # 第 4 步：按样本数降序返回，同数按模式名升序保证稳定
        return sorted(
            buckets.values(), key=lambda c: (-len(c.samples), c.pattern)
        )

    @staticmethod
    def _to_ids(raw) -> list[int]:
        """把 JSON 列的值规整成整数列表（脏数据直接跳过）。"""
        if not raw:
            return []
        ids: list[int] = []
        for item in raw:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                continue
        return ids

    @staticmethod
    def _most_related_unit(cluster: _Cluster) -> int | None:
        """推关联知识单元：取该簇内被命中次数最多的那个。

        8.7 的推荐列表要展示"关联知识单元"，11.3 未规定推导方式，
        这里用"该问题模式下最常命中的单元"这个最朴素的统计口径。
        """
        if not cluster.units:
            return None
        # most_common 同数时按插入顺序，结果稳定
        return cluster.units.most_common(1)[0][0]

    @staticmethod
    def _merge_samples(existing, new_samples: Sequence[str]) -> list[str]:
        """合并样本提问：去重 + 限量，避免 JSON 列无限膨胀。"""
        merged: list[str] = []
        for question in list(existing or []) + list(new_samples):
            # 去掉纯空白与重复项，保持首次出现的顺序
            if isinstance(question, str) and question.strip() and question not in merged:
                merged.append(question)
        return merged[:MAX_SAMPLE_QUESTIONS]
