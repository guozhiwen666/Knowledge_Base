"""A7 MetricsAgent / A8 SettlementAgent（3.1 / 4.5 / 4.6）。

* **A7** 在问答链路末尾落一条 ``qa_access_logs``。它只写原始日志、**不做聚合**——
  4.6 的原话是"主链路只做一件事：落一条原始日志，聚合全部放在查询侧与定时子图"。
* **A8** 是离线沉淀挖掘的执行体，由定时任务经子图调用（4.5），与在线链路物理隔离。
"""

from __future__ import annotations

import time

from graph.events import record_trace

__all__ = ["MetricsAgent", "SettlementAgent"]


class MetricsAgent:
    """A7：异步记录访问日志（11.1 的字段清单）。"""

    def __init__(self, dashboard, *, enabled: bool = True) -> None:
        """:param dashboard: 数据看板统计服务（5.6）。
        :param enabled: 是否落库（关掉可用于压测或调试）。
        """
        self._dashboard = dashboard
        self._enabled = enabled

    def __call__(self, state: dict) -> dict:
        """写日志并返回 ``response_time_ms``。

        **本节点绝不抛异常**：答案早就流给用户了，此时因为写日志失败而中断
        整张图，等于让用户看着半截回答然后报错。因此写库失败只记录到 ``errors``。
        """
        started = time.monotonic()

        # 第 1 步：算接口响应时长。起点由接口层在请求进入时写进 state.started_at
        request_start = state.get("started_at") or started
        elapsed_ms = int((time.monotonic() - request_start) * 1000)

        # 第 2 步：未启用或看板服务缺失时只回填耗时
        if not self._enabled or self._dashboard is None:
            return {"response_time_ms": elapsed_ms}

        # 第 3 步：把三组单元 id 从对象列表摊平成 id 列表（与 qa_access_logs 的 JSON 列对应）
        recalled = list(state.get("recalled_units") or [])
        authorized = list(state.get("authorized_units") or [])
        unauthorized = list(state.get("unauthorized_units") or [])

        # 第 4 步：落库。全部包在 try 里，失败只记账不中断
        try:
            self._dashboard.record_access_log(
                session_id=state.get("session_id") or "",
                user_id=state.get("user_id"),
                question=state.get("question"),
                answer="".join(state.get("answer_chunks") or []),
                recalled_unit_ids=[unit.unit_id for unit in recalled],
                authorized_unit_ids=[unit.unit_id for unit in authorized],
                unauthorized_unit_ids=[unit.unit_id for unit in unauthorized],
                prompt_tokens=int(state.get("prompt_tokens") or 0),
                completion_tokens=int(state.get("completion_tokens") or 0),
                total_tokens=int(state.get("total_tokens") or 0),
                response_time_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 日志失败不能影响已返回的回答
            return {
                "response_time_ms": elapsed_ms,
                "errors": [{"node": "A7 MetricsAgent", "error": str(exc)}],
            }

        # 第 5 步：回填耗时，供接口层在 done 事件里下发
        return {
            "response_time_ms": elapsed_ms,
            "trace": [
                record_trace(
                    "A7 MetricsAgent",
                    int((time.monotonic() - started) * 1000),
                    f"已记录日志（耗时 {elapsed_ms}ms）",
                )
            ],
        }


class SettlementAgent:
    """A8：知识沉淀挖掘（4.5 子图的两个执行节点）。"""

    def __init__(self, settlement) -> None:
        """:param settlement: 知识沉淀挖掘服务（5.7）。"""
        self._settlement = settlement

    def mine(self, state: dict) -> dict:
        """高频相似问题 → 待审核 FAQ 推荐项。"""
        started = time.monotonic()
        window_days = int(state.get("window_days") or 30)

        # 第 1 步：挖掘服务缺失时直接返回空结果（定时任务不该因此崩掉）
        if self._settlement is None:
            return {
                "faq_recommendations": 0,
                "errors": [{"node": "A8 SettlementAgent", "error": "沉淀服务未就绪"}],
            }

        # 第 2 步：执行挖掘并只回传计数，不把 ORM 对象带出节点
        created = self._settlement.mine_faq_recommendations(window_days)
        return {
            "faq_recommendations": len(created),
            "trace": [
                record_trace(
                    "A8 SettlementAgent",
                    int((time.monotonic() - started) * 1000),
                    f"生成 {len(created)} 条 FAQ 推荐项",
                )
            ],
        }

    def detect_gaps(self, state: dict) -> dict:
        """未命中提问 → 知识缺口。"""
        started = time.monotonic()
        window_days = int(state.get("window_days") or 30)

        # 第 1 步：服务缺失时返回空结果
        if self._settlement is None:
            return {"knowledge_gaps": 0}

        # 第 2 步：识别缺口并回传计数
        gaps = self._settlement.detect_knowledge_gaps(window_days)
        return {
            "knowledge_gaps": len(gaps),
            "trace": [
                record_trace(
                    "A8 SettlementAgent",
                    int((time.monotonic() - started) * 1000),
                    f"识别 {len(gaps)} 条知识缺口",
                )
            ],
        }
