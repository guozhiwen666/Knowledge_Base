"""AI 鉴权检索服务（2.9.6 / 文档 5.5）门面。

职责与实现落点：

    混合召回           -> :mod:`ai_authentication_and_retrieval_service.retrieval`
    权限过滤（调 5.4）  -> :meth:`AiQaService.filter_by_permission`
    Prompt 组装        -> :meth:`AiQaService.assemble_context`
    流式回答生成        -> :meth:`AiQaService.stream_answer`
    权限缺失提示        -> :meth:`AiQaService.build_permission_notice`
    知识引用来源        -> :meth:`AiQaService.build_citations`
    会话标识           -> :meth:`AiQaService.ensure_session_id`

**本类是最容易出安全事故的地方，两条铁律**：

1. :meth:`assemble_context` **只接受已授权单元**。它的入参类型就是"已授权"，
   未授权内容永远不会进入上下文字符串（3.1 的硬约束）；
2. :meth:`build_permission_notice` **只输出提示文案，不输出被拒单元的内容**
   （2.9.4：召回但无权限的知识单元，只需在回复或引用来源中明确告知缺失访问权限）。

**刻意不做的事**（需求未写明，见第 14 章【待确认】）：

* **历史对话列表**：2.9.3 要求展示，但 2.9.8 没有对应接口，
  且按 4.7 应由 ``qa_access_logs`` 按 ``session_id`` 反查 —— 归属看板/日志读取侧，
  本服务不提前实现无处可调的方法；
* **LangGraph 状态与 SSE 事件映射**：``QAState``、Checkpointer、``astream_events``
  → SSE 的映射属于编排层与接口层职责（4.2 / 4.7 / 4.8），本服务只产出
  纯文本增量与结构化结果，不掺 SSE 协议细节。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from typing import NamedTuple

from sqlalchemy.orm import Session

from services.ai_authentication_and_retrieval_service.retrieval import (
    HybridRetriever,
    RecallOutcome,
    RecalledUnit,
    VectorSearchFn,
)
from services.data_permission_engine.permission import DataPermissionEngine

__all__ = [
    "LlmStreamFn",
    "PermissionFilterResult",
    "AiQaService",
]

# 流式生成函数的签名：输入已组装好的 Prompt，输出文本增量迭代器。
# 不绑死任何厂商 SDK —— 模型选型属第 14 章【待确认】项，由调用方注入。
LlmStreamFn = Callable[[str], Iterator[str]]

# Prompt 模板。需求未规定措辞，这里是可替换的占位模板：
# 只做"给上下文 + 要求依据上下文回答"这两件必要的事，不额外限定语气与长度。
PROMPT_TEMPLATE = """请依据下面提供的知识内容回答用户问题。
若知识内容不足以回答，请直接说明依据不足，不要编造。

【知识内容】
{context}

【用户问题】
{question}
"""

# 权限缺失提示文案。2.9.4 要求"明确告知缺失对应知识单元的访问权限"，
# 因此文案里必须出现"访问权限"字样并点名单元标题。
PERMISSION_NOTICE_TEMPLATE = (
    "以下知识单元因访问权限限制，未能用于本次回答：{titles}。"
    "如需查看，请联系知识管理员申请对应的数据权限。"
)


class PermissionFilterResult(NamedTuple):
    """权限过滤结果（A4 的产出）。

    :param authorized: 允许访问的候选（可进入 Prompt 组装）。
    :param unauthorized: 被拒的候选（只能出现在提示文案里，内容不外泄）。
    """

    authorized: list[RecalledUnit]
    unauthorized: list[RecalledUnit]


class AiQaService:
    """AI 鉴权检索服务门面。

    依赖注入：会话、鉴权引擎、向量检索函数、流式生成函数都由外部传入；
    混合召回器在内部构造，与 ``KnowledgeUnitService`` 内部构造导入流水线的做法一致。
    """

    def __init__(
        self,
        session: Session,
        permission_engine: DataPermissionEngine,
        vector_search: VectorSearchFn,
        llm_stream: LlmStreamFn,
        *,
        default_top_k: int = 5,
    ) -> None:
        """:param session: SQLAlchemy 会话。
        :param permission_engine: 数据权限引擎（5.4），鉴权唯一入口。
        :param vector_search: 向量检索函数，见
            :data:`~ai_authentication_and_retrieval_service.retrieval.VectorSearchFn`。
        :param llm_stream: 流式生成函数，见 :data:`LlmStreamFn`。
        :param default_top_k: 召回条数默认值。
        """
        self._session = session
        self._permission_engine = permission_engine
        self._llm_stream = llm_stream
        self._retriever = HybridRetriever(
            session, vector_search, default_top_k=default_top_k
        )

    # ------------------------------------------------------------------ 会话

    @staticmethod
    def ensure_session_id(session_id: str | None) -> str:
        """返回可用的会话标识：传入则沿用，为空则生成（4.7 会话标识约定）。

        生成值随首个 SSE 事件下发，因此这里只负责产出，
        下发时机由接口层处理。
        """
        return session_id or uuid.uuid4().hex

    # ------------------------------------------------------------------ 召回

    def recall_candidates(
        self, question: str, top_k: int | None = None
    ) -> RecallOutcome:
        """召回候选知识单元（A3，尚未鉴权）。"""
        return self._retriever.recall(question, top_k)

    def filter_by_permission(
        self, user_id: int, candidates: Sequence[RecalledUnit]
    ) -> PermissionFilterResult:
        """对召回结果做数据权限过滤（A4）。

        鉴权口径完全交给 5.4 的引擎，本方法不做任何本地补充判断 ——
        引擎返回什么就是什么，避免出现"两套鉴权逻辑"。
        """
        # 第 1 步：把候选 id 交给权限引擎批量判定
        result = self._permission_engine.check_permissions(
            user_id, [unit.unit_id for unit in candidates]
        )
        # 第 2 步：按判定结果分流；用 id 集合做匹配，保持候选中已有顺序
        authorized_ids = set(result.authorized_unit_ids)
        authorized = [u for u in candidates if u.unit_id in authorized_ids]
        unauthorized = [u for u in candidates if u.unit_id not in authorized_ids]
        return PermissionFilterResult(authorized, unauthorized)

    # ------------------------------------------------------------------ 组装

    def assemble_context(self, authorized: Sequence[RecalledUnit]) -> str:
        """把已授权知识单元组装成 Prompt 上下文（5.5）。

        **只接受已授权单元**。格式为"引用序号 + 标题 + 正文片段"：
        序号与 :meth:`build_citations` 的序号一一对应，
        因此回答里出现 ``[1]`` 时，前端能直接定位到引用卡片。
        """
        # 第 1 步：无已授权内容时返回空串，由调用方决定如何回复
        if not authorized:
            return ""
        # 第 2 步：逐条拼装，序号从 1 开始
        blocks: list[str] = []
        for index, unit in enumerate(authorized, start=1):
            blocks.append(f"[{index}] {unit.title}\n{unit.snippet}")
        # 第 3 步：用空行分隔，便于模型区分不同来源
        return "\n\n".join(blocks)

    def build_citations(self, authorized: Sequence[RecalledUnit]) -> list[dict]:
        """生成知识引用来源卡片（A6 / 2.9.3 的"知识引用来源展示"）。

        序号与 :meth:`assemble_context` 保持一致，均从 1 开始。
        """
        return [
            {
                "index": index,
                "unit_id": unit.unit_id,
                "title": unit.title,
                "score": unit.score,
            }
            for index, unit in enumerate(authorized, start=1)
        ]

    def build_permission_notice(
        self, unauthorized: Sequence[RecalledUnit]
    ) -> str | None:
        """生成权限缺失提示（A6 / 2.9.4）。

        **只输出文案，不输出被拒单元的正文**：这里只用标题，
        ``snippet`` 一律不带，避免把未授权内容顺带泄漏出去。

        :return: 提示文案；没有无权限召回项时返回 ``None``。
        """
        # 第 1 步：没有无权限项就不产生提示，避免每轮都塞一句无用文案
        if not unauthorized:
            return None
        # 第 2 步：用标题点名（标题属知识单元元信息，2.9.4 要求"明确告知"）
        titles = "、".join(u.title or f"知识单元 {u.unit_id}" for u in unauthorized)
        return PERMISSION_NOTICE_TEMPLATE.format(titles=titles)

    # ------------------------------------------------------------------ 生成

    def stream_answer(self, question: str, context: str) -> Iterator[str]:
        """流式生成回答（5.5 / 4.8）。

        只产出纯文本增量，SSE 事件封装由接口层负责（``delta`` 事件）。

        :param question: 用户提问。
        :param context: :meth:`assemble_context` 的产出；为空表示没有可依据的知识，
            仍然交给模型按模板中的"依据不足"要求作答，而不是在这里替它编答案。
        """
        # 第 1 步：按模板组装 Prompt
        prompt = PROMPT_TEMPLATE.format(context=context, question=question)
        # 第 2 步：委托注入的流式函数逐段产出
        yield from self._llm_stream(prompt)
