"""看板聚合子图 `metrics_graph`（对应文档 4.6）。

4.6 的五个步骤：

    1) 按 created_at 日期分组，汇总日访问次数、独立用户数
    2) 由提问内容聚合常见问题榜
    3) 由 recalled_unit_ids_json 展开聚合知识单元热度榜
    4) 汇总 token 与响应时间
    5) 结果写入缓存供 GET /api/dashboard/* 直读

**为什么要有这张图**：4.6 的硬约束是"主链路只落一条原始日志，聚合放在查询侧
与定时子图"。定时子图的价值在于把聚合结果**预热到缓存**，
接口层命中缓存就能直接返回，避免每次开看板都扫一遍日志表。

**缓存键集中在这里定义**，接口层 import 这几个常量来读 —— 键名只写一处。

缓存的读侧是"命中即用、未命中回源 SQL"（接口层实现），因此本子图没跑过时
看板依然可用，只是每次都要现算。
"""

from __future__ import annotations

import json
import time
from typing import Annotated, TypedDict

import operator
from langgraph.graph import END, START, StateGraph

from services.data_dashboard_statistics_service.aggregation import (
    GRANULARITY_DAY,
    GRANULARITY_WEEK,
    collect_metrics,
    collect_token_trend,
    rank_questions,
    rank_units,
)

__all__ = [
    "CACHE_KEY_METRICS",
    "CACHE_KEY_TOP_QUESTIONS",
    "CACHE_KEY_TOP_UNITS",
    "CACHE_KEY_TOKEN_TREND",
    "MetricsState",
    "build_metrics_graph",
    "refresh_dashboard_cache",
]

# 缓存键。接口层 import 这些常量读取，禁止另外拼字符串
CACHE_KEY_METRICS = "kb:dashboard:snapshot:metrics"
CACHE_KEY_TOP_QUESTIONS = "kb:dashboard:snapshot:top_questions"
CACHE_KEY_TOP_UNITS = "kb:dashboard:snapshot:top_units"
CACHE_KEY_TOKEN_TREND = "kb:dashboard:snapshot:token_trend"

# 榜单长度。8.6 未规定 TOP 长度，集中在此
DEFAULT_TOP_N = 10


class MetricsState(TypedDict, total=False):
    """看板聚合子图状态。"""

    top_n: int
    snapshot: dict
    elapsed_ms: int
    errors: Annotated[list[dict], operator.add]


def _aggregate(session, cache, top_n: int) -> dict:
    """执行 4.6 的五步聚合，并写入缓存。

    :return: 本次聚合出的快照（同时已写进缓存）。
    """
    # 第 1、4 步：核心指标（访问次数 / 独立人数 / 单元数 / token / 平均耗时）
    metrics = collect_metrics(session, None)

    # 第 2 步：常见问题榜
    questions = rank_questions(session, top_n, None)

    # 第 3 步：知识单元热度榜。口径默认按召回统计（11.2 注明该口径属待确认项）
    units = rank_units(session, top_n, None, field="recalled")

    # 第 4 步：token 与响应时间趋势（日 / 周两种粒度都预热，供前端切换时直接读）
    trend_day = collect_token_trend(session, GRANULARITY_DAY)
    trend_week = collect_token_trend(session, GRANULARITY_WEEK)

    snapshot = {
        "metrics": metrics,
        "top_questions": questions,
        "top_units": units,
        "token_trend": {"day": trend_day, "week": trend_week},
    }

    # 第 5 步：写入缓存。缓存不可用（降级为内存或未注入）时静默跳过，
    # 接口层回源 SQL 一样能出数，不影响正确性
    if cache is not None:
        _write_cache(cache, CACHE_KEY_METRICS, metrics)
        _write_cache(cache, CACHE_KEY_TOP_QUESTIONS, questions)
        _write_cache(cache, CACHE_KEY_TOP_UNITS, units)
        _write_cache(cache, CACHE_KEY_TOKEN_TREND, snapshot["token_trend"])

    return snapshot


def _write_cache(cache, key: str, payload) -> None:
    """写缓存快照；任何异常都吞掉（缓存只是加速，不是数据源）。"""
    try:
        cache.set(key, json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        return


def build_metrics_graph(session, cache):
    """构造并编译看板聚合子图。

    :param session: 数据库会话（由定时任务创建并在本轮内复用）。
    :param cache: 缓存客户端（可传 ``None``）。
    :return: 已编译的图；``.invoke({"top_n": 10})`` 跑一轮。
    """

    def _node(state: dict) -> dict:
        started = time.monotonic()
        top_n = int(state.get("top_n") or DEFAULT_TOP_N)
        # 聚合失败不该让定时任务崩掉，记进 errors 供调度器打点
        try:
            snapshot = _aggregate(session, cache, top_n)
        except Exception as exc:  # noqa: BLE001
            return {
                "errors": [{"node": "metrics_graph", "error": str(exc)}],
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        return {
            "snapshot": snapshot,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    # 单节点图：4.6 的五步是一条直线，不需要分支
    graph = StateGraph(MetricsState)
    graph.add_node("aggregate", _node)
    graph.add_edge(START, "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()


def refresh_dashboard_cache(session, cache, *, top_n: int = DEFAULT_TOP_N) -> dict:
    """跑一轮看板聚合，返回结果（同时已刷新缓存）。供定时任务调用。"""
    app = build_metrics_graph(session, cache)
    final = app.invoke({"top_n": int(top_n), "errors": []})
    return {
        "snapshot": final.get("snapshot") or {},
        "elapsed_ms": int(final.get("elapsed_ms") or 0),
        "errors": final.get("errors", []),
    }
