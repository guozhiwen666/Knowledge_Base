"""知识沉淀挖掘服务 · 已发布 FAQ 库（第 14 章 #6 的落地）。

2.9.3 要求知识沉淀管理页展示"已发布 FAQ 库（含命中次数）"，
但 8.7 只给了"待审核推荐列表 / 审核 / 知识缺口列表"三个接口。
这里给出查询与下线实现。

**下线为什么复用 ``rejected``**：2.9.7 的 ``faqs.status`` 只有
``pending_review`` / ``published`` / ``rejected`` 三态，没有独立的"已下线"取值。
新增一个取值属于表结构与契约变更，不在"补齐接口"的范围内，
因此下线落到 ``rejected``，并**同时让缓存失效** —— 下线的意义就是"立刻不再命中"，
只改库不管缓存等于没下线。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Faq
from models.enums import FaqStatus, FaqSourceType
from services.FAQ_cache_ervice.faq_cache import FaqCacheService

__all__ = ["FaqLibraryService"]


class FaqLibraryService:
    """已发布 FAQ 的查询与下线。

    依赖注入：会话与 FAQ 缓存服务由外部传入 —— 下线必须联动缓存，
    而缓存的失效规则属 5.8 的职责，不在这里重写一遍。
    事务边界：``offline`` 自行 commit。
    """

    def __init__(self, session: Session, faq_cache: FaqCacheService) -> None:
        """:param session: SQLAlchemy 会话。
        :param faq_cache: FAQ 缓存服务（5.8），下线时联动失效。
        """
        self._session = session
        self._faq_cache = faq_cache

    # ------------------------------------------------------------------ 读

    def list_faqs(
        self,
        *,
        status: str | None = FaqStatus.PUBLISHED.value,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        """分页查询 FAQ 库（``GET /api/settlement/faqs``）。

        :param status: 状态过滤，默认只看 ``published``；
            传 ``None`` 表示不限状态（前端"全部"标签页用）。
        :param keyword: 按标准问题模糊过滤。
        :return: ``(total, items)``；每项含 ``id``、``question``、``answer``、
            ``category``、``related_unit_id``、``source_type``、``status``、
            ``hit_count``、``reviewer_id``、``reviewed_at``、``created_at``。
        """
        # 第 1 步：入参兜底
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)

        # 第 2 步：拼装过滤条件
        conditions = []
        if status:
            conditions.append(Faq.status == status)
        if keyword:
            conditions.append(Faq.question.like(f"%{keyword}%"))

        # 第 3 步：总数
        count_stmt = select(func.count()).select_from(Faq)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = self._session.execute(count_stmt).scalar_one()

        # 第 4 步：当页数据。命中次数降序 —— 已发布 FAQ 库最有价值的排序
        # 就是"哪些问答真正在被用"
        list_stmt = select(Faq)
        if conditions:
            list_stmt = list_stmt.where(*conditions)
        faqs = (
            self._session.execute(
                list_stmt.order_by(Faq.hit_count.desc(), Faq.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return total, [self._to_dict(faq) for faq in faqs]

    # ------------------------------------------------------------------ 写

    def offline(self, faq_id: int, reviewer_id: int | None = None) -> Faq:
        """让一条已发布 FAQ 下线（``POST /api/settlement/faqs/{id}/offline``）。

        流转：``published`` → ``rejected``，同时记录操作人与时间。
        缓存写入顺序与 ``SettlementService.review_faq`` 一致：
        **先改状态并 flush → 清缓存 → 提交**，缓存清理失败就回滚状态，
        避免出现"库里已下线、缓存还在命中"的两边不一致。

        :raise LookupError: FAQ 不存在。
        :raise ValueError: 该 FAQ 当前不是已发布状态（重复下线没有意义）。
        """
        # 第 1 步：取 FAQ
        faq = self._session.get(Faq, faq_id)
        if faq is None:
            raise LookupError(f"FAQ 不存在：id={faq_id}")
        # 第 2 步：状态校验。只允许对已发布的下线，挡住误操作
        if faq.status != FaqStatus.PUBLISHED.value:
            raise ValueError(f"该 FAQ 当前状态为 {faq.status}，只有已发布的才能下线")
        # 第 3 步：改状态
        faq.status = FaqStatus.REJECTED.value
        faq.reviewer_id = reviewer_id
        faq.reviewed_at = datetime.now()
        # 第 4 步：flush 让状态进入当前事务，缓存清理要参与同一次成败判定
        self._session.flush()
        try:
            self._faq_cache.invalidate(faq)
        except Exception:
            self._session.rollback()
            raise
        # 第 5 步：提交
        self._session.commit()
        return faq

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _to_dict(faq: Faq) -> dict:
        """把 FAQ 行转成响应结构。字段名与 8.7 的风格保持一致（``source_type`` 用原值）。"""
        return {
            "id": faq.id,
            "question": faq.question,
            "answer": faq.answer,
            "category": faq.category,
            "related_unit_id": faq.related_unit_id,
            "source_type": faq.source_type or FaqSourceType.MANUAL.value,
            "status": faq.status,
            "hit_count": int(faq.hit_count or 0),
            "reviewer_id": faq.reviewer_id,
            "reviewed_at": faq.reviewed_at,
            "created_at": faq.created_at,
        }
