"""知识沉淀挖掘服务 · 知识缺口状态流转（第 14 章 #7 的落地）。

11.4 规定缺口的流转是 ``unresolved`` →（补全知识单元后）``resolved``
并写 ``resolved_unit_id``；无效项可置 ``ignored``。
对应接口原先未在 8 章列出，这里给出实现。

**"一键建档"为什么不在这里造单元**：11.4 说"补全知识单元后置 resolved"，
补全动作本身属于 5.3 知识单元管理服务。若在这里再实现一遍单元创建，
必然出现"新建单元"与"导入单元"两条不同的写入路径，
向量化与校验规则迟早漂移。因此本服务只做**关联与状态流转**，
新单元由接口层调用知识单元管理服务建成后再把 id 交进来。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models import KnowledgeGap, KnowledgeUnit
from models.enums import KnowledgeGapStatus

__all__ = ["KnowledgeGapService"]


class KnowledgeGapService:
    """知识缺口的状态流转。

    依赖注入：会话由外部传入。事务边界：每个写方法自行 commit。
    """

    def __init__(self, session: Session) -> None:
        """:param session: SQLAlchemy 会话。"""
        self._session = session

    # ------------------------------------------------------------------ 写

    def resolve(self, gap_id: int, unit_id: int) -> KnowledgeGap:
        """把缺口置为已解决并关联补全的知识单元（11.4）。

        :param gap_id: 缺口 id。
        :param unit_id: 补全该缺口的知识单元 id，必须真实存在 ——
            指向一个不存在的单元会让"已解决"变成一句空话，
            查的时候还得再翻一次库才知道是假的。
        :raise LookupError: 缺口或知识单元不存在。
        :raise ValueError: 缺口已是终态（已解决 / 已忽略）。
        """
        # 第 1 步：取缺口
        gap = self._session.get(KnowledgeGap, gap_id)
        if gap is None:
            raise LookupError(f"知识缺口不存在：id={gap_id}")
        # 第 2 步：终态校验。repeated 调用同一缺口多半是重复点击，
        # 明确报错比静默覆盖更利于发现问题
        if gap.status == KnowledgeGapStatus.RESOLVED.value:
            raise ValueError("该知识缺口已解决，无需重复处理")
        if gap.status == KnowledgeGapStatus.IGNORED.value:
            raise ValueError("该知识缺口已被忽略，请先恢复后再补全")
        # 第 3 步：校验知识单元存在
        if self._session.get(KnowledgeUnit, unit_id) is None:
            raise LookupError(f"知识单元不存在：id={unit_id}")
        # 第 4 步：流转并提交
        gap.status = KnowledgeGapStatus.RESOLVED.value
        gap.resolved_unit_id = unit_id
        self._session.commit()
        return gap

    def ignore(self, gap_id: int) -> KnowledgeGap:
        """把缺口置为已忽略（11.4："无效项可置 ignored"）。

        忽略后不再纳入未解决清单，但记录保留 —— 样本提问还有参考价值。

        :raise LookupError: 缺口不存在。
        :raise ValueError: 缺口已解决（已补全的知识不该被反悔成忽略）。
        """
        # 第 1 步：取缺口
        gap = self._session.get(KnowledgeGap, gap_id)
        if gap is None:
            raise LookupError(f"知识缺口不存在：id={gap_id}")
        # 第 2 步：已解决的不能忽略。补全过的知识是真的存在了，
        # 把它标成忽略会让统计口径错乱（缺口数少一个、知识单元却还在）
        if gap.status == KnowledgeGapStatus.RESOLVED.value:
            raise ValueError("该知识缺口已解决，无法再忽略")
        # 第 3 步：流转并提交
        gap.status = KnowledgeGapStatus.IGNORED.value
        self._session.commit()
        return gap
