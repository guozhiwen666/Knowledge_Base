"""A2 FAQCacheAgent / A3 RecallAgent / A4 PermissionAgent（3.1 / 4.3 / 4.4）。

这三个 Agent 构成"召回 → 鉴权"的前半段，边界必须记牢：

* **A2 缓存优先**：命中已发布 FAQ 就直接产出答案，**不调用大模型**
  （4.4：此时 prompt/completion tokens 均为 0，这是 Token 走势出现低值区间的原因）；
* **A3 只召回不鉴权**：产出的 ``recalled_units`` 是候选集，允许包含用户看不到的内容；
* **A4 是图上唯一的鉴权点**：允许/拒绝两个列表由它产出，下游只认这两个列表
  （3.1 的硬约束：一旦别处也判一次权限，两处逻辑迟早漂移成两个不同的答案）。
"""

from __future__ import annotations

import time

from graph.events import EVENT_FAQ_HIT, emit, record_trace

__all__ = ["FAQCacheAgent", "RecallAgent", "PermissionAgent"]


class FAQCacheAgent:
    """A2：FAQ 缓存匹配（4.4 的精确匹配 → 语义匹配）。"""

    def __init__(self, faq_cache, *, count_hit: bool = True) -> None:
        """:param faq_cache: FAQ 缓存服务（5.8）。
        :param count_hit: 命中后是否回写 ``faqs.hit_count``（11.3 的命中计数）。
        """
        self._faq_cache = faq_cache
        self._count_hit = count_hit

    def __call__(self, state: dict) -> dict:
        """查缓存；命中则产出标准答案，未命中则让下游走召回。"""
        started = time.monotonic()
        question = state.get("question") or ""

        # 第 1 步：缓存不可用（未注入）时按未命中处理，主链路继续走召回
        if self._faq_cache is None:
            return {"faq_hit": False}

        # 第 2 步：查缓存（精确 → 语义），异常按未命中处理，绝不让缓存故障打断问答
        try:
            hit = self._faq_cache.lookup(question)
        except Exception as exc:  # noqa: BLE001
            return {
                "faq_hit": False,
                "errors": [{"node": "A2 FAQCacheAgent", "error": str(exc)}],
            }

        if hit is None:
            return {
                "faq_hit": False,
                "trace": [
                    record_trace(
                        "A2 FAQCacheAgent",
                        int((time.monotonic() - started) * 1000),
                        "未命中",
                    )
                ],
            }

        # 第 3 步：命中 → 下发 faq_hit 事件（4.8），供前端展示"命中缓存"标识
        emit(
            EVENT_FAQ_HIT,
            {
                "faq_id": hit.faq_id,
                "question": hit.question,
                "matched_by": hit.matched_by,
            },
        )

        # 第 4 步：回写命中计数（11.3）。best-effort：计数失败不影响本次回答
        if self._count_hit:
            try:
                self._faq_cache.increment_hit(hit.faq_id)
            except Exception:  # noqa: BLE001
                pass

        return {
            "faq_hit": True,
            "faq_id": hit.faq_id,
            "faq_answer": hit.answer or "",
            "matched_by": hit.matched_by,
            # 命中缓存不调模型，token 记为 0（4.4）
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "trace": [
                record_trace(
                    "A2 FAQCacheAgent",
                    int((time.monotonic() - started) * 1000),
                    f"命中 faq={hit.faq_id} by={hit.matched_by}",
                )
            ],
        }


class RecallAgent:
    """A3：向量 + 关键字混合召回（5.5），**不做任何权限判断**。"""

    def __init__(self, qa_service, *, top_k: int = 5) -> None:
        """:param qa_service: AI 鉴权检索服务（5.5）。
        :param top_k: 召回条数。
        """
        self._qa = qa_service
        self._top_k = top_k

    def __call__(self, state: dict) -> dict:
        """执行召回；向量库异常时降级为纯关键字检索（3.4）。"""
        started = time.monotonic()
        question = state.get("question") or ""

        # 第 1 步：检索服务不可用（Milvus / 模型未就绪）时返回空候选，
        # 由下游的"召回为空 → 知识缺口"分支兜住，而不是抛异常中断整张图
        if self._qa is None:
            return {
                "recalled_units": [],
                "degraded": "no_retrieval",
                "errors": [{"node": "A3 RecallAgent", "error": "检索服务未就绪"}],
            }

        # 第 2 步：混合召回
        outcome = self._qa.recall_candidates(question, self._top_k)

        # 第 3 步：返回候选与降级标记（degraded 取值与 3.4 的约定一致）
        return {
            "recalled_units": list(outcome.units),
            "degraded": outcome.degraded,
            "trace": [
                record_trace(
                    "A3 RecallAgent",
                    int((time.monotonic() - started) * 1000),
                    f"召回 {len(outcome.units)} 条"
                    + (f"（降级 {outcome.degraded}）" if outcome.degraded else ""),
                )
            ],
        }


class PermissionAgent:
    """A4：数据权限鉴权过滤（5.4），**图上的唯一鉴权点**。"""

    def __init__(self, qa_service) -> None:
        """:param qa_service: AI 鉴权检索服务（内部转调数据权限引擎）。"""
        self._qa = qa_service

    def __call__(self, state: dict) -> dict:
        """把召回候选切成"允许"与"拒绝"两个列表。"""
        started = time.monotonic()
        recalled = list(state.get("recalled_units") or [])
        user_id = state.get("user_id")

        # 第 1 步：没有候选就没必要鉴权，直接返回两个空列表
        if not recalled or user_id is None:
            return {"authorized_units": [], "unauthorized_units": [], "branch": "permission"}

        # 第 2 步：交给 5.4 的引擎判定，本节点不做任何本地补充判断
        result = self._qa.filter_by_permission(user_id, recalled)

        # 第 3 步：返回两个列表。**未授权单元只带元信息进入下游的提示文案**，
        # 其正文片段绝不会被 AnswerAgent 使用（3.1 硬约束）
        return {
            "authorized_units": list(result.authorized),
            "unauthorized_units": list(result.unauthorized),
            "branch": "permission",
            "trace": [
                record_trace(
                    "A4 PermissionAgent",
                    int((time.monotonic() - started) * 1000),
                    f"放行 {len(result.authorized)} 拒绝 {len(result.unauthorized)}",
                )
            ],
        }
