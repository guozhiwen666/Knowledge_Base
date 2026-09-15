"""知识沉淀子图 `settlement_graph`（对应文档 4.5）。

4.5 的流程拆成两个节点：

    读取窗口期日志 → 规范化去重 → 频次聚合 → 阈值判定 → 生成 FAQ 推荐项   （A8.mine）
    未命中提问 → 按问题模式聚合 → 写入/累加 knowledge_gaps                （A8.detect_gaps）

两张表互不依赖，本可以并行；但挖掘都要读同一批 ``qa_access_logs``，
串行执行能共用一次会话、避免两个事务同时写库打架，因此按 4.5 的图序串行。

**触发方式**：由定时任务调用（4.1 的"离线子图"），不在在线问答链路上。
8.7 只提供了查询与审核接口，没有"手动触发挖掘"的接口，
因此这里只暴露一个可被调度器调用的 ``run_settlement`` 函数。
"""

from __future__ import annotations

import time
from typing import Annotated, TypedDict

import operator
from langgraph.graph import END, START, StateGraph

from agents.platform import SettlementAgent

__all__ = ["SettlementState", "build_settlement_graph", "run_settlement"]


class SettlementState(TypedDict, total=False):
    """沉淀子图状态。"""

    # 输入：回溯窗口天数
    window_days: int
    # 输出：本轮产出计数
    faq_recommendations: int
    knowledge_gaps: int
    # 可观测
    errors: Annotated[list[dict], operator.add]
    trace: Annotated[list[dict], operator.add]


def build_settlement_graph(settlement):
    """构造并编译沉淀子图。

    :param settlement: 知识沉淀挖掘服务（5.7）。
    :return: 已编译的图；用 ``.invoke({"window_days": 30})`` 触发一轮挖掘。
    """
    agent = SettlementAgent(settlement)

    # 第 1 步：搭两个节点，与 4.5 的两条产出对应
    graph = StateGraph(SettlementState)
    graph.add_node("a8_mine_faq", agent.mine)
    graph.add_node("a8_detect_gaps", agent.detect_gaps)

    # 第 2 步：串行连接
    graph.add_edge(START, "a8_mine_faq")
    graph.add_edge("a8_mine_faq", "a8_detect_gaps")
    graph.add_edge("a8_detect_gaps", END)

    # 第 3 步：编译。沉淀是批处理，不需要 Checkpointer
    return graph.compile()


def run_settlement(settlement, window_days: int = 30) -> dict:
    """跑一轮沉淀挖掘，返回本轮产出计数。

    :return: ``{"faq_recommendations": n, "knowledge_gaps": m, "elapsed_ms": t}``
    """
    started = time.monotonic()
    app = build_settlement_graph(settlement)
    final = app.invoke({"window_days": int(window_days), "errors": [], "trace": []})
    return {
        "faq_recommendations": int(final.get("faq_recommendations") or 0),
        "knowledge_gaps": int(final.get("knowledge_gaps") or 0),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "trace": final.get("trace", []),
        "errors": final.get("errors", []),
    }
