"""SSE 事件下发helper（对应文档 4.8 的事件契约）。

4.8 规定用 ``astream_events`` 把图内事件映射成 SSE。本方案用的是更直接的
**custom 流式**：图内任意节点调用 :func:`emit` 把 ``{event, data}`` 写进流，
接口层原样按 SSE 帧输出。这样"哪个节点发哪种事件"在图里一眼可见，
不必再写一层"事件名 → 事件名"的翻译表。

事件契约（照抄 4.8，不加不改）：

============================  ==========================================
event                         触发时机
============================  ==========================================
``session``                   会话新建时
``faq_hit``                   命中 FAQ 缓存
``citation``                  A6 产出引用来源
``permission_notice``         存在无权限召回项
``delta``                     A5 每个生成片段
``done``                      结束（带 token 与耗时）
``error``                     失败
============================  ==========================================
"""

from __future__ import annotations

from typing import Any

__all__ = ["EVENTS", "emit", "emit_delta", "record_trace"]

# 事件名常量。用常量而不是散落的裸字符串，避免拼错导致前端收不到
EVENT_SESSION = "session"
EVENT_FAQ_HIT = "faq_hit"
EVENT_CITATION = "citation"
EVENT_PERMISSION_NOTICE = "permission_notice"
EVENT_DELTA = "delta"
EVENT_DONE = "done"
EVENT_ERROR = "error"

EVENTS = (
    EVENT_SESSION,
    EVENT_FAQ_HIT,
    EVENT_CITATION,
    EVENT_PERMISSION_NOTICE,
    EVENT_DELTA,
    EVENT_DONE,
    EVENT_ERROR,
)


def emit(event: str, data: dict[str, Any] | None = None) -> None:
    """把一条事件写进当前流。

    **不在流式上下文里调用时静默返回**：图也可能被 ``invoke`` 直接调用
    （定时子图、单测），那种场景没有流可写，不该因此报错。
    """
    # 第 1 步：取流写入器；非流式上下文会取不到，直接放弃本次下发
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # noqa: BLE001 - 无流上下文属正常情况
        return
    # 第 2 步：写入事件；写入器本身异常也不能影响主流程
    try:
        writer({"event": event, "data": data or {}})
    except Exception:  # noqa: BLE001
        return


def emit_delta(text: str) -> None:
    """下发一段回答增量（``delta`` 事件）。"""
    if text:
        emit(EVENT_DELTA, {"text": text})


def record_trace(node: str, elapsed_ms: int, detail: str = "") -> dict:
    """构造一条节点执行记录（3.3 的可观测约定）。

    :return: 供节点并入 ``state["trace"]`` 的字典。
    """
    return {"node": node, "elapsed_ms": elapsed_ms, "detail": detail}
