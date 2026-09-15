"""种子数据脚本：写入演示所需的部门、角色、权限、用户、知识单元与 FAQ。

设计目标：**让 2.9.11 的八条验收标准每一条都有现成数据可验**，因此数据不是
随便造的，而是刻意覆盖了四维数据权限的四种情况与"默认拒绝"：

====================================  ==========================================
知识单元                              数据权限（演示用）
====================================  ==========================================
差旅报销管理办法                      ``global`` —— 人人可见
财务内控手册                          ``department: 财务部`` —— 只 bob 可见
知识库平台操作指南                    ``role: 知识管理员`` —— 只 kbadmin 可见
项目立项流程说明                      ``user: alice`` —— 只 alice 可见
内部审计要点                          无任何记录 —— **默认拒绝**（除管理员外谁都看不到）
====================================  ==========================================

演示账号（口令统一 ``123456``，仅用于本地演示）：

    admin    系统管理员   总部
    kbadmin  知识管理员   知识运营部
    alice    普通用户     知识运营部
    bob      普通用户     财务部
    carol    普通用户     技术部

执行：``python scripts/seed_data.py [--reset]``
（``--reset`` 会先清空十张表再写入，用于反复演示）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从项目根直接运行（导入根是 backend/）
BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, delete, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from core.cache import build_cache  # noqa: E402
from core.config import get_settings  # noqa: E402
from models import (  # noqa: E402
    Department,
    Faq,
    KnowledgeGap,
    KnowledgeUnit,
    QaAccessLog,
    Role,
    RolePermission,
    UnitPermission,
    User,
    UserRole,
)
from services.FAQ_cache_ervice.faq_cache import FaqCacheService  # noqa: E402
from services.authentication_and_authorization_module.passwords import (  # noqa: E402
    hash_password,
)

# 演示口令。仅用于本地演示。
# 部署前必须：① 改为强口令；② 或直接删除演示账号（见下方 DEMO_USERS）。
# 可通过环境变量 KB_DEMO_PASSWORD 覆盖（不传默认 123456，仅供本地演示）。
import os

DEMO_PASSWORD = os.getenv("KB_DEMO_PASSWORD", "123456")

# 权限码。取自文档 6.1 的权限编码表（menu / operation / ai 三类）
PERMISSIONS_BY_ROLE = {
    "sys_admin": [
        ("menu:org", "menu"),
        ("menu:knowledge", "menu"),
        ("menu:dashboard", "menu"),
        ("menu:settlement", "menu"),
        ("knowledge:unit:create", "operation"),
        ("knowledge:unit:read", "operation"),
        ("knowledge:unit:update", "operation"),
        ("knowledge:unit:delete", "operation"),
        ("ai:chat:access", "ai"),
    ],
    "kb_admin": [
        ("menu:knowledge", "menu"),
        ("menu:settlement", "menu"),
        ("knowledge:unit:create", "operation"),
        ("knowledge:unit:read", "operation"),
        ("knowledge:unit:update", "operation"),
        ("knowledge:unit:delete", "operation"),
        ("ai:chat:access", "ai"),
    ],
    # 普通用户只做一件事：登录后提问（2.9.2）
    "user": [("ai:chat:access", "ai")],
}

# 知识单元正文。刻意写成有区分度的内容，便于用关键字检索验证召回与鉴权
UNITS = [
    {
        "unit_code": "KU-DEMO-001",
        "title": "差旅报销管理办法",
        "category": "制度",
        "summary": "差旅报销的标准、流程与凭证要求",
        "content": (
            "第一章 总则\n员工因公出差产生的交通费、住宿费与市内交通费，按本办法报销。\n\n"
            "第二章 报销标准\n职级 P1-P3 每日住宿标准 400 元，P4-P6 每日 600 元，P7 及以上每日 900 元。\n"
            "交通费按实际发生额报销，市内交通每日上限 150 元。\n\n"
            "第三章 报销流程\n出差结束后 15 个工作日内提交报销单，附发票原件与出差审批单，"
            "经直属主管审批后交财务审核，审核通过后 5 个工作日内打款。\n\n"
            "第四章 凭证要求\n发票抬头须为公司全称，住宿发票需注明入住与离店日期。"
        ),
        "permissions": [("global", 0)],
    },
    {
        "unit_code": "KU-DEMO-002",
        "title": "财务内控手册",
        "category": "制度",
        "summary": "资金审批权限与内部牵制要求",
        "content": (
            "第一章 资金审批权限\n单笔付款 5 万元以下由部门负责人审批，5 万至 50 万元由财务负责人审批，"
            "50 万元以上需总经理审批。\n\n第二章 内部牵制\n出纳不得兼任稽核与会计档案保管。付款申请与付款执行必须由不同人完成。\n\n"
            "第三章 银行账户管理\n银行账户开立与变更须经财务负责人与总经理双签。"
        ),
        "permissions": [("department", "财务部")],  # 部门 id 在写入时按名称解析
    },
    {
        "unit_code": "KU-DEMO-003",
        "title": "知识库平台操作指南",
        "category": "操作指南",
        "summary": "知识单元导入、权限配置与 FAQ 审核的操作步骤",
        "content": (
            "一、导入知识\n在「知识维护与导入页」拖拽上传 PDF、Markdown、Word 或 TXT 文件，"
            "系统会解析正文、切分为检索切片并写入向量库。\n\n"
            "二、配置数据权限\n每个知识单元默认没有任何访问权限。需要在「数据权限配置」弹窗中勾选"
            "全局公开、指定部门、指定角色或指定人员，满足任意一种即可访问。\n\n"
            "三、审核 FAQ\n在「知识沉淀管理页」查看系统从历史对话中挖掘出的高频问题，"
            "确认答案后点击审核通过，该问答对会被写入缓存并在后续提问时直接命中。"
        ),
        "permissions": [("role", "知识管理员")],  # 角色 id 在写入时按名称解析
    },
    {
        "unit_code": "KU-DEMO-004",
        "title": "项目立项流程说明",
        "category": "流程",
        "summary": "立项申请、评审与预算确认流程",
        "content": (
            "项目立项分为四个步骤：需求提出、可行性评审、预算确认、立项批复。\n"
            "需求由业务部门提出并填写立项申请单；评审会由技术委员会组织，"
            "重点评估技术方案与资源投入；预算由财务部核定；最后由总经理签批立项。\n"
            "整个流程通常需要 10 个工作日。"
        ),
        "permissions": [("user", "alice")],  # 用户 id 在写入时按用户名解析
    },
    {
        "unit_code": "KU-DEMO-005",
        "title": "内部审计要点",
        "category": "制度",
        "summary": "内部审计的范围与关注重点",
        "content": (
            "内部审计每年至少开展一次，覆盖财务收支、采购流程、资金安全与合规性。"
            "重点关注大额资金往来的审批完整性、供应商准入记录以及报销凭证的真实性。"
        ),
        # 刻意不配置任何权限：演示 2.9.4 的"默认无权限"
        "permissions": [],
    },
]

# 人工录入的已发布 FAQ，用于演示「已发布 FAQ 库」与缓存精确命中
MANUAL_FAQS = [
    {
        "question": "差旅报销标准是什么？",
        "answer": (
            "住宿：P1-P3 每日 400 元，P4-P6 每日 600 元，P7 及以上每日 900 元；"
            "市内交通每日上限 150 元。出差结束后 15 个工作日内提交报销单。"
        ),
        "category": "制度",
        "related_unit_code": "KU-DEMO-001",
    },
]


def truncate_all(session: Session) -> None:
    """清空十张表（``--reset`` 时调用）。顺序无所谓，因为没有外键约束。"""
    for model in (
        QaAccessLog,
        KnowledgeGap,
        Faq,
        UnitPermission,
        KnowledgeUnit,
        UserRole,
        RolePermission,
        Role,
        User,
        Department,
    ):
        session.execute(delete(model))
    session.commit()


def seed(session: Session) -> dict:
    """写入全部演示数据，返回写入统计。"""
    # 第 1 步：部门（两棵树：总部下挂三个部门）
    hq = Department(parent_id=None, name="总部", sort_order=0)
    session.add(hq)
    session.flush()
    kb_dept = Department(parent_id=hq.id, name="知识运营部", sort_order=1)
    fin_dept = Department(parent_id=hq.id, name="财务部", sort_order=2)
    tech_dept = Department(parent_id=hq.id, name="技术部", sort_order=3)
    session.add_all([kb_dept, fin_dept, tech_dept])
    session.flush()

    # 第 2 步：角色 + 角色权限
    roles: dict[str, Role] = {}
    for code, name, desc in (
        ("sys_admin", "系统管理员", "创建维护用户角色部门，分配操作权限，监控看板"),
        ("kb_admin", "知识管理员", "导入编辑知识单元，配置数据权限，审核 FAQ"),
        ("user", "普通用户", "登录进行 AI 智能问答"),
    ):
        role = Role(role_name=name, role_code=code, description=desc)
        session.add(role)
        session.flush()
        roles[code] = role
        for permission_code, permission_type in PERMISSIONS_BY_ROLE[code]:
            session.add(
                RolePermission(
                    role_id=role.id,
                    permission_code=permission_code,
                    permission_type=permission_type,
                )
            )

    # 第 3 步：用户 + 用户角色。口令统一哈希后入库，不存明文
    password_hash = hash_password(DEMO_PASSWORD)
    users: dict[str, User] = {}
    for username, display_name, dept, role_code in (
        ("admin", "系统管理员", hq, "sys_admin"),
        ("kbadmin", "知识管理员", kb_dept, "kb_admin"),
        ("alice", "Alice", kb_dept, "user"),
        ("bob", "Bob", fin_dept, "user"),
        ("carol", "Carol", tech_dept, "user"),
    ):
        user = User(
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            department_id=dept.id,
            status=1,
        )
        session.add(user)
        session.flush()
        session.add(UserRole(user_id=user.id, role_id=roles[role_code].id))
        users[username] = user

    # 第 4 步：权限实体的名称 → id 解析表。刻意用名称而不是硬编码 id，
    # 换环境（部门/角色/用户 id 必然不同）时种子数据不会指错实体
    resolvers: dict[str, dict[str, int]] = {
        "department": {"财务部": fin_dept.id},
        "role": {"知识管理员": roles["kb_admin"].id},
        "user": {"alice": users["alice"].id},
    }

    # 第 5 步：知识单元 + 数据权限
    creator = users["kbadmin"]
    units: dict[str, KnowledgeUnit] = {}
    for item in UNITS:
        unit = KnowledgeUnit(
            unit_code=item["unit_code"],
            title=item["title"],
            content=item["content"],
            summary=item["summary"],
            category=item["category"],
            source_file_name=f"{item['title']}.md",
            file_type="md",
            file_size=len(item["content"].encode("utf-8")),
            status="active",
            creator_id=creator.id,
        )
        session.add(unit)
        session.flush()
        units[item["unit_code"]] = unit
        # 逐条写权限实体；global 的 target_id 固定为 0（2.9.4）
        for target_type, entity in item["permissions"]:
            target_id = 0 if target_type == "global" else resolvers[target_type][entity]
            session.add(
                UnitPermission(
                    unit_id=unit.id, target_type=target_type, target_id=target_id
                )
            )

    # 第 6 步：人工录入的已发布 FAQ（source_type=manual）
    faqs: list[Faq] = []
    for item in MANUAL_FAQS:
        faq = Faq(
            question=item["question"],
            answer=item["answer"],
            category=item["category"],
            related_unit_id=units[item["related_unit_code"]].id,
            source_type="manual",
            status="published",
        )
        session.add(faq)
        faqs.append(faq)

    session.commit()
    return {
        "departments": 4,
        "roles": len(roles),
        "users": len(users),
        "units": len(units),
        "unit_objects": units,
        "faqs": len(faqs),
    }


def vectorize_seed_units(session: Session, units: dict) -> list[str]:
    """把种子知识单元也写入 Milvus 向量库。

    **为什么必须做这一步**：知识单元只有走 ``POST /api/knowledge/import`` 才会被
    切片向量化；种子数据是直接写库的，若不补这一步，这 5 个单元就只存在于 MySQL，
    向量召回永远找不到它们 —— AI 问答会退化成"只靠关键字匹配"，
    而权限演示所依赖的正是这几个单元。

    失败时只记录说明、不中断：没有 Milvus 或模型的环境仍然应当能写入种子数据。
    """
    notes: list[str] = []
    try:
        from core.container import get_container
        from services.knowledge_unit_management_service.knowledge_parsers import (
            split_into_chunks,
        )

        vectors = get_container().vector_store
        if vectors is None:
            return ["向量存储未就绪，跳过种子单元向量化（AI 问答将只依赖关键字检索）"]

        total = 0
        for unit in units.values():
            chunks = split_into_chunks(unit.content or "")
            total += vectors.upsert_chunks(
                unit_id=unit.id,
                unit_code=unit.unit_code,
                category=unit.category,
                status=unit.status,
                chunks=chunks,
            )
        notes.append(f"已把 {len(units)} 个种子知识单元写入向量库，共 {total} 个切片")
    except Exception as exc:  # noqa: BLE001 - 向量化失败不影响种子数据本身
        notes.append(f"种子单元向量化失败：{type(exc).__name__}: {exc}")
    return notes


def warm_faq_cache(session: Session, stats: dict) -> list[str]:
    """把已发布 FAQ 预热进缓存。

    4.4 的缓存写入时机是"审核通过"，这里额外做一次预热是为了让演示不必先手动
    审核一轮。**不传向量化函数**，因此只建立精确匹配键，不调用外部模型接口。
    """
    notes: list[str] = []
    try:
        cache, note = build_cache(get_settings())
        notes.append(note)
        service = FaqCacheService(session, cache, None)
        published = list(
            session.execute(select(Faq).where(Faq.status == "published")).scalars()
        )
        for faq in published:
            service.publish(faq)
        notes.append(f"已预热 {len(published)} 条已发布 FAQ 到缓存（仅精确匹配键）")
    except Exception as exc:  # noqa: BLE001 - 预热失败不影响种子数据
        notes.append(f"FAQ 缓存预热失败：{type(exc).__name__}: {exc}")
    return notes


def main() -> int:
    """入口：建连接 → 可选清空 → 写数据 → 预热缓存 → 打印统计。"""
    parser = argparse.ArgumentParser(description="写入知识库平台演示种子数据")
    parser.add_argument("--reset", action="store_true", help="先清空十张表再写入")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url, future=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        # 第 1 步：可选清空
        if args.reset:
            truncate_all(session)
            print("  已清空十张表")

        # 第 2 步：幂等检查，避免重复写入把唯一约束撞坏
        existing = session.execute(select(User.id).limit(1)).scalar_one_or_none()
        if existing is not None:
            print("  检测到已有用户数据，跳过写入。需要重写请加 --reset")
            return 0

        # 第 3 步：写种子数据
        stats = seed(session)
        print(
            "  已写入："
            f"部门 {stats['departments']}、角色 {stats['roles']}、"
            f"用户 {stats['users']}、知识单元 {stats['units']}、"
            f"已发布 FAQ {stats['faqs']}"
        )

        # 第 4 步：把种子单元也写进向量库，否则向量召回找不到它们
        for note in vectorize_seed_units(session, stats.get("unit_objects") or {}):
            print(f"  {note}")

        # 第 5 步：预热 FAQ 缓存
        for note in warm_faq_cache(session, stats):
            print(f"  {note}")

    print("  完成。演示账号：admin / kbadmin / alice / bob / carol，口令 123456")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
