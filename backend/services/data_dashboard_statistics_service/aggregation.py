"""数据看板统计服务 · 聚合查询（2.9.6 / 文档 5.6 的"聚合"部分）。

对应 4.6 的聚合子图口径与 11.2 的聚合口径表，与 :mod:`dashboard` 的写入侧分开：

    :mod:`dashboard`    —— 在线写入（落日志、实时计数）
    :mod:`aggregation`  —— 读侧聚合（指标、榜单、趋势）

**为什么这些函数是模块级函数而不是类方法**：它们是"给一个会话 + 参数、
返回聚合结果"的纯查询过程，没有任何需要持有的状态。项目里已有的模块级函数
（``hash_password`` / ``normalize_question`` 等）都是同一类东西，
保持这个写法比为了形式统一再造一个只包一层 ``self._session`` 的类更清楚。

**聚合为什么全在 SQL 侧**：4.6 明确"主链路只做一件事：落一条原始日志，
聚合全部放在查询侧与定时子图，避免在线写放大"。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from models import KnowledgeUnit, QaAccessLog

__all__ = [
    "GRANULARITY_DAY",
    "GRANULARITY_WEEK",
    "collect_metrics",
    "rank_questions",
    "rank_units",
    "collect_token_trend",
]

# 趋势图支持的粒度，对应 8.6 的 ``granularity=day|week``
GRANULARITY_DAY = "day"
GRANULARITY_WEEK = "week"


def _time_condition(days: int | None):
    """构造"最近 N 天"的过滤条件；``days`` 为空返回 ``None`` 表示全量。

    11.2 在访问次数口径处注明"可加时间范围过滤"，故这里允许传天数。
    """
    if not days or days <= 0:
        return None
    return QaAccessLog.created_at >= datetime.now() - timedelta(days=int(days))


def _unit_ids_column(field: str):
    """把热度榜的统计口径映射到实际的 JSON 列。

    :raise ValueError: 口径非法。11.2 注明该口径属第 14 章【待确认】，故做成可切换。
    """
    if field == "recalled":
        return QaAccessLog.recalled_unit_ids_json
    if field == "authorized":
        return QaAccessLog.authorized_unit_ids_json
    raise ValueError(f"不支持的口径：{field}")


def _iter_unit_ids(raw: Iterable | None) -> Iterable[int]:
    """把 JSON 列的值规整成整数迭代器；脏数据跳过，不让一条坏记录打挂整张榜。"""
    if not raw:
        return []
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _load_titles(session: Session, unit_ids: Sequence[int]) -> dict[int, str]:
    """批量取知识单元标题，返回 ``{id: title}``。只有 id 的榜单没法看。"""
    if not unit_ids:
        return {}
    rows = session.execute(
        select(KnowledgeUnit.id, KnowledgeUnit.title).where(
            KnowledgeUnit.id.in_(list(unit_ids))
        )
    ).all()
    return {row[0]: (row[1] or "") for row in rows}


def collect_metrics(session: Session, days: int | None = None) -> dict:
    """核心指标（``GET /api/dashboard/metrics``，口径见 11.2）。

    :param days: 只统计最近 N 天；不传为全量。
    :return: 字段名与 8.6 一一对应。
    """
    # 第 1 步：一次查出访问次数、独立用户数、Token 总量、平均耗时
    log_stmt = select(
        func.count(QaAccessLog.id),
        func.count(distinct(QaAccessLog.user_id)),
        func.sum(QaAccessLog.total_tokens),
        func.avg(QaAccessLog.response_time_ms),
    )
    condition = _time_condition(days)
    if condition is not None:
        log_stmt = log_stmt.where(condition)
    access_count, unique_users, total_tokens, avg_time = session.execute(log_stmt).one()

    # 第 2 步：知识单元总数单独查 knowledge_units（11.2 口径）
    unit_count = session.execute(
        select(func.count()).select_from(KnowledgeUnit)
    ).scalar_one()

    # 第 3 步：无数据时 SUM/AVG 返回 None，统一兜成 0，避免前端拿到 null
    return {
        "total_access_count": int(access_count or 0),
        "unique_user_count": int(unique_users or 0),
        "knowledge_unit_count": int(unit_count or 0),
        "total_tokens": int(total_tokens or 0),
        "avg_response_time_ms": round(float(avg_time or 0.0), 2),
    }


def rank_questions(
    session: Session, top_n: int = 10, days: int | None = None
) -> list[dict]:
    """常见问题 TOP 榜（``GET /api/dashboard/rankings/questions``）。

    口径（11.2）：按 ``question`` 归一化分组后计数降序。
    归一化取"去首尾空白 + 转小写"，把大小写与空格差异合并成同一个问题。
    """
    # 第 1 步：构造归一化表达式，select 与 group_by 复用同一个对象
    normalized = func.lower(func.trim(QaAccessLog.question)).label("question")
    count_expr = func.count(QaAccessLog.id).label("ask_count")

    # 第 2 步：分组统计，排除空提问
    stmt = (
        select(normalized, count_expr)
        .where(QaAccessLog.question.isnot(None))
        .group_by(normalized)
        .order_by(count_expr.desc())
        .limit(max(int(top_n), 1))
    )
    condition = _time_condition(days)
    if condition is not None:
        stmt = stmt.where(condition)

    # 第 3 步：转成 8.6 的 items 结构
    return [
        {"question": row[0], "ask_count": int(row[1])}
        for row in session.execute(stmt).all()
    ]


def rank_units(
    session: Session,
    top_n: int = 10,
    days: int | None = None,
    field: str = "recalled",
) -> list[dict]:
    """知识单元热度 TOP 榜（``GET /api/dashboard/rankings/units``）。

    **JSON 展开为什么在 Python 里做**：11.2 要求展开 ``*_unit_ids_json`` 后再计数，
    MySQL 侧要用 ``JSON_TABLE`` 才能一次算完，但那会把 SQL 钉死在 MySQL 8.0+，
    语句也会难读到没法维护。这里改为取回 JSON 列后在内存展开 ——
    牺牲少量内存，换取可读与可测；数据量显著增大时再考虑下推。

    :param field: 统计口径。**第 14 章 #11 已确认为 ``recalled``**（召回口径，
        涵盖"想找但没看到"的那部分热度）；``authorized`` 仅保留为可切换项。
    """
    # 第 1 步：只取目标列，减少不必要的列传输
    stmt = select(_unit_ids_column(field))
    condition = _time_condition(days)
    if condition is not None:
        stmt = stmt.where(condition)

    # 第 2 步：内存里展开计数
    counter: dict[int, int] = {}
    for ids in session.execute(stmt).scalars():
        for unit_id in _iter_unit_ids(ids):
            counter[unit_id] = counter.get(unit_id, 0) + 1
    if not counter:
        return []

    # 第 3 步：降序取 TOP N（同票按 id 升序，保证结果稳定）
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[
        : max(int(top_n), 1)
    ]

    # 第 4 步：补标题
    titles = _load_titles(session, [unit_id for unit_id, _ in ranked])
    return [
        {
            "unit_id": unit_id,
            "title": titles.get(unit_id, ""),
            "access_count": count,
        }
        for unit_id, count in ranked
    ]


def collect_token_trend(
    session: Session, granularity: str = GRANULARITY_DAY
) -> list[dict]:
    """Token 消耗与响应时间趋势（``GET /api/dashboard/stats/tokens``）。

    口径（11.2）：按日 ``SUM(total_tokens)``，响应时间取该桶均值。

    :raise ValueError: 粒度不是 ``day`` / ``week``。
    """
    # 第 1 步：校验粒度（8.6 只声明 day | week 两种）
    if granularity == GRANULARITY_DAY:
        bucket = func.date(QaAccessLog.created_at)
    elif granularity == GRANULARITY_WEEK:
        # YEARWEEK 是 MySQL 函数；本方案数据库固定为 MySQL 8.4（见 2.2）
        bucket = func.yearweek(QaAccessLog.created_at)
    else:
        raise ValueError(f"不支持的粒度：{granularity}")

    # 第 2 步：按时间桶聚合后升序返回，直接对应折线图的 x 轴
    stmt = (
        select(
            bucket.label("bucket"),
            func.sum(QaAccessLog.total_tokens),
            func.avg(QaAccessLog.response_time_ms),
        )
        .group_by(bucket)
        .order_by(bucket)
    )
    return [
        {
            "date": str(row[0]),
            "total_tokens": int(row[1] or 0),
            "avg_response_time_ms": round(float(row[2] or 0.0), 2),
        }
        for row in session.execute(stmt).all()
    ]
