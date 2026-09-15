"""AI 鉴权检索服务 · 历史对话（第 14 章 #4 的落地）。

2.9.3 要求 AI 对话工作台展示"历史对话列表"，但 2.9.8 没有列出对应接口。
这里给出数据来源实现。

**数据源是 ``qa_access_logs``，不另建会话表**：11.1 规定每轮问答都往这张表
记一条（含 ``session_id``），按 ``session_id`` 聚合天然就是"会话"，
再建一张会话表只会让同一件事有两份存储、两个可能不一致的真相。

**只返回自己发起的会话**：``user_id`` 来自令牌，不给"查别人的对话"留入口 ——
对话内容里可能包含他本无权访问的知识片段（A6 的权限提示、A5 的上下文引用），
越权读日志等于绕过了整套数据权限。管理员要审计请走独立的审计通道，不在本服务。
"""

from __future__ import annotations

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from models import KnowledgeUnit, QaAccessLog

__all__ = ["ConversationHistoryService"]

# 单个会话最多返回的问答轮数。超过部分只影响一次性加载量，不影响数据本身。
MAX_MESSAGES_PER_SESSION = 200


class ConversationHistoryService:
    """历史对话查询。

    依赖注入：会话由外部传入。本类**全部方法只读**，不写库。
    """

    def __init__(self, session: Session) -> None:
        """:param session: SQLAlchemy 会话。"""
        self._session = session

    # ------------------------------------------------------------------ 读

    def list_sessions(
        self,
        user_id: int,
        *,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        """分页查询当前用户的会话列表（``GET /api/ai/conversations``）。

        每个会话取"最后一轮"的提问作为摘要，并给出总轮数与最近活动时间 ——
        列表里只放一个 session_id 对使用者毫无意义。

        :param keyword: 按提问内容模糊过滤会话。
        :return: ``(total, items)``；每项含 ``session_id``、``last_question``、
            ``round_count``、``last_asked_at``。
        """
        # 第 1 步：入参兜底
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)

        # 第 2 步：总数 = 去重会话数。分页必须基于"会话数"而不是"日志条数"，
        # 否则一页 20 条可能只装得下 2 个会话
        total = self._session.execute(
            select(func.count(distinct(QaAccessLog.session_id))).where(
                QaAccessLog.user_id == user_id,
                *([QaAccessLog.question.like(f"%{keyword}%")] if keyword else []),
            )
        ).scalar_one()
        if not total:
            return 0, []

        # 第 3 步：取当页会话的聚合信息。用 max(id) 定位"最后一轮"——
        # 自增主键在同一个会话内严格递增，比按时间排序更稳（同秒写入也不乱序）
        aggregate = (
            select(
                QaAccessLog.session_id,
                func.max(QaAccessLog.id).label("last_id"),
                func.count(QaAccessLog.id).label("round_count"),
                func.max(QaAccessLog.created_at).label("last_asked_at"),
            )
            .where(QaAccessLog.user_id == user_id)
            .group_by(QaAccessLog.session_id)
            .order_by(func.max(QaAccessLog.id).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if keyword:
            aggregate = aggregate.where(QaAccessLog.question.like(f"%{keyword}%"))
        sessions = self._session.execute(aggregate).all()
        if not sessions:
            return total, []

        # 第 4 步：批量取这些"最后一轮"的提问内容，避免逐会话查库
        last_ids = [int(row[1]) for row in sessions]
        questions = {
            int(row[0]): (row[1] or "")
            for row in self._session.execute(
                select(QaAccessLog.id, QaAccessLog.question).where(
                    QaAccessLog.id.in_(last_ids)
                )
            ).all()
        }

        # 第 5 步：拼装
        items = [
            {
                "session_id": row[0],
                "last_question": questions.get(int(row[1]), ""),
                "round_count": int(row[2] or 0),
                "last_asked_at": row[3],
            }
            for row in sessions
        ]
        return total, items

    def get_session_messages(self, session_id: str, user_id: int) -> list[dict]:
        """取某个会话的全部问答轮（``GET /api/ai/conversations/{session_id}``）。

        按时间正序返回，与对话界面的阅读顺序一致。

        :param session_id: 会话标识。
        :param user_id: 当前用户；会话不属于他时按"不存在"处理。
        :return: 每项含 ``id``、``question``、``answer``、``cited_units``、
            ``total_tokens``、``response_time_ms``、``created_at``。
        """
        # 第 1 步：归属校验。会话里只要有一条不是该用户发的，就当他没这个会话 ——
        # 返回 404 而不是 403，避免通过状态码差异探测出"这个 session_id 存在"
        owner_rows = self._session.execute(
            select(QaAccessLog.user_id).where(
                QaAccessLog.session_id == session_id
            ).distinct()
        ).scalars().all()
        if not owner_rows or any(int(uid or 0) != user_id for uid in owner_rows):
            return []

        # 第 2 步：取全部轮次
        logs = (
            self._session.execute(
                select(QaAccessLog)
                .where(QaAccessLog.session_id == session_id)
                .order_by(QaAccessLog.id)
                .limit(MAX_MESSAGES_PER_SESSION)
            )
            .scalars()
            .all()
        )
        if not logs:
            return []

        # 第 3 步：批量补引用来源的标题（只取授权过的单元 —— 那才是真正给了内容的）
        unit_ids: set[int] = set()
        for log in logs:
            for raw in log.authorized_unit_ids_json or []:
                try:
                    unit_ids.add(int(raw))
                except (TypeError, ValueError):
                    continue
        titles = self._load_titles(unit_ids)

        # 第 4 步：拼装
        return [
            {
                "id": log.id,
                "question": log.question or "",
                "answer": log.answer or "",
                "cited_units": [
                    {"unit_id": int(raw), "title": titles.get(int(raw), "")}
                    for raw in (log.authorized_unit_ids_json or [])
                    if self._is_int(raw)
                ],
                "total_tokens": int(log.total_tokens or 0),
                "response_time_ms": int(log.response_time_ms or 0),
                "created_at": log.created_at,
            }
            for log in logs
        ]

    # ------------------------------------------------------------------ 内部

    def _load_titles(self, unit_ids: set[int]) -> dict[int, str]:
        """批量取知识单元标题，避免逐条查库。"""
        if not unit_ids:
            return {}
        return {
            int(row[0]): (row[1] or "")
            for row in self._session.execute(
                select(KnowledgeUnit.id, KnowledgeUnit.title).where(
                    KnowledgeUnit.id.in_(list(unit_ids))
                )
            ).all()
        }

    @staticmethod
    def _is_int(value) -> bool:
        """判断能否安全转成整数（JSON 列里的脏数据不该让整条记录报错）。"""
        try:
            int(value)
            return True
        except (TypeError, ValueError):
            return False
