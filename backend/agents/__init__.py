"""多 Agent 联合层（对应文档 3.1 的 8 个 Agent）。

一个 Agent 一个类，职责边界严格按 3.1 划分：

================  ==========================  ==========================================
模块               Agent                        职责
================  ==========================  ==========================================
``guards``        A1 AuthGuardAgent           登录态、身份载入、AI 访问操作权限
``retrieval``     A2 FAQCacheAgent            FAQ 缓存精确 / 语义匹配
``retrieval``     A3 RecallAgent              向量 + 关键字混合召回（不鉴权）
``retrieval``     A4 PermissionAgent          四维数据权限过滤（图上唯一鉴权点）
``response``      A5 AnswerAgent              Prompt 组装与流式生成
``response``      A6 CitationAgent            引用来源与权限缺失提示
``platform``      A7 MetricsAgent             落访问日志
``platform``      A8 SettlementAgent          离线沉淀挖掘
================  ==========================  ==========================================

**Agent 之间不直接调用**（3.3）：所有协作都通过 LangGraph 共享状态完成，
每个 Agent 只写自己负责的字段，下游只读不写上游产出的字段。
本包的类都是"可调用的状态处理器"，签名统一为 ``__call__(state) -> dict``。
"""
