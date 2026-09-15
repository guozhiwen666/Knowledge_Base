"""知识沉淀接口（8.7）。

实现 8.7 的 3 个接口：

    GET  /api/settlement/faqs/recommendations   待审核 FAQ 推荐列表
    POST /api/settlement/faqs/{id}/review       审核（approve / reject）
    GET  /api/settlement/knowledge-gaps         知识缺口列表

**审核通过会联动缓存**：服务层在 ``approve`` 时写入 FAQ 缓存（11.3），
驳回时让缓存失效（5.8），接口层不需要关心这件事。

**未实现的接口**：8.7 没有"已发布 FAQ 库查询"与"知识缺口一键建档"接口
（第 14 章【待确认】），因此这里不提供。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from core.container import Container, get_container
from core.db import get_session
from core.response import ok
from core.security import CurrentUser, require_permission
from schemas.requests import FaqReviewRequest
from services.FAQ_cache_ervice.faq_cache import FaqCacheService
from services.knowledge_precipitation_and_mining_service.settlement import (
    SettlementService,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/settlement", tags=["知识沉淀"])


def _service(
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> SettlementService:
    """沉淀服务依赖。

    缓存的向量化函数传 ``None``：审核发布时只建立精确匹配键，
    不为一条 FAQ 去调用外部模型接口 —— 语义匹配的向量在首次语义查询时按需补。
    """
    cache = container.cache
    faq_cache = FaqCacheService(session, cache, None)
    return SettlementService(session, faq_cache)


@router.get("/faqs/recommendations", summary="待审核 FAQ 推荐列表")
def list_recommendations(
    _: CurrentUser = Depends(require_permission("menu:settlement")),
    service: SettlementService = Depends(_service),
) -> dict:
    """返回自动挖掘出的待审核 FAQ。

    每项含 ``id``、``question``、``frequency``（推荐频次）、``related_unit_id``、
    ``suggested_answer``。其中 ``suggested_answer`` 通常为 ``null`` ——
    需求未规定挖掘阶段如何生成建议答案（11.3 只规定审核时可用 ``edited_answer`` 覆盖）。
    """
    return ok({"items": service.list_recommendations()})


@router.post("/faqs/{id}/review", summary="审核 FAQ（通过 / 驳回）")
def review_faq(
    payload: FaqReviewRequest,
    id: int = Path(ge=1),
    user: CurrentUser = Depends(require_permission("menu:settlement")),
    service: SettlementService = Depends(_service),
) -> dict:
    """审核推荐项。

    ``approve`` → 置 ``published``、记录审核人、用 ``edited_answer`` 覆盖答案并写入缓存；
    ``reject`` → 置 ``rejected`` 并让缓存失效。
    """
    # 第 1 步：服务层完成状态流转与缓存联动
    faq = service.review_faq(
        id,
        payload.action,
        edited_answer=payload.edited_answer,
        reviewer_id=user.user_id,
    )
    # 第 2 步：返回审核后的关键状态，便于前端就地刷新列表
    return ok(
        {
            "id": faq.id,
            "question": faq.question,
            "status": faq.status,
            "reviewer_id": faq.reviewer_id,
            "reviewed_at": faq.reviewed_at,
        }
    )


@router.get("/knowledge-gaps", summary="知识缺口列表")
def list_knowledge_gaps(
    _: CurrentUser = Depends(require_permission("menu:settlement")),
    service: SettlementService = Depends(_service),
) -> dict:
    """返回知识缺口，按提问频次降序。

    每项含 ``question_pattern``、``ask_count``、``last_asked_at``、``status``。
    """
    return ok({"items": service.list_knowledge_gaps()})
