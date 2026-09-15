"""知识单元管理服务 · 异步导入任务与进度查询（第 14 章 #3 的落地）。

`POST /api/knowledge/import` 返回的 ``task_ids`` 就是这里的任务标识，
前端按 2.9.5 的要求轮询 `GET /api/knowledge/import/progress` 取得每个文件的
解析进度，全部到达终态后停止轮询。

**为什么必须做成异步**：2.9.3 / 2.9.5 要的是"上传 → 解析中 → 完成"的进度轮询。
若导入仍在请求内同步跑完，请求返回时任务已经结束，进度永远是 100%，
轮询就成了一场表演。因此接口层只做**接收与格式校验**（很快），
把"解析 + 切片 + 向量化"整段丢到后台线程，前端才能观察到真实的阶段跃迁。

**进度状态机**：

    queued ──→ running ──→ completed
                     └──→ failed

``completed`` 与 ``failed`` 是终态（见 :data:`TERMINAL_STATUSES`）。

**进度存在缓存里而不是数据库**：2.9.7 的 10 张表里没有任务表，
而进度是"看完即弃"的临时数据，塞进关系库只会给审计表添噪音。
缓存客户端只需提供 ``get`` / ``set``（鸭子类型，与 5.8 的做法一致）。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

from sqlalchemy.orm import Session

from services.knowledge_unit_management_service.knowledge_importer import (
    KnowledgeUnitImporter,
    UnsupportedFileType,
)

__all__ = [
    "ImportTaskStore",
    "ImportTaskRunner",
    "QueuedFile",
    "TERMINAL_STATUSES",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
]

# 任务状态取值
STATUS_QUEUED = "queued"        # 已接收，等待后台线程取走
STATUS_RUNNING = "running"      # 解析中（含具体阶段）
STATUS_COMPLETED = "completed"  # 已入库
STATUS_FAILED = "failed"        # 失败（格式不支持或解析异常）

# 终态集合：前端轮询到这些状态就可以停了（9.4"全部任务终态后停止"）
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED})

# 缓存键前缀与单次批量导入的默认并发度
TASK_KEY_PREFIX = "kb:import:task:"
DEFAULT_MAX_WORKERS = 3


class QueuedFile(NamedTuple):
    """一个已接收、待后台解析的文件。

    :param task_id: 任务标识，接口层回给前端的 ``task_ids``。
    :param file_name: 原始文件名。
    :param data: 文件字节（已在请求内读全，后台线程不再持有请求对象）。
    """

    task_id: str
    file_name: str
    data: bytes


class ImportTaskStore:
    """导入任务的进度存储（缓存客户端之上的薄封装）。

    写入不设过期：任务键的数量与导入次数同阶，对内存与 Redis 都不构成压力；
    真要清理可以按 :data:`TASK_KEY_PREFIX` 批量删。加 TTL 需要缓存客户端支持
    ``setex``，而 5.8 的缓存契约只约定 ``get`` / ``set`` / ``delete`` /
    ``incrby`` 四个方法 —— 不为这个功能单方面扩大契约。
    """

    def __init__(self, cache) -> None:
        """:param cache: 缓存客户端，需提供 ``get`` / ``set``。传 ``None`` 表示不记录。"""
        self._cache = cache
        self._lock = threading.Lock()

    def create(
        self,
        task_id: str,
        file_name: str,
        *,
        creator_id: int | None = None,
        category: str | None = None,
    ) -> dict:
        """建一条任务记录，初始状态 ``queued``。

        创建人、分类一并记在任务里：后台线程只能拿到任务标识，
        这些上下文不随请求对象存活。
        """
        record = {
            "task_id": task_id,
            "file_name": file_name,
            "status": STATUS_QUEUED,
            "stage": "queued",
            "percent": 0,
            "creator_id": creator_id,
            "category": category,
            "unit_id": None,
            "unit_code": None,
            "chunk_count": 0,
            "reason": None,
        }
        self._write(task_id, record)
        return record

    def update(self, task_id: str, **fields: Any) -> dict | None:
        """就地更新任务记录的若干字段。

        用锁保护"读-改-写"：同一任务只会被一个后台线程写，
        但同一次批量导入的多个任务共用一个缓存客户端，
        内存兜底实现虽是线程安全的，跨"读"与"写"两步仍需自己串起来。
        """
        with self._lock:
            record = self.get(task_id)
            if record is None:
                return None
            record.update(fields)
            self._write(task_id, record)
            return record

    def get(self, task_id: str) -> dict | None:
        """读一条任务记录，不存在或缓存不可用返回 ``None``。"""
        if self._cache is None:
            return None
        try:
            raw = self._cache.get(self._key(task_id))
        except Exception:  # noqa: BLE001 - 进度查不到不该影响其它功能
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def get_many(self, task_ids: Sequence[str]) -> list[dict]:
        """按传入顺序批量取任务记录，查不到的跳过（不返回占位项）。

        跳过的语义是"这个 task_id 我们没接收到"或"记录已被清理"，
        由接口层在响应里通过 ``missing_task_ids`` 如实说明，
        而不是伪造一条状态为失败的假记录。
        """
        found: list[dict] = []
        for task_id in task_ids:
            record = self.get(task_id)
            if record is not None:
                found.append(record)
        return found

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _key(task_id: str) -> str:
        """任务记录的缓存键。"""
        return f"{TASK_KEY_PREFIX}{task_id}"

    def _write(self, task_id: str, record: dict) -> None:
        """写缓存；失败一律吞掉（进度丢一条不影响导入结果本身）。"""
        if self._cache is None:
            return
        try:
            self._cache.set(
                self._key(task_id), json.dumps(record, ensure_ascii=False)
            )
        except Exception:  # noqa: BLE001
            return


class ImportTaskRunner:
    """把已接收的文件丢到后台线程池执行，并逐阶段刷新进度。

    依赖注入：``session_factory`` 与 ``importer_factory`` 都由接口层传入，
    本类不 import ``core`` —— 服务层与基础设施层保持单向依赖（接口层依赖两者）。

    **每个任务一个独立会话**：单个文件失败只回滚它自己的事务，
    不会把同批次其它文件的解析结果一起带走。
    """

    def __init__(
        self,
        store: ImportTaskStore,
        session_factory: Callable[[], Session],
        importer_factory: Callable[[Session], KnowledgeUnitImporter],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """:param store: 进度存储。
        :param session_factory: 无参的会话工厂，后台线程内自行取会话。
        :param importer_factory: 由会话构造导入流水线的工厂函数。
        :param max_workers: 并发解析的线程数，与前端"并发度"上限对齐。
        """
        self._store = store
        self._session_factory = session_factory
        self._importer_factory = importer_factory
        self._pool = ThreadPoolExecutor(
            max_workers=max(int(max_workers), 1), thread_name_prefix="kb-import"
        )

    def submit(self, files: Sequence[QueuedFile]) -> None:
        """把一批文件提交到后台执行，立即返回。

        这里**不等结果** —— 接口层已经把它们标成 ``queued`` 回应给前端了，
        等待就又把同步阻塞请了回来。
        """
        for item in files:
            self._pool.submit(self._run_one, item)

    def _run_one(self, item: QueuedFile) -> None:
        """执行单个文件的导入，全程把进度写回存储。"""
        record = self._store.get(item.task_id) or {}
        creator_id = record.get("creator_id")
        category = record.get("category")

        # 第 1 步：置为解析中，让前端立刻能看到状态变化
        self._store.update(
            item.task_id, status=STATUS_RUNNING, stage="starting", percent=5
        )

        def progress(stage: str, percent: int) -> None:
            """流水线上报的阶段进度，直接落到任务记录里。"""
            self._store.update(
                item.task_id, status=STATUS_RUNNING, stage=stage, percent=percent
            )

        # 第 2 步：独立会话 + 独立事务
        session = self._session_factory()
        try:
            importer = self._importer_factory(session)
            unit, chunk_count = importer.import_document(
                item.file_name,
                item.data,
                creator_id,
                category,
                progress=progress,
            )
        except UnsupportedFileType:
            # 格式不支持：接口层已按扩展名挡过一次，这里是解析侧的兜底
            self._store.update(
                item.task_id,
                status=STATUS_FAILED,
                stage="failed",
                percent=100,
                reason="unsupported_format",
            )
        except Exception as exc:  # noqa: BLE001 - 后台线程必须兜住所有异常
            self._store.update(
                item.task_id,
                status=STATUS_FAILED,
                stage="failed",
                percent=100,
                reason=f"{type(exc).__name__}: {exc}",
            )
        else:
            # 第 3 步：成功，回填入库产物，前端可直接跳转到详情
            self._store.update(
                item.task_id,
                status=STATUS_COMPLETED,
                stage="completed",
                percent=100,
                unit_id=unit.id,
                unit_code=unit.unit_code,
                chunk_count=chunk_count,
            )
        finally:
            session.close()

    def shutdown(self, wait: bool = False) -> None:
        """停止线程池（进程退出或测试收尾用）。"""
        self._pool.shutdown(wait=wait)
