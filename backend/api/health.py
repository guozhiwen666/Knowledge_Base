"""健康检查接口（部署就绪度补齐，P0-5）。

编排层（Docker / K8s）需要可探测的存活与就绪端点：

* ``GET /api/health/live``   —— 进程活着即返回 200，不依赖任何外部组件
* ``GET /api/health/ready``  —— 依赖 MySQL 连通才返回 200（就绪探针）
* ``GET /api/health``        —— 汇总：db 探活 + 各外部组件状态（来自启动自检 notes）

说明：本文件是"上线部署"需要的基础设施接口，不计入 8.8 的 41 个业务路由。
"""

from __future__ import annotations

from fastapi import APIRouter

from core.container import get_container
from core.db import ping_database
from core.response import ok

__all__ = ["router"]

router = APIRouter(prefix="/api/health", tags=["健康检查"])


@router.get("/live", summary="存活探针")
def live() -> dict:
    """进程存活即可，不碰任何外部依赖。供 K8s livenessProbe / Docker healthcheck。"""
    return ok({"status": "alive"})


@router.get("/ready", summary="就绪探针")
def ready() -> dict:
    """MySQL 连通才算就绪；外部组件（MinIO/Milvus/Redis）缺失不阻断。"""
    ok_db, note_db = ping_database()
    return ok(
        {"status": "ready" if ok_db else "not_ready", "db": note_db},
        message="ok" if ok_db else note_db,
    )


@router.get("", summary="综合健康状态")
def health() -> dict:
    """汇总数据库与各外部组件状态（组件状态在首次访问容器时由启动自检填充）。"""
    ok_db, note_db = ping_database()
    container = get_container()
    components = list(container.notes)  # 各组件连接情况说明
    all_ok = ok_db
    return ok(
        {
            "status": "ok" if all_ok else "degraded",
            "db": note_db,
            "components": components,
        }
    )
