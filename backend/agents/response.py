"""A5 AnswerAgent / A6 CitationAgent（3.1 / 4.3 / 4.8）。

**这两个 Agent 分离的原因**：生成回答与"处理没权限的部分"是两件事。
分开之后 A5 的入参在类型上就只有 ``authorized_units``，
未授权内容根本没机会进入 Prompt（3.1 硬约束）；
A6 知道哪些被拒了，但它只输出提示**文案**，不输出被拒单元的正文（2.9.4）。
"""

from __future__ import annotations

import time

from graph.events import (
    EVENT_CITATION,
    EVENT_PERMISSION_NOTICE,
    emit,
    emit_delta,
    record_trace,
)

__all__ = ["AnswerAgent", "CitationAgent"]


class AnswerAgent:
    """A5：组装上下文并流式生成回答（5.5）。"""

    def __init__(self, qa_service, llm_stream) -> None:
        """:param qa_service: AI 鉴权检索服务（负责 Prompt 组装与流式生成）。
        :param llm_stream: 与 ``qa_service`` 使用同一个流式函数对象，
            以便生成结束后从它身上读 token 用量（``last_usage``）。
        """
        self._qa = qa_service
        self._llm = llm_stream

    def __call__(self, state: dict) -> dict:
        """产出回答增量并下发 ``delta`` 事件。"""
        started = time.monotonic()

        # 第 1 步：命中 FAQ 缓存 → 直接输出标准答案，不调用大模型（4.4）。
        # 此时 token 三项保持为 0，正是缓存加速降低 Token 消耗的体现
        if state.get("faq_hit"):
            answer = state.get("faq_answer") or ""
            emit_delta(answer)
            return {
                "answer_chunks": [answer],
                "trace": [
                    record_trace("A5 AnswerAgent", 0, "命中 FAQ 缓存，未调用模型")
                ],
            }

        # 第 2 步：检索服务不可用 → 友好回复，不编造答案
        if self._qa is None or self._llm is None:
            text = "服务暂不可用，无法生成回答。"
            emit_delta(text)
            return {"answer_chunks": [text]}

        # 第 3 步：**只用已授权单元**组装上下文（3.1）。空授权集时上下文为空串，
        # 由 Prompt 模板要求模型说明"依据不足"，而不是在这里替它编答案
        authorized = list(state.get("authorized_units") or [])
        context = self._qa.assemble_context(authorized)

        # 第 4 步：流式生成并逐段下发 delta 事件。历史轮次由接口层按
        # 第 14 章 #12 填入 state，这里只负责透传，不做取舍
        collected: list[str] = []
        for piece in self._qa.stream_answer(
            state.get("question") or "",
            context,
            history=state.get("history") or [],
        ):
            collected.append(piece)
            emit_delta(piece)

        # 第 5 步：取 token 用量（网关不回 usage 时按 0 处理，11.1 允许为 0）
        usage = getattr(self._llm, "last_usage", None) or {}
        return {
            "answer_chunks": collected,
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "trace": [
                record_trace(
                    "A5 AnswerAgent",
                    int((time.monotonic() - started) * 1000),
                    f"引用 {len(authorized)} 个单元，输出 {len(collected)} 段",
                )
            ],
        }


class CitationAgent:
    """A6：引用来源与权限缺失提示（4.3 / 4.8）。"""

    def __init__(self, qa_service) -> None:
        """:param qa_service: AI 鉴权检索服务（提供引用与提示文案构造）。"""
        self._qa = qa_service

    def __call__(self, state: dict) -> dict:
        """下发 ``citation`` 与 ``permission_notice`` 事件。"""
        started = time.monotonic()
        authorized = list(state.get("authorized_units") or [])
        unauthorized = list(state.get("unauthorized_units") or [])

        # 第 1 步：命中 FAQ 缓存时不产生知识引用（答案是缓存的标准答案，
        # 没有经由知识单元组装），因此只在走召回的链路上出引用
        citations: list[dict] = []
        if not state.get("faq_hit") and authorized and self._qa is not None:
            citations = self._qa.build_citations(authorized)
            if citations:
                emit(EVENT_CITATION, {"citations": citations})

        # 第 2 步：存在被召回但无权限的单元 → 明确告知缺失访问权限（2.9.4）。
        # 提示里只出现标题，绝不带 snippet —— 这是最容易泄漏内容的地方
        notice: str | None = None
        if unauthorized and self._qa is not None:
            notice = self._qa.build_permission_notice(unauthorized)
            if notice:
                emit(
                    EVENT_PERMISSION_NOTICE,
                    {
                        "notice": notice,
                        "unauthorized_units": [
                            {"unit_id": unit.unit_id, "title": unit.title}
                            for unit in unauthorized
                        ],
                    },
                )

        return {
            "citations": citations,
            "permission_notice": notice,
            "trace": [
                record_trace(
                    "A6 CitationAgent",
                    int((time.monotonic() - started) * 1000),
                    f"引用 {len(citations)} 条，缺权限提示 {'有' if notice else '无'}",
                )
            ],
        }
