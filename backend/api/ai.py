"""AI 对话接口（8.5）。

只实现 8.5 的 ``POST /api/ai/chat/stream``：**校验登录态 → 驱动 qa_graph → 输出 SSE**。

SSE 事件严格按 4.8 的契约下发，事件名与 data 结构都不加不改：

    session / faq_hit / citation / permission_notice / delta / done / error

实现方式：图用 ``stream_mode=["custom", "values"]`` 驱动 ——
``custom`` 收图内 ``emit()`` 写出的事件（即 4.8 要求的 astream_events 效果），
``values`` 收最终状态，用于在末尾组装 ``done`` 事件里的 token 与耗时。

**为什么用同步 generator**：图内部跑的是同步的 SQLAlchemy / Milvus / OpenAI 客户端，
同步 generator 交给 FastAPI 会在线程池里执行，不阻塞事件循环；
换成 async generator 反而要在里面跑同步阻塞调用，是更差的选择。
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from core.container import Container, get_container
from core.db import get_session
from core.security import CurrentUser, get_current_user
from graph.qa_graph import build_qa_graph
from schemas.requests import ChatRequest
from services.ai_authentication_and_retrieval_service.qa import AiQaService

__all__ = ["router"]

router = APIRouter(prefix="/api/ai", tags=["AI 问答"])


def _frame(event: str, data: dict) -> str:
    """把一个事件序列化成 SSE 帧。

    每条帧形如 ``event: delta\\ndata: {...}\\n\\n`` —— 末尾的空行是帧结束标志，
    少了它前端会一直等下一帧。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/chat/stream", summary="AI 鉴权问答（SSE 流式）")
def chat_stream(
    payload: ChatRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> StreamingResponse:
    """登录后提问，返回 SSE 流式应答与权限缺失提示（8.5）。

    会话标识为空时由服务端生成，并随首个 ``session`` 事件下发（4.7）。
    """
    # 第 1 步：确定会话标识
    session_id = AiQaService.ensure_session_id(payload.session_id)

    # 第 2 步：构造初始状态。user_id 来自令牌（接口层已完成登录态校验），
    # A1 会再查一次库，保证"停用即刻生效"
    initial_state = {
        "question": payload.question,
        "session_id": session_id,
        "user_id": user.user_id,
        "started_at": time.monotonic(),
        "answer_chunks": [],
        "errors": [],
        "trace": [],
    }

    # 第 3 步：建图（每请求一张新图实例：会话与 checkpointer 都是请求内的）
    graph = build_qa_graph(session, container)

    def event_stream():
        """驱动图并把事件翻译成 SSE 帧。"""
        final_state: dict = {}
        config = {"configurable": {"thread_id": session_id}}
        try:
            # ``custom`` 收图内事件；``values`` 收状态快照（用于组装 done）
            for mode, chunk in graph.stream(
                initial_state, config=config, stream_mode=["custom", "values"]
            ):
                if mode == "custom" and isinstance(chunk, dict) and "event" in chunk:
                    yield _frame(chunk["event"], chunk.get("data") or {})
                elif mode == "values" and isinstance(chunk, dict):
                    final_state = chunk
        except Exception as exc:  # noqa: BLE001 - 流已开始，只能用 error 事件收尾
            yield _frame(
                "error",
                {"code": 500, "message": f"问答失败：{type(exc).__name__}: {exc}"},
            )
            return

        # 第 4 步：结束事件。token 与耗时均取自最终状态（与落库日志同一份数据）
        yield _frame(
            "done",
            {
                "session_id": session_id,
                "total_tokens": int(final_state.get("total_tokens") or 0),
                "response_time_ms": int(final_state.get("response_time_ms") or 0),
                "degraded": final_state.get("degraded"),
                "faq_hit": bool(final_state.get("faq_hit")),
            },
        )

    # 第 5 步：返回流式响应。显式关闭各级缓冲，否则前端收不到逐字效果
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx 等反向代理据此关闭缓冲
            "X-Accel-Buffering": "no",
        },
    )
