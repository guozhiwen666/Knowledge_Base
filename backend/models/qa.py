"""问答访问日志表模型。

对应 2.9.7 中的 1 张表：

    qa_access_logs    问答访问日志表

这张表同时承担三个职责，所以在字段上要一次说清：

1. **看板数据源** —— 2.9.6 要求异步记录每轮问答的提问内容、命中知识单元、
   Token 消耗与响应时长，2.9.9 的数据看板聚合规则全部从本表取数；
2. **知识沉淀数据源** —— 2.9.9 的 FAQ 挖掘与知识缺口识别都基于本表的历史提问；
3. **会话历史** —— 前端"历史对话列表"按 ``session_id`` 反查本表。

它与 LangGraph 主图状态 ``QAState`` 的字段命名刻意保持一致（三组单元 ID、
三个 Token 计数、响应时长），落盘时无需做字段名的二次映射。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, MediumTextType

__all__ = ["QaAccessLog"]


class QaAccessLog(Base):
    """问答访问日志表 ``qa_access_logs``。

    典型特征是**写多读少**：每条问答无条件写一行，读只在看板聚合与历史会话时发生。
    因此本表只有 ``created_at``，没有 ``updated_at``（日志一旦落库不再修改），
    也不设置任何外键，避免高频写入时被约束校验拖慢。

    三个单元 ID 列表列均为 JSON 数组，语义严格按 2.9.4 / 2.9.6 区分：

    * ``recalled_unit_ids_json``     召回（尚未鉴权）的候选知识单元
    * ``authorized_unit_ids_json``   鉴权通过、实际参与组装的单元
    * ``unauthorized_unit_ids_json`` 鉴权被拒、需在回复中提示缺失权限的单元
    """

    __tablename__ = "qa_access_logs"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    session_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="会话标识"
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="提问用户，对应 users.id"
    )
    question: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="提问内容"
    )
    answer: Mapped[str | None] = mapped_column(
        MediumTextType, nullable=True, comment="回答内容（MEDIUMTEXT）"
    )
    recalled_unit_ids_json: Mapped[list[int] | None] = mapped_column(
        JSON, nullable=True, comment="召回单元 id 列表"
    )
    authorized_unit_ids_json: Mapped[list[int] | None] = mapped_column(
        JSON, nullable=True, comment="授权单元 id 列表"
    )
    unauthorized_unit_ids_json: Mapped[list[int] | None] = mapped_column(
        JSON, nullable=True, comment="未授权单元 id 列表"
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="输入 Token 数"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="输出 Token 数"
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="总 Token 数"
    )
    response_time_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="接口响应时长，毫秒"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        comment="记录时间，看板按日聚合的分组依据",
    )

    __table_args__ = (
        Index("idx_log_session", "session_id"),
        Index("idx_log_user", "user_id"),
        Index("idx_log_created", "created_at"),
        {"comment": "问答访问日志表"},
    )
