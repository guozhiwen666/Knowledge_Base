"""知识沉淀挖掘服务（2.9.6 / 文档 5.7）门面。

职责与实现落点：

    定时挖掘入口（4.5 子图）        -> :meth:`SettlementService.run`
    待审核 FAQ 推荐列表（8.7）      -> :meth:`SettlementService.list_recommendations`
    FAQ 审核流转（8.7）             -> :meth:`SettlementService.review_faq`
    知识缺口列表（8.7）             -> :meth:`SettlementService.list_knowledge_gaps`
    挖掘算法本身                    -> :mod:`knowledge_precipitation_and_mining_service.mining`

**审核通过为什么要联动缓存**：11.3 规定 ``approve`` 时除了改状态，还要"写缓存"，
缓存写入属 5.8 的 FAQ 缓存服务职责，因此本服务持有其引用并在通过时调用；
驳回（或重新审核）时同样要让缓存失效（5.8 的"失效重建"）。

**审核写入顺序**：先改状态并 flush → 写缓存 → 提交。
万一缓存写入失败，回滚数据库状态并主动让缓存失效，避免出现
"缓存里已经是新答案、库里还是待审核"这种两边不一致的状态。

**刻意不做的事**：

* **建议答案生成**：11.3 只规定了审核时可用 ``edited_answer`` 覆盖答案，
  没有规定挖掘阶段如何生成建议答案，因此推荐项的 ``answer`` 留空，
  由管理员在审核界面填写；列表里的 ``suggested_answer`` 因此可能是 ``null``；
* **缺口状态流转（"一键创建关联知识单元补全"）**：11.4 写了 ``unresolved →
  resolved`` 并回写 ``resolved_unit_id``，但 2.9.3 所需的对应接口未在 2.9.8 中列出
  （第 14 章【待确认】）。没有对外入口就不实现无处可调的方法。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Faq, KnowledgeGap, QaAccessLog
from models.enums import FaqSourceType, FaqStatus
from services.FAQ_cache_ervice.faq_cache import FaqCacheService
from services.knowledge_precipitation_and_mining_service.mining import (
    DEFAULT_MIN_FREQUENCY,
    DEFAULT_WINDOW_DAYS,
    ClusterFn,
    QuestionMiner,
    default_cluster,
    normalize_question,
)

__all__ = ["InvalidReviewAction", "SettlementService"]

# 审核动作取值（8.7 的 action 字段）
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"


class InvalidReviewAction(ValueError):
    """审核动作非法。8.7 只定义了 ``approve`` 与 ``reject`` 两种。"""


class SettlementService:
    """知识沉淀挖掘服务。

    依赖注入：会话与 FAQ 缓存服务由外部传入；挖掘器在内部构造，
    与 ``KnowledgeUnitService`` 内部构造导入流水线的做法一致。
    """

    def __init__(
        self,
        session: Session,
        faq_cache: FaqCacheService,
        *,
        cluster_fn: ClusterFn = default_cluster,
        min_frequency: int = DEFAULT_MIN_FREQUENCY,
    ) -> None:
        """:param session: SQLAlchemy 会话。
        :param faq_cache: FAQ 缓存服务（5.8），审核通过 / 驳回时联动。
        :param cluster_fn: 聚类函数，透传给挖掘器。
        :param min_frequency: 推荐 FAQ 的频次阈值，透传给挖掘器。
        """
        self._session = session
        self._faq_cache = faq_cache
        self._miner = QuestionMiner(
            session, cluster_fn=cluster_fn, min_frequency=min_frequency
        )

    # ------------------------------------------------------------------ 挖掘

    def mine_faq_recommendations(self, window_days: int = DEFAULT_WINDOW_DAYS) -> list:
        """挖掘高频相似问题并生成待审核 FAQ 推荐项（5.7 第 1 条职责）。

        与 :meth:`run` 的区别：本方法只做挖掘，便于 4.5 子图把它拆成独立节点。
        """
        return self._miner.mine_faq_recommendations(window_days)

    def detect_knowledge_gaps(self, window_days: int = DEFAULT_WINDOW_DAYS) -> list:
        """识别未命中知识库的提问并聚合为知识缺口（5.7 第 2 条职责）。"""
        return self._miner.detect_knowledge_gaps(window_days)

    def run(self, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
        """执行一轮完整沉淀挖掘（4.5 子图的执行体，由定时任务调用）。

        先挖 FAQ 推荐项，再识别知识缺口 —— 顺序与 4.5 的流程一致。

        :return: ``{"faq_recommendations": n, "knowledge_gaps": m}``，便于定时任务打点。
        """
        # 第 1 步：高频相似问题 → 待审核 FAQ 推荐项
        recommendations = self._miner.mine_faq_recommendations(window_days)
        # 第 2 步：未命中提问 → 知识缺口
        gaps = self._miner.detect_knowledge_gaps(window_days)
        # 第 3 步：返回计数（不返回 ORM 对象，避免定时任务持有过期会话对象）
        return {
            "faq_recommendations": len(recommendations),
            "knowledge_gaps": len(gaps),
        }

    # ------------------------------------------------------------------ 查询

    def list_recommendations(self) -> list[dict]:
        """待审核 FAQ 推荐列表（``GET /api/settlement/faqs/recommendations``）。

        只取 ``source_type = auto_mined`` 且 ``status = pending_review`` 的记录 ——
        8.7 的语义是"系统从历史对话挖掘的高频问题推荐列表"。

        **"推荐频次"是现算的**：``faqs`` 表没有频次列（2.9.7），
        因此这里回 ``qa_access_logs`` 按归一化问题统计后匹配。
        这是需求与表结构对不上的地方，已在模块说明中登记。
        """
        # 第 1 步：取待审核的自动推荐项
        faqs = (
            self._session.execute(
                select(Faq)
                .where(
                    Faq.source_type == FaqSourceType.AUTO_MINED.value,
                    Faq.status == FaqStatus.PENDING_REVIEW.value,
                )
                .order_by(Faq.id)
            )
            .scalars()
            .all()
        )
        if not faqs:
            return []

        # 第 2 步：一次统计全部历史提问的归一化频次，避免按条查库
        frequencies = self._question_frequencies()

        # 第 3 步：组装 8.7 的字段结构
        return [
            {
                "id": faq.id,
                "question": faq.question,
                "frequency": frequencies.get(normalize_question(faq.question), 0),
                "related_unit_id": faq.related_unit_id,
                # 挖掘阶段不生成答案，因此通常为 null，由审核时人工填写
                "suggested_answer": faq.answer,
            }
            for faq in faqs
        ]

    def list_knowledge_gaps(self) -> list[dict]:
        """知识缺口列表（``GET /api/settlement/knowledge-gaps``）。

        按提问频次降序（7.4 的 ``idx_gap_count`` 正是服务这个查询）。
        """
        rows = (
            self._session.execute(
                select(KnowledgeGap).order_by(
                    KnowledgeGap.ask_count.desc(), KnowledgeGap.id
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": gap.id,
                "question_pattern": gap.question_pattern,
                "ask_count": gap.ask_count,
                "last_asked_at": gap.last_asked_at,
                "status": gap.status,
            }
            for gap in rows
        ]

    # ------------------------------------------------------------------ 审核

    def review_faq(
        self,
        faq_id: int,
        action: str,
        edited_answer: str | None = None,
        reviewer_id: int | None = None,
    ) -> Faq:
        """审核 FAQ 推荐项（``POST /api/settlement/faqs/{id}/review``）。

        流转规则（11.3）：

        * ``approve`` → ``published``，写入 ``reviewer_id`` / ``reviewed_at``，
          并用 ``edited_answer`` 覆盖答案，最后写入缓存；
        * ``reject`` → ``rejected``，并让缓存失效（该 FAQ 若曾发布过）。

        :raise LookupError: FAQ 不存在。
        :raise InvalidReviewAction: ``action`` 不是 approve / reject。
        """
        # 第 1 步：校验动作取值
        if action not in (ACTION_APPROVE, ACTION_REJECT):
            raise InvalidReviewAction(f"不支持的审核动作：{action}")
        # 第 2 步：取 FAQ，不存在直接抛错
        faq = self._session.get(Faq, faq_id)
        if faq is None:
            raise LookupError(f"FAQ 不存在：id={faq_id}")

        # 第 3 步：按动作改状态；两分支都要记录审核人与审核时间
        faq.reviewer_id = reviewer_id
        faq.reviewed_at = datetime.now()
        if action == ACTION_APPROVE:
            faq.status = FaqStatus.PUBLISHED.value
            # 管理员编辑过的答案优先，未编辑则沿用原答案
            if edited_answer:
                faq.answer = edited_answer
        else:
            faq.status = FaqStatus.REJECTED.value

        # 第 4 步：flush 让状态落到当前事务，但先不提交 ——
        # 缓存写入要参与同一次"要么都成、要么都不成"的判定
        self._session.flush()
        try:
            if action == ACTION_APPROVE:
                self._faq_cache.publish(faq)
            else:
                # 驳回也可能是对已发布 FAQ 的重新审核，缓存必须失效重建（5.8）
                self._faq_cache.invalidate(faq)
        except Exception:
            # 缓存写失败则回滚状态，并清理可能写入了一半的缓存，避免两边不一致
            self._session.rollback()
            self._faq_cache.invalidate(faq)
            raise

        # 第 5 步：提交
        self._session.commit()
        return faq

    # ------------------------------------------------------------------ 内部

    def _question_frequencies(self) -> dict[str, int]:
        """统计历史提问的归一化频次。

        只取 ``question`` 一列，避免把 LONGTEXT 的答案一起拉回来。
        数据量大时应改为定时物化（例如在 ``faqs`` 上加频次列），
        但那是表结构变更，超出当前范围。
        """
        counter: Counter = Counter()
        for question in self._session.execute(
            select(QaAccessLog.question).where(QaAccessLog.question.isnot(None))
        ).scalars():
            key = normalize_question(question or "")
            if key:
                counter[key] += 1
        return dict(counter)
