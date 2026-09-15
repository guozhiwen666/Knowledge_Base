"""LangGraph 全局状态定义（对应文档 4.2）。

字段命名与 ``qa_access_logs`` 的列**逐一保持对应**（三组单元 id、三个 token 计数、
``response_time_ms``），这样 A7 落库时直接映射，不做二次改名 —— 少一层映射，
就少一处"名字对不上"的隐蔽 bug。

三组单元 id 的语义严格区分（这是本项目最容易出安全事故的地方）：

* ``recalled_units``     召回候选，**尚未鉴权**，可能是用户看不到的内容
* ``authorized_units``   鉴权通过，允许进入 Prompt 组装
* ``unauthorized_units`` 鉴权被拒，只允许用于生成"权限缺失提示"文案
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

__all__ = ["QAState"]

# 说明：三组单元列表里存放的是服务层的 ``retrieval.RecalledUnit`` 命名元组
# （unit_id / title / score / snippet）。不在这里另定一个 dict 结构，
# 是为了避免"图内一套形态、服务层一套形态"来回转换 —— 转换层是 bug 的温床。
# 事件下发前由接口层负责序列化。


class QAState(TypedDict, total=False):
    """AI 鉴权问答主图的状态。

    字段全部可选（``total=False``）：图在不同分支下只会填一部分字段，
    强制要求全部存在反而要在每个节点里补空值。
    """

    # ---- 请求入参（来自 POST /api/ai/chat/stream）----
    question: str
    session_id: str

    # ---- A1 产出：登录态与身份 ----
    auth_ok: bool
    user_id: int
    department_id: int | None
    role_ids: list[int]
    ai_permitted: bool

    # ---- A2 产出：FAQ 缓存 ----
    faq_hit: bool
    faq_id: int | None
    faq_answer: str | None
    matched_by: str | None

    # ---- A3 产出：召回候选（未鉴权）----
    recalled_units: list

    # ---- A4 产出：鉴权结果 ----
    authorized_units: list
    unauthorized_units: list

    # ---- A5 / A6 产出 ----
    citations: list[dict]
    permission_notice: str | None
    # 流式增量用 operator.add 累加，保证多节点写入时不被覆盖
    answer_chunks: Annotated[list[str], operator.add]

    # ---- A7 产出：日志指标（与 qa_access_logs 列名一致）----
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    response_time_ms: int

    # ---- 控制与可观测 ----
    branch: str | None
    degraded: str | None
    # 请求进入时刻（time.monotonic()），A7 据此算出接口响应时长
    started_at: float
    errors: Annotated[list[dict], operator.add]
    trace: Annotated[list[dict], operator.add]
