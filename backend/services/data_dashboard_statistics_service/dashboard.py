"""数据看板统计服务（2.9.6 / 文档 5.6）—— 在线写入与查询入口。

职责与实现落点：

    异步记录每轮问答的访问日志   -> :meth:`DashboardService.record_access_log`（11.1）
    当日实时计数（Redis 原子自增）-> :meth:`DashboardService.daily_realtime`
    指标 / 榜单 / 趋势的聚合查询  -> :mod:`data_dashboard_statistics_service.aggregation`
                                     （8.6 的四个接口，本类只做转发）

**在线只写、聚合放在读侧**：4.6 明确"主链路只做一件事：A7 落一条原始日志，
聚合全部放在查询侧与定时子图，避免在线写放大"。因此本模块不在问答链路上做
任何聚合计算，只落一行日志 + 一次实时计数自增；聚合查询拆在 ``aggregation`` 里。

**"异步记录"怎么落地**：5.6 要求异步，本服务提供的是**同步的落库方法**，
由调用方决定执行方式 —— 编排层用 FastAPI ``BackgroundTasks``（或线程池）调用即可。
服务内部不自建线程池，那会引入无人管理的线程生命周期。

**刻意不做的事**：

* 4.6 中"聚合结果写入缓存供接口直读"的**定时子图**属编排层（``graph`` 目录）职责；
* 8.6 没有"今日实时指标"这类响应字段，因此 :meth:`DashboardService.daily_realtime`
  供定时子图与运维观测使用，**不并入** 8.6 的响应结构（不擅自扩字段）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.orm import Session

from models import QaAccessLog
from services.data_dashboard_statistics_service.aggregation import (
    GRANULARITY_DAY,
    collect_metrics,
    collect_token_trend,
    rank_questions,
    rank_units,
)

__all__ = ["DashboardService", "DEFAULT_TOP_N"]

# 榜单默认取前 10。8.6 未规定 TOP 长度，集中在此便于调整。
DEFAULT_TOP_N = 10

# 当日实时计数的缓存键前缀（5.6：实时部分走 Redis 原子自增）
REALTIME_ACCESS_KEY = "kb:dashboard:access"
REALTIME_TOKEN_KEY = "kb:dashboard:tokens"


class DashboardService:
    """数据看板统计服务。

    :param session: SQLAlchemy 会话。
    :param counter: 可选的实时计数器（Redis 客户端），需提供 ``incrby(key, n)``
        与 ``get(key)``。传 ``None`` 时跳过实时计数，只做落库与 SQL 聚合 ——
        这样没有 Redis 的环境（例如跑单测）照样能用。
    """

    def __init__(self, session: Session, counter=None) -> None:
        self._session = session
        self._counter = counter

    # ------------------------------------------------------------------ 写入

    def record_access_log(
        self,
        *,
        session_id: str,
        user_id: int | None,
        question: str | None,
        answer: str | None,
        recalled_unit_ids: Sequence[int] | None = None,
        authorized_unit_ids: Sequence[int] | None = None,
        unauthorized_unit_ids: Sequence[int] | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        response_time_ms: int = 0,
        commit: bool = True,
    ) -> QaAccessLog:
        """写入一条问答访问日志（A7；字段清单见 11.1）。

        参数与 11.1 的字段表逐项对应，三组单元 id 以 JSON 数组落库。
        调用方用 ``BackgroundTasks`` 包一层即可实现 5.6 要求的"异步记录"。

        :param commit: 是否立即提交；批量补写历史日志时可传 ``False`` 由调用方统一提交。
        :return: 已写入的日志行（可读其 ``id``）。
        """
        # 第 1 步：建立日志行，三组 id 统一转 list 后落 JSON 列
        log = QaAccessLog(
            session_id=session_id,
            user_id=user_id,
            question=question,
            answer=answer,
            recalled_unit_ids_json=list(recalled_unit_ids or []),
            authorized_unit_ids_json=list(authorized_unit_ids or []),
            unauthorized_unit_ids_json=list(unauthorized_unit_ids or []),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            response_time_ms=response_time_ms,
        )
        self._session.add(log)
        # 第 2 步：flush 拿到 log.id，再决定是否提交
        self._session.flush()
        if commit:
            self._session.commit()
        # 第 3 步：实时计数（best-effort）。计数失败绝不影响已落库的日志
        self._bump_realtime(total_tokens)
        return log

    def _bump_realtime(self, total_tokens: int) -> None:
        """当日实时计数自增（5.6 的"实时部分走 Redis 原子自增"）。

        best-effort：计数器异常一律吞掉。看板少一个实时数字是小事，
        把已落库的问答日志连带搞失败是大事。
        """
        if self._counter is None:
            return
        try:
            suffix = self._today()
            self._counter.incrby(f"{REALTIME_ACCESS_KEY}:{suffix}", 1)
            if total_tokens:
                self._counter.incrby(f"{REALTIME_TOKEN_KEY}:{suffix}", int(total_tokens))
        except Exception:
            # 有意静默：实时计数属可降级能力
            return

    def daily_realtime(self) -> dict:
        """读取当日实时计数（供 4.6 的定时聚合子图与运维观测使用）。"""
        # 第 1 步：无计数器时返回零值，调用方无需做 None 判断
        if self._counter is None:
            return {"access_count": 0, "total_tokens": 0}
        # 第 2 步：读两个当日键；当天还没写时按 0 处理
        suffix = self._today()
        return {
            "access_count": int(self._read_counter(f"{REALTIME_ACCESS_KEY}:{suffix}") or 0),
            "total_tokens": int(self._read_counter(f"{REALTIME_TOKEN_KEY}:{suffix}") or 0),
        }

    def _read_counter(self, key: str):
        """读一个计数器；异常时返回 ``None``（降级）。"""
        try:
            return self._counter.get(key)
        except Exception:
            return None

    @staticmethod
    def _today() -> str:
        """当日键后缀，形如 ``20260915``。"""
        return datetime.now().strftime("%Y%m%d")

    # ------------------------------------------------------------------ 查询

    def metrics(self, days: int | None = None) -> dict:
        """核心指标（``GET /api/dashboard/metrics``，口径见 11.2）。

        :param days: 只统计最近 N 天；不传为全量。
        """
        return collect_metrics(self._session, days)

    def top_questions(
        self, top_n: int = DEFAULT_TOP_N, days: int | None = None
    ) -> list[dict]:
        """常见问题 TOP 榜（``GET /api/dashboard/rankings/questions``）。"""
        return rank_questions(self._session, top_n, days)

    def top_units(
        self,
        top_n: int = DEFAULT_TOP_N,
        days: int | None = None,
        field: str = "recalled",
    ) -> list[dict]:
        """知识单元热度 TOP 榜（``GET /api/dashboard/rankings/units``）。

        :param field: 统计口径 ``recalled`` / ``authorized``，
            该口径属第 14 章【待确认】，故暴露为参数。
        """
        return rank_units(self._session, top_n, days, field)

    def token_trend(self, granularity: str = GRANULARITY_DAY) -> list[dict]:
        """Token 消耗与响应时间趋势（``GET /api/dashboard/stats/tokens``）。"""
        return collect_token_trend(self._session, granularity)
