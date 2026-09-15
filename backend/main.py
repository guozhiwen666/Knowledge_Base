"""FastAPI 应用入口。

启动：``cd backend && uvicorn main:app --host 127.0.0.1 --port 8000``

职责：

1. 注册 6 个路由分组（共 21 个接口，严格对应文档第 8 章）；
2. 注册统一异常处理（8.1 的响应格式与错误码）；
3. 打开 CORS —— 前端是独立的静态 SPA（2.9.5），与后端不同源；
4. 把 ``frontend/`` 挂到根路径，这样不起静态服务器也能直接访问页面；
5. 启动时打印组件自检结果，把"哪个外部依赖没连上"在日志里说清楚。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api import ai, auth, dashboard, knowledge, org, settlement
from core.config import BACKEND_ROOT, PROJECT_ROOT, get_settings
from core.container import get_container
from core.db import ping_database
from core.response import register_exception_handlers

__all__ = ["app", "create_app"]

logger = logging.getLogger("knowledge_base")

# 前端目录（纯静态 SPA）
FRONTEND_DIR = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动自检：把每个外部组件的连通情况打进日志。

    刻意不"连不上就退出" —— 组织架构、看板等接口不依赖 MinIO/Milvus，
    它们没起来时也该能用。哪一项不可用，日志里会明说。
    """
    settings = get_settings()
    logger.info("=" * 62)
    logger.info("知识库管理平台 启动中")
    logger.info("  配置来源：%s", PROJECT_ROOT / ".env")
    logger.info("  监听地址：http://%s:%s", settings.app_host, settings.app_port)

    # 第 1 步：数据库连通性（最关键，连不上则几乎全站不可用）
    ok_db, note_db = ping_database()
    logger.info("  %s %s", "✓" if ok_db else "✗", note_db)

    # 第 2 步：逐个触发外部组件构造，把自检说明打出来
    container = get_container()
    for component in ("cache", "object_storage", "milvus", "embedding", "llm_stream"):
        getattr(container, component)  # 访问属性即触发构造
    for note in container.notes:
        logger.info("  · %s", note)

    # 第 3 步：JWT 密钥缺失要显式告警（否则发出去的令牌无法校验）
    if not settings.jwt_enabled:
        logger.warning("  ✗ 未配置 JWT_SECRET_KEY，登录将失败")

    logger.info("  接口文档：http://%s:%s/docs", settings.app_host, settings.app_port)
    logger.info("=" * 62)
    yield
    logger.info("服务已停止")


def create_app() -> FastAPI:
    """构造 FastAPI 应用。"""
    settings = get_settings()
    app = FastAPI(
        title="知识库管理平台",
        version="1.0.0",
        description=(
            "知识维护与批量导入 · 四维数据权限 · AI 鉴权问答 · 数据看板 · 知识沉淀\n\n"
            "接口严格对应《技术方案设计文档》第 8 章的 21 个路由。"
        ),
        lifespan=lifespan,
    )

    # 第 1 步：统一异常处理（8.1 的响应包装与错误码）
    register_exception_handlers(app)

    # 第 2 步：CORS。前端静态站点与后端不同源，必须放行；
    # 演示环境用 "*"，生产应改为具体来源
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # 用 Bearer 令牌而非 Cookie，因此不需要凭证
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # 第 3 步：注册 6 个路由分组
    app.include_router(auth.router)
    app.include_router(org.router)
    app.include_router(knowledge.router)
    app.include_router(ai.router)
    app.include_router(dashboard.router)
    app.include_router(settlement.router)

    # 第 4 步：挂载前端静态站点。放在最后注册，保证 /api 路由优先匹配
    if FRONTEND_DIR.is_dir():
        app.mount(
            "/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend"
        )
        logger.info("已挂载前端静态目录：%s", FRONTEND_DIR)
    else:
        logger.warning("前端目录不存在，跳过挂载：%s", FRONTEND_DIR)

    return app


def configure_logging() -> None:
    """配置根日志，让启动自检与业务日志都能看到。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


configure_logging()
app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(app, host=_settings.app_host, port=_settings.app_port)
