"""知识单元管理服务 · 文档导入流水线。

承载《技术方案设计文档》5.3 中"从上传到向量化同步"的完整写入链路，
是知识单元管理服务里**唯一有副作用**的部分：

    生成编号 → 原始文件落 MinIO → 解析正文 → 文本切片
    → 写入 knowledge_units → 切片向量写入 Milvus → 提交

单文件（:meth:`KnowledgeUnitImporter.import_document`）与批量
（:meth:`KnowledgeUnitImporter.import_documents`）两种入口都在这里，
批量只是逐文件调用单文件并做结果归类，走的是同一条代码路径。

**失败处理原则**：先外部资源、后数据库行、最后向量，任一步失败都逐级清理，
不留"库里有、向量库没有"或"数据库有行、对象存储没文件"的半成品。

拆成独立模块的原因：主模块 ``knowledge.py`` 还要承担查询、更新、状态流转、
批量删除，把这段流水线放进去会让单文件远超 300 行。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4

from sqlalchemy.orm import Session

from models import KnowledgeUnit
from services.knowledge_unit_management_service.knowledge_parsers import (
    CHUNK_MAX_CHARS,
    CHUNK_MIN_CHARS,
    detect_file_type,
    parse_document,
    split_into_chunks,
)
from services.knowledge_unit_management_service.knowledge_storage import ObjectStorage, VectorStore

__all__ = [
    "UploadedFile",
    "UnsupportedFileType",
    "generate_unit_code",
    "KnowledgeUnitImporter",
    "ProgressFn",
    "STAGE_PARSING",
    "STAGE_CHUNKING",
    "STAGE_EMBEDDING",
    "STAGE_INDEXING",
]

# 解析阶段的上报回调：``(阶段名, 百分比)``。异步导入任务据此刷新进度。
ProgressFn = Callable[[str, int], None]

# 四个解析阶段与各自的进度百分比（见 2.9.5"解析状态进度轮询"）。
# 百分比刻意留出间隔，让前端轮询能观察到阶段跃迁，而不是一路匀速爬格子。
STAGE_PARSING = ("parsing", 20)
STAGE_CHUNKING = ("chunking", 45)
STAGE_EMBEDDING = ("embedding", 70)
STAGE_INDEXING = ("indexing", 90)


class UploadedFile(NamedTuple):
    """一个待导入的文件：只承载文件名与原始字节，不掺解析结果。

    :param file_name: 文件名，可带相对路径（目录批量导入场景）。
    :param data: 文件原始字节。
    """

    file_name: str
    data: bytes


class UnsupportedFileType(ValueError):
    """文件类型不在 2.9.3 声明的四类之内。

    批量导入时由调用方捕获并翻译成 8.4 响应里的 ``unsupported_format``。
    """


def generate_unit_code() -> str:
    """生成知识单元编号（``unit_code``）。

    格式 ``KU-YYYYMMDD-XXXXXXXX``，后 8 位为随机十六进制。

    需求只要求编号唯一、未规定格式，因此编号规则集中在这一处，
    将来要改成业务规则（例如按分类加前缀）只动这里。
    """
    return f"KU-{datetime.now():%Y%m%d}-{uuid4().hex[:8].upper()}"


class KnowledgeUnitImporter:
    """文档导入流水线。只做写入，查询与删除在服务主模块里。"""

    def __init__(
        self,
        session: Session,
        storage: ObjectStorage,
        vectors: VectorStore,
        *,
        chunk_max_chars: int = CHUNK_MAX_CHARS,
        chunk_min_chars: int = CHUNK_MIN_CHARS,
    ) -> None:
        """:param session: SQLAlchemy 会话，由调用方负责其生命周期。
        :param storage: MinIO 原始文件存储封装。
        :param vectors: Milvus 切片向量存储封装。
        :param chunk_max_chars: 切片长度阈值上限。
        :param chunk_min_chars: 尾块碎片判定阈值。
        """
        self._session = session
        self._storage = storage
        self._vectors = vectors
        self._max_chars = chunk_max_chars
        self._min_chars = chunk_min_chars

    def import_documents(
        self,
        files: Sequence[UploadedFile],
        creator_id: int | None = None,
        category: str | None = None,
        on_progress: Callable[[str, str, int], None] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """批量导入（``POST /api/knowledge/import`` 的服务侧实现）。

        返回结构对齐 8.4：``accepted`` 给出已入库的知识单元，
        ``rejected`` 给出被拒原因（``unsupported_format`` 或解析失败信息）。
        单个文件失败**不中断**其余文件 —— 批量导入里一个坏文件拖垮整批
        是最糟糕的体验，因此逐文件独立处理并隔离异常。

        :param files: 待导入文件列表，单文件时长度为 1。
        :param creator_id: 创建人。
        :param category: 统一指定的分类，可为空。
        :param on_progress: 可选。``(file_name, stage, percent)`` 形式的上报回调，
            异步导入任务用它刷新进度。同步调用不传即可，行为完全不变。
        """
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for uploaded in files:
            # 第 1 步：为每个文件包一层带文件名的回调，调用方不必自己记住是谁的进度。
            # 用默认参数把文件名绑进闭包，避免循环变量的后绑定陷阱
            progress: ProgressFn | None = None
            if on_progress is not None:
                progress = lambda stage, percent, _name=uploaded.file_name: (  # noqa: E731
                    on_progress(_name, stage, percent)
                )

            # 第 2 步：逐文件导入，格式不支持与解析失败分开归类
            try:
                unit, chunk_count = self.import_document(
                    uploaded.file_name,
                    uploaded.data,
                    creator_id,
                    category,
                    progress=progress,
                )
            except UnsupportedFileType:
                rejected.append(
                    {"file_name": uploaded.file_name, "reason": "unsupported_format"}
                )
                continue
            except Exception as exc:  # noqa: BLE001 - 批量场景需兜住所有失败
                rejected.append({"file_name": uploaded.file_name, "reason": str(exc)})
                continue

            # 第 3 步：记录成功项，便于前端逐文件展示解析结果
            accepted.append(
                {
                    "file_name": uploaded.file_name,
                    "unit_id": unit.id,
                    "unit_code": unit.unit_code,
                    "chunk_count": chunk_count,
                }
            )

        return {"accepted": accepted, "rejected": rejected}

    def import_document(
        self,
        file_name: str,
        data: bytes,
        creator_id: int | None = None,
        category: str | None = None,
        progress: ProgressFn | None = None,
    ) -> tuple[KnowledgeUnit, int]:
        """导入一个文件，返回（知识单元, 切片数量）。

        :param file_name: 文件名，可带相对路径（目录批量导入场景）。
        :param data: 文件原始字节。
        :param creator_id: 创建人，写入 ``knowledge_units.creator_id``。
        :param category: 分类，可为空。
        :param progress: 可选的阶段上报回调，见 :data:`STAGE_PARSING` 等四个常量。
        :raise UnsupportedFileType: 文件类型不在四类支持范围内。
        """
        # 第 1 步：判格式。不支持的直接拒绝，不做任何落盘动作
        file_type = detect_file_type(file_name)
        if file_type is None:
            raise UnsupportedFileType(f"不支持的文件类型：{file_name}")

        # 第 2 步：生成编号，后续的对象键与向量主键都依赖它
        unit_code = generate_unit_code()

        # 第 3 步：原始文件落 MinIO（5.3 第 1 项职责）
        object_key = self._storage.build_object_key(unit_code, file_name)
        self._storage.put_object(object_key, data)

        # 第 4 步：解析正文；失败则先清掉刚上传的原始文件再抛错
        _report(progress, STAGE_PARSING)
        try:
            text = parse_document(file_name, data)
        except Exception:
            self._storage.remove_object(object_key)
            raise

        # 第 5 步：切片。5.3 明确"每个独立导入的文档作为一个知识单元"，
        # 因此切片只是该单元下的检索粒度，不参与单元边界划分
        _report(progress, STAGE_CHUNKING)
        chunks = split_into_chunks(text, self._max_chars, self._min_chars)

        # 第 6 步：写入知识单元行。标题取文件名主干（title 列非空，必须给值）；
        # summary 列的生成方式需求未规定，保持为空，由编辑接口人工填写
        unit = KnowledgeUnit(
            unit_code=unit_code,
            title=Path(file_name).stem,
            content=text,
            category=category,
            source_file_name=Path(file_name).name,
            file_type=file_type,
            file_size=len(data),
            creator_id=creator_id,
        )
        self._session.add(unit)
        # flush 而非 commit：先拿到自增主键，向量切片要带上 unit_id
        self._session.flush()
        # status 的默认值由数据库侧 server_default 提供，新建对象上仍是 None，
        # 而 Milvus 标量字段不接受空值，故把真实值取回（只刷这一列）
        self._session.refresh(unit, attribute_names=["status"])

        # 第 7 步：向量化同步（5.3 第 6 项职责）。失败则该单元整体不成立 ——
        # 回滚数据库行并清掉原始文件，不留残缺数据
        _report(progress, STAGE_EMBEDDING)
        try:
            chunk_count = self._vectors.upsert_chunks(
                unit_id=unit.id,
                unit_code=unit.unit_code,
                category=unit.category,
                status=unit.status,
                chunks=chunks,
            )
        except Exception:
            self._session.rollback()
            self._storage.remove_object(object_key)
            raise

        # 第 8 步：全部成功才提交
        _report(progress, STAGE_INDEXING)
        self._session.commit()
        return unit, chunk_count


def _report(progress: ProgressFn | None, stage: tuple[str, int]) -> None:
    """上报一个阶段进度。没有回调时什么都不做。"""
    if progress is not None:
        progress(stage[0], stage[1])
