"""AI 鉴权问答主图 `qa_graph`（对应文档 4.3）。

图结构（与 4.3 的流程图逐步对应）：

    START → A1 鉴权
              ├─ auth_ok=False ────────────────→ deny → END
              └─ 通过 → 并行扇出 ┬ A2 FAQ 缓存
                                 └ A3 混合召回
                 ↓ 汇聚（等待两路都完成）
               merge 判定
                 ├─ faq_hit=True       → A5 生成（直接用缓存答案，跳过 A4）
                 ├─ 召回为空            → gap（友好答复）
                 └─ 召回非空            → A4 鉴权过滤 → A5 → A6 → A7 → END

三处与 4.3 图**形式不同但语义一致**的地方，都记在这里避免后人误会：

1. 4.3 里 ``faq_answer`` 与「A5 生成」是两个终点。这里合并成同一个节点 ——
   "把答案下发给用户"这件事写两遍必然漂移，A5 内部按 ``faq_hit`` 分流即可，
   关键是**命中缓存时不经过 A4**，这一点与 4.3 完全一致；
2. 4.3 的 ``gap`` 节点写的是"记录知识缺口并友好答复"。这里**只做友好答复**：
   在线链路已经把"召回为空"这件事记进 ``qa_access_logs``（A7），
   再由离线子图统一聚合成 ``knowledge_gaps``（4.5/5.7）。
   两处都写同一张表只会导致重复计数；
3. 扇出用的是 LangGraph 的 ``add_edge(["a2_faq","a3_recall"], "merge")`` 汇聚写法，
   保证 merge 在两路都完成后**只执行一次**。
"""

from __future__ import annotations

import time

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agents.guards import AuthGuardAgent
from agents.platform import MetricsAgent
from agents.response import AnswerAgent, CitationAgent
from agents.retrieval import FAQCacheAgent, PermissionAgent, RecallAgent
from graph.events import emit_delta, record_trace
from graph.state import QAState
from models.vector import COLLECTION_NAME, FIELD_UNIT_ID
from services.FAQ_cache_ervice.faq_cache import FaqCacheService
from services.ai_authentication_and_retrieval_service.qa import AiQaService
from services.data_dashboard_statistics_service.dashboard import DashboardService
from services.data_permission_engine.permission import DataPermissionEngine

__all__ = ["build_qa_graph", "build_vector_search", "GAP_ANSWER_TEXT"]

# 召回为空时的友好答复。2.9.4 只要求"友好提示"，措辞可替换
GAP_ANSWER_TEXT = "抱歉，知识库中暂时没有与您问题相关的内容。"


def build_vector_search(container, *, metric_type: str = "COSINE", nlist: int = 128):
    """构造向量检索函数，注入给 AI 鉴权检索服务。

    返回的是"输入查询文本 → 输出 (单元 id, 相似度) 列表"的可调用对象 ——
    服务层只认这个契约，不关心 Milvus 的字段名与调用细节。

    **同一知识单元的多个切片会按最高分去重**：检索粒度是切片，
    但召回粒度是知识单元，不去重会把同一个单元挤满整个 TOP K。
    """

    def _search(question: str, limit: int) -> list[tuple[int, float]]:
        # 第 1 步：Milvus 或向量化未就绪 → 返回空，由 A3 判定为无候选
        client = getattr(container, "milvus", None)
        embedding = getattr(container, "embedding", None)
        if client is None or embedding is None:
            return []

        # 第 2 步：把查询文本向量化
        vectors = embedding([question])
        if not vectors:
            return []

        # 第 3 步：检索切片，取出 ``unit_id`` 标量字段
        result = client.search(
            collection_name=COLLECTION_NAME,
            data=[list(vectors[0])],
            limit=max(int(limit), 1),
            output_fields=[FIELD_UNIT_ID],
            search_params={"metric_type": metric_type, "params": {"nlist": nlist}},
        )

        # 第 4 步：解析命中结果。pymilvus 返回 [ [ {id, distance, entity}, ... ] ]
        hits: dict[int, float] = {}
        for hit in (result[0] if result else []):
            entity = hit.get("entity") if isinstance(hit, dict) else None
            raw_unit_id = None
            if isinstance(entity, dict):
                raw_unit_id = entity.get(FIELD_UNIT_ID)
            if raw_unit_id is None and isinstance(hit, dict):
                # 极端情况下 output_fields 没带回来，退回用切片主键而非丢弃结果
                raw_unit_id = hit.get("id")
            try:
                unit_id = int(raw_unit_id)
            except (TypeError, ValueError):
                continue
            score = float(hit.get("distance", 0.0)) if isinstance(hit, dict) else 0.0
            # 同一单元的多个切片只保留最高分
            if unit_id not in hits or score > hits[unit_id]:
                hits[unit_id] = score

        # 第 5 步：按相似度降序返回
        return sorted(hits.items(), key=lambda item: -item[1])

    return _search


def _build_merge_node():
    """构造汇聚后的分支判定节点。

    LangGraph 的条件边只能挂在具体节点上，因此两路扇出需要一个真实的汇聚节点；
    这里让它产出 ``branch``，条件函数只做取值映射，逻辑不分两处。
    """

    def _merge(state: dict) -> dict:
        # 第 1 步：命中缓存优先级最高（4.4：缓存命中直接输出，跳过召回与鉴权）
        if state.get("faq_hit"):
            return {"branch": "faq"}
        # 第 2 步：召回为空 → 走知识缺口分支（友好答复）
        if not state.get("recalled_units"):
            return {"branch": "gap"}
        # 第 3 步：正常链路 → 鉴权过滤后生成
        return {"branch": "permission"}

    return _merge


def _build_gap_node():
    """构造"召回为空"的友好答复节点。

    用 ``delta`` 事件下发答复 —— 它是**正常回答**，不是错误。
    这一点写错过一次：早期版本误用 ``error`` 事件，导致前端把"知识库里没有
    相关内容"显示成报错。
    """

    def _gap(state: dict) -> dict:
        emit_delta(GAP_ANSWER_TEXT)
        return {
            "answer_chunks": [GAP_ANSWER_TEXT],
            "trace": [record_trace("gap", 0, "召回为空，返回友好答复")],
        }

    return _gap


def build_qa_graph(session, container, *, top_k: int = 5, checkpointer=None):
    """构造并编译 AI 鉴权问答主图。

    :param session: 数据库会话（一次请求一个）。
    :param container: 应用级组件容器（提供 Milvus / 向量化 / 缓存 / 模型）。
    :param top_k: 召回条数。
    :param checkpointer: 会话持久化器；不传则用进程内的 ``InMemorySaver``。
        Redis Checkpointer 需要额外安装 ``langgraph-checkpoint-redis``，
        本环境未安装，因此默认进程内保存（重启即失）。

        **多轮上下文不依赖它**：第 14 章 #12 的"保留 10 轮"由接口层从
        ``qa_access_logs`` 按 ``session_id`` 取出后填进状态（见 ``QAState.history``），
        Checkpointer 只承担图自身的断点续跑，重启即失也不影响对话连续性。
    :return: 已编译的图，可用 ``.stream(state, stream_mode="custom")`` 驱动。
    """
    # 第 1 步：组装服务。全部依赖注入，图本身不 new 任何连接。
    # 权限引擎与召回门槛都取配置值（第 14 章 #8 / #9 / #10）
    settings = getattr(container, "settings", None)
    permission_engine = DataPermissionEngine(
        session,
        inherit_departments=getattr(settings, "dept_permission_inherit", True),
        admin_role_codes=getattr(settings, "admin_role_codes", ()),
    )
    vector_search = build_vector_search(container)
    llm_stream = container.llm_stream

    qa_service = AiQaService(
        session,
        permission_engine,
        vector_search,
        llm_stream if llm_stream is not None else (lambda _prompt: iter([GAP_ANSWER_TEXT])),
        default_top_k=top_k,
        vector_min_score=getattr(settings, "recall_similarity_threshold", 0.5),
    )
    faq_cache = FaqCacheService(session, container.cache, container.embedding)
    dashboard = DashboardService(session, container.cache)

    # 第 2 步：实例化 8 个 Agent 中参与在线链路的 6 个
    a1 = AuthGuardAgent(session)
    a2 = FAQCacheAgent(faq_cache)
    a3 = RecallAgent(qa_service, top_k=top_k)
    a4 = PermissionAgent(qa_service)
    a5 = AnswerAgent(qa_service, llm_stream)
    a6 = CitationAgent(qa_service)
    a7 = MetricsAgent(dashboard)

    # 第 3 步：搭图
    graph = StateGraph(QAState)
    graph.add_node("a1_auth", a1)
    graph.add_node("a2_faq", a2)
    graph.add_node("a3_recall", a3)
    graph.add_node("merge", _build_merge_node())
    graph.add_node("a4_permission", a4)
    graph.add_node("a5_answer", a5)
    graph.add_node("a6_citation", a6)
    graph.add_node("a7_metrics", a7)
    graph.add_node("gap", _build_gap_node())
    graph.add_node("deny", _deny_node)

    # 第 4 步：连边。A1 是硬门，未通过直接结束，不触发任何检索
    graph.add_edge(START, "a1_auth")
    graph.add_conditional_edges(
        "a1_auth",
        lambda state: ["a2_faq", "a3_recall"] if state.get("auth_ok") else ["deny"],
    )
    # 并行扇出 → 汇聚（等两路都完成，merge 只跑一次）
    graph.add_edge(["a2_faq", "a3_recall"], "merge")
    graph.add_conditional_edges(
        "merge",
        lambda state: state.get("branch") or "gap",
        {"faq": "a5_answer", "gap": "gap", "permission": "a4_permission"},
    )

    # 第 5 步：三轮链路
    graph.add_edge("a4_permission", "a5_answer")
    graph.add_edge("a5_answer", "a6_citation")
    graph.add_edge("a6_citation", "a7_metrics")
    graph.add_edge("a7_metrics", END)
    # 缓存命中路径跳过 A4/A6 的引用，但要落日志（11.1 每轮问答都记一条）
    graph.add_edge("gap", "a7_metrics")
    graph.add_edge("deny", END)

    # 第 6 步：编译
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


def _deny_node(state: dict) -> dict:
    """A1 未通过时的时间落点：只回填耗时，不产生回答。

    错误提示已在 A1 内部通过 ``error`` 事件下发（4.9 的异常路由）。
    """
    started = state.get("started_at") or time.monotonic()
    return {
        "answer_chunks": [],
        "response_time_ms": int((time.monotonic() - started) * 1000),
        "trace": [record_trace("deny", 0, "登录态或 AI 权限校验未通过")],
    }
