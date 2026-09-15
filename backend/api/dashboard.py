"""数据看板接口（8.6）。

实现 8.6 的 4 个接口：

    GET /api/dashboard/metrics                     核心指标
    GET /api/dashboard/rankings/questions          常见问题 TOP 榜
    GET /api/dashboard/rankings/units              知识单元热度 TOP 榜
    GET /api/dashboard/stats/tokens                Token 与响应时间趋势

**读缓存的顺序**：先看 4.6 的定时聚合子图有没有把快照写进缓存，命中就直接返回；
未命中（子图还没跑过）就回源 SQL 现算 —— 这样看板不依赖定时任务也能出数，
而定时任务跑过之后又能省掉重复聚合。

**未扩字段**：8.6 没有"今日实时指标"这类字段，因此服务层的 ``daily_realtime``
不并入任何响应；它只供 4.6 的定时聚合子图与运维排查使用，不对前端暴露。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.container import Container, get_container
from core.db import get_session
from core.response import AppError, ok
from core.security import CurrentUser, require_permission
from graph.metrics_graph import (
    CACHE_KEY_METRICS,
    CACHE_KEY_TOP_QUESTIONS,
    CACHE_KEY_TOP_UNITS,
    CACHE_KEY_TOKEN_TREND,
)
from services.data_dashboard_statistics_service.aggregation import (
    GRANULARITY_DAY,
    GRANULARITY_WEEK,
    collect_metrics,
    collect_token_trend,
    rank_questions,
    rank_units,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/dashboard", tags=["数据看板"])

# 榜单默认长度，与聚合子图保持一致
DEFAULT_TOP_N = 10


def _cached(container: Container, key: str):
    """读聚合快照；没有或格式坏了都返回 ``None``（回源 SQL）。"""
    cache = container.cache
    if cache is None:
        return None
    try:
        raw = cache.get(key)
    except Exception:  # noqa: BLE001 - 缓存异常必须回源，不能把看板打挂
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


@router.get("/metrics", summary="核心指标")
def metrics(
    days: int | None = Query(default=None, ge=1, description="只统计最近 N 天，不传为全量"),
    _: CurrentUser = Depends(require_permission("menu:dashboard")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """访问总次数、独立用户数、知识单元数、总 Token、平均响应时间（11.2 口径）。

    只有"全量"口径才读缓存快照（快照就是全量算的）；带 ``days`` 时直接回源。
    """
    # 第 1 步：全量查询优先读聚合子图预热的快照
    if days is None:
        snapshot = _cached(container, CACHE_KEY_METRICS)
        if snapshot is not None:
            return ok(snapshot)
    # 第 2 步：回源 SQL 现算
    return ok(collect_metrics(session, days))


@router.get("/rankings/questions", summary="常见问题 TOP 榜")
def ranking_questions(
    top_n: int = Query(default=DEFAULT_TOP_N, ge=1, le=100),
    days: int | None = Query(default=None, ge=1),
    _: CurrentUser = Depends(require_permission("menu:dashboard")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """按提问归一化分组后计数降序（11.2）。"""
    # 第 1 步：默认参数下可用预热快照
    if days is None and top_n == DEFAULT_TOP_N:
        snapshot = _cached(container, CACHE_KEY_TOP_QUESTIONS)
        if snapshot is not None:
            return ok({"items": snapshot})
    # 第 2 步：回源
    return ok({"items": rank_questions(session, top_n, days)})


@router.get("/rankings/units", summary="最常访问知识单元 TOP 榜")
def ranking_units(
    top_n: int = Query(default=DEFAULT_TOP_N, ge=1, le=100),
    days: int | None = Query(default=None, ge=1),
    field: str = Query(default="recalled", pattern="^(recalled|authorized)$"),
    _: CurrentUser = Depends(require_permission("menu:dashboard")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """展开单元 id JSON 后计数降序（11.2）。

    ``field`` 为统计口径（``recalled`` 召回 / ``authorized`` 已授权），
    该口径属第 14 章【待确认】，因此做成参数而不是写死。
    """
    # 第 1 步：默认口径与默认长度时可用预热快照
    if days is None and top_n == DEFAULT_TOP_N and field == "recalled":
        snapshot = _cached(container, CACHE_KEY_TOP_UNITS)
        if snapshot is not None:
            return ok({"items": snapshot})
    # 第 2 步：回源
    return ok({"items": rank_units(session, top_n, days, field)})


@router.get("/stats/tokens", summary="Token 消耗与响应时间趋势")
def stats_tokens(
    granularity: str = Query(default=GRANULARITY_DAY, pattern="^(day|week)$"),
    _: CurrentUser = Depends(require_permission("menu:dashboard")),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> dict:
    """按日或按周汇总 Token 与平均响应时间（8.6 只声明 day | week）。"""
    # 第 1 步：优先读快照（子图两种粒度都预热了）
    snapshot = _cached(container, CACHE_KEY_TOKEN_TREND)
    if isinstance(snapshot, dict) and granularity in snapshot:
        return ok({"granularity": granularity, "items": snapshot[granularity]})
    # 第 2 步：回源
    if granularity not in (GRANULARITY_DAY, GRANULARITY_WEEK):
        raise AppError(f"不支持的粒度：{granularity}", code=422, http_status=422)
    return ok({"granularity": granularity, "items": collect_token_trend(session, granularity)})
