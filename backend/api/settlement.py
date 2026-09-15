"""知识沉淀接口（8.7 + 第 14 章 #6 / #7 补齐）。

实现 8.7 的 3 个接口：

    GET  /api/settlement/faqs/recommendations   待审核 FAQ 推荐列表
    POST /api/settlement/faqs/{id}/review       审核（approve / reject）
    GET  /api/settlement/knowledge-gaps         知识缺口列表

并补齐 2.9.3 要求但 8.7 未列出的 4 个接口（第 14 章 #6 / #7 确认为"补充新建接口"）：

    GET  /api/settlement/faqs                        已发布 FAQ 库（分页）
    POST /api/settlement/faqs/{id}/offline           已发布 FAQ 下线
    POST /api/settlement/knowledge-gaps/{id}/resolve 缺口补全（关联或一键建档）
    POST /api/settlement/knowledge-gaps/{id}/ignore  缺口忽略

**审核通过会联动缓存**：服务层在 ``approve`` 时写入 FAQ 缓存（11.3），
驳回或下线时让缓存失效（5.8），接口层不需要关心这件事。

**一键建档的编排放在接口层**：11.4 说"补全知识单元后置 resolved"，
而"新建知识单元"属 5.3 的职责。这里先调知识单元管理服务建成单元，
再把 id 交给沉淀服务做状态流转 —— 不在沉淀服务里再造一条单元写入路径。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from core.config import get_settings
from core.container import Container, get_container
from core.db import get_session
from core.response import AppError, ok
from core.security import CurrentUser, require_permission
from schemas.requests import FaqReviewRequest, GapResolveRequest
from services.FAQ_cache_ervice.faq_cache import FaqCacheService
from services.knowledge_precipitation_and_mining_service.settlement import (
    SettlementService,
)
from services.knowledge_unit_management_service.knowledge import (
    KnowledgeUnitService,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/settlement", tags=["知识沉淀"])


def _faq_cache(
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> FaqCacheService:
    """FAQ 缓存服务依赖。

    向量化函数传 ``None``：审核发布时只建立精确匹配键，
    不为一条 FAQ 去调用外部模型接口 —— 语义匹配的向量在首次语义查询时按需补。
    """
    return FaqCacheService(session, container.cache, None)


def _service(
    session: Session = Depends(get_session),
    faq_cache: FaqCacheService = Depends(_faq_cache),
) -> SettlementService:
    """沉淀服务依赖。频次阈值取自配置（第 14 章 #8 确认值）。"""
    return SettlementService(
        session,
        faq_cache,
        min_frequency=get_settings().faq_min_frequency,
    )


# ---------------------------------------------------------------------- FAQ


@router.get("/faqs/recommendations", summary="待审核 FAQ 推荐列表")
def list_recommendations(
    _: CurrentUser = Depends(require_permission("menu:settlement")),
    service: SettlementService = Depends(_service),
) -> dict:
    """返回自动挖掘出的待审核 FAQ。

    每项含 ``id``、``question``、``frequency``（推荐频次）、``related_unit_id``、
    ``suggested_answer``。其中 ``suggested_answer`` 通常为 ``null`` ——
    需求未规定挖掘阶段如何生成建议答案（11.3 只规定审核时可用 ``edited_answer`` 覆盖）。

    **列表可能为空是正常的**：触发阈值由第 14 章 #8 定为 50 次，
    低频问题不会被推荐（见 ``FAQ_MIN_FREQUENCY``）。
    """
    return ok({"items": service.list_recommendations()})


@router.get("/faqs", summary="FAQ 库（默认只看已发布）")
def list_faqs(
    status: str | None = Query(
        default="published",
        description="状态过滤；传空串表示不限状态（前端\"全部\"标签页用）",
    ),
    keyword: str | None = Query(default=None, description="按标准问题模糊匹配"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: CurrentUser = Depends(require_permission("menu:settlement")),
    service: SettlementService = Depends(_service),
) -> dict:
    """分页查询 FAQ 库（第 14 章 #6 的落地），按命中次数降序。

    每项含 ``hit_count`` —— 2.9.3 要求已发布 FAQ 库展示命中次数。
    """
    total, items = service.list_faqs(
        status=status or None, keyword=keyword, page=page, page_size=page_size
    )
    return ok({"total": total, "items": items, "page": page, "page_size": page_size})


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


@router.post("/faqs/{id}/offline", summary="已发布 FAQ 下线")
def offline_faq(
    id: int = Path(ge=1),
    user: CurrentUser = Depends(require_permission("menu:settlement")),
    service: SettlementService = Depends(_service),
) -> dict:
    """让一条已发布 FAQ 下线（第 14 章 #6 的落地）。

    状态落到 ``rejected`` —— 2.9.7 的 ``status`` 只有三态，没有独立的"已下线"取值；
    同时**让缓存失效**，否则下线的意义（立刻不再命中）就不成立。
    """
    faq = service.offline_faq(id, reviewer_id=user.user_id)
    return ok(
        {
            "id": faq.id,
            "question": faq.question,
            "status": faq.status,
            "reviewed_at": faq.reviewed_at,
        }
    )


# ---------------------------------------------------------------------- 缺口


@router.get("/knowledge-gaps", summary="知识缺口列表")
def list_knowledge_gaps(
    _: CurrentUser = Depends(require_permission("menu:settlement")),
    service: SettlementService = Depends(_service),
) -> dict:
    """返回知识缺口，按提问频次降序。

    每项含 ``question_pattern``、``ask_count``、``last_asked_at``、``status``、
    ``resolved_unit_id``（已补全时对应的知识单元）与 ``sample_questions``。
    """
    return ok({"items": service.list_knowledge_gaps()})


@router.post("/knowledge-gaps/{id}/resolve", summary="缺口补全（关联或一键建档）")
def resolve_gap(
    payload: GapResolveRequest,
    id: int = Path(ge=1),
    user: CurrentUser = Depends(require_permission("menu:settlement")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
    service: SettlementService = Depends(_service),
) -> dict:
    """补全知识缺口（第 14 章 #7 / 11.4）。

    两种用法：

    * 传 ``unit_id`` → 直接关联已有单元；
    * 不传 ``unit_id`` → **一键建档**：用 ``title``（缺省为缺口的问题模式）作标题、
      ``content``（缺省为样本提问拼出的骨架）作正文，新建知识单元后关联。

    建档需要向量化，因此这里会校验 MinIO / Milvus 就绪。
    """
    unit_id = payload.unit_id
    created_unit_code: str | None = None

    # 第 1 步：没有指定已有单元时，先建档
    if unit_id is None:
        storage = container.object_storage
        vectors = container.vector_store
        if storage is None or vectors is None:
            raise AppError(
                "一键建档依赖的 MinIO 或 Milvus 未就绪，请检查 .env 配置与服务状态",
                code=503,
                http_status=503,
            )
        # 标题缺省用缺口的问题模式；正文缺省用样本提问拼骨架，便于管理员后续补齐
        unit_service = KnowledgeUnitService(session, storage, vectors)
        gap = _find_gap(service, id)
        unit = unit_service.create_unit(
            payload.title or gap["question_pattern"],
            payload.content or _draft_content(gap),
            category=payload.category,
            summary=f"由知识缺口补全创建（提问 {gap['ask_count']} 次）",
            creator_id=user.user_id,
        )
        unit_id = unit.id
        created_unit_code = unit.unit_code

    # 第 2 步：状态流转由沉淀服务负责
    gap_record = service.resolve_gap(id, int(unit_id))
    return ok(
        {
            "id": gap_record.id,
            "status": gap_record.status,
            "resolved_unit_id": gap_record.resolved_unit_id,
            "created_unit_code": created_unit_code,
        }
    )


@router.post("/knowledge-gaps/{id}/ignore", summary="缺口忽略")
def ignore_gap(
    id: int = Path(ge=1),
    _: CurrentUser = Depends(require_permission("menu:settlement")),
    service: SettlementService = Depends(_service),
) -> dict:
    """把无效缺口置为 ``ignored``（11.4），记录保留但不再纳入未解决清单。"""
    gap = service.ignore_gap(id)
    return ok({"id": gap.id, "status": gap.status})


# ---------------------------------------------------------------------- 内部


def _find_gap(service: SettlementService, gap_id: int) -> dict:
    """取一个缺口的结构化数据，找不到抛 404。

    走 ``list_knowledge_gaps`` 而不是新增一个"取单个缺口"的服务方法 ——
    缺口总量是"聚合后才有的少量记录"，全量取回再筛比再开一个查询入口更省事，
    也避免为了一个编排动作扩大服务层的公开面。
    """
    for gap in service.list_knowledge_gaps():
        if gap["id"] == gap_id:
            return gap
    raise AppError(f"知识缺口不存在：id={gap_id}", code=404, http_status=404)


def _draft_content(gap: dict) -> str:
    """用缺口的样本提问拼出正文骨架。

    不是替管理员写答案 —— 只是把"大家在问什么"落到正文里，
    让新建的单元先有内容可编辑，而不是一个空壳。
    """
    samples = gap.get("sample_questions") or []
    lines = [f"该知识单元由知识缺口自动创建，累计被提问 {gap.get('ask_count') or 0} 次。", ""]
    if samples:
        lines.append("历史提问样本：")
        lines.extend(f"- {item}" for item in samples)
        lines.append("")
    lines.append("（请在此补充标准答案与相关说明。）")
    return "\n".join(lines)
