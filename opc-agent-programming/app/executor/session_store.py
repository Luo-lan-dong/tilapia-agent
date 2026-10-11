"""会话登记簿：持久化 task_id 到 tilacode 会话的映射。

以 JSON 文件（如 ``data/sessions.json``）为存储介质，记录每个任务的
session_id、工作目录与状态；使用 asyncio.Lock 保证并发安全，
原子写（临时文件 + os.replace）避免写一半导致文件损坏。
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# 临时文件名（与登记簿同目录），写入完成后 os.replace 覆盖目标文件
_TMP_FILE_NAME: str = ".sessions.json.tmp"


class SessionStoreError(Exception):
    """会话登记簿操作异常，携带操作类型、文件路径与失败原因。"""

    def __init__(self, operation: str, file_path: Path, reason: str) -> None:
        self.operation = operation
        self.file_path = file_path
        self.reason = reason
        super().__init__(f"会话登记簿操作失败（{operation}）：{file_path}，原因：{reason}")


@dataclass
class SessionRecord:
    """单条会话记录：内存中使用 datetime，落盘时转为 UTC ISO 字符串。"""

    task_id: str
    session_id: str
    workspace_dir: str
    status: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, str]:
        """序列化为可 JSON 存储的字典（时间字段转 UTC ISO 字符串）。"""
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "workspace_dir": self.workspace_dir,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "SessionRecord":
        """从字典反序列化；时间字段缺失时回退为当前 UTC 时间。"""
        record = cls(
            task_id=data["task_id"],
            session_id=data["session_id"],
            workspace_dir=data["workspace_dir"],
            status=data["status"],
        )
        if data.get("created_at"):
            record.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("updated_at"):
            record.updated_at = datetime.fromisoformat(data["updated_at"])
        return record


class SessionStore:
    """基于 JSON 文件的会话登记簿。

    文件格式为 ``{"task_id": {..记录..}, ...}``；读取容错：
    文件不存在返回空表，JSON 损坏记录 warning 日志并当作空表重建。
    """

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = asyncio.Lock()
        self._logger = logging.getLogger(__name__)

    @property
    def file_path(self) -> Path:
        """返回登记簿文件路径。"""
        return self._file_path

    async def get(self, task_id: str) -> SessionRecord | None:
        """查询指定任务的会话记录，不存在时返回 None。"""
        async with self._lock:
            data = self._load().get(task_id)
            if data is None:
                return None
            return SessionRecord.from_dict(data)

    async def upsert(
        self,
        task_id: str,
        *,
        session_id: str,
        workspace_dir: str,
        status: str,
    ) -> SessionRecord:
        """新建或更新记录：已有记录保留未传入字段（如 created_at），覆盖传入字段。"""
        async with self._lock:
            records = self._load()
            now = datetime.now(timezone.utc)
            existing = records.get(task_id)
            if existing is None:
                record = SessionRecord(
                    task_id=task_id,
                    session_id=session_id,
                    workspace_dir=workspace_dir,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            else:
                record = SessionRecord.from_dict(existing)
                record.session_id = session_id
                record.workspace_dir = workspace_dir
                record.status = status
                record.updated_at = now
            records[task_id] = record.to_dict()
            self._save(records)
            self._logger.info(
                "session_upserted",
                extra={
                    "task_id": task_id,
                    "session_id": session_id,
                    "status": status,
                    "workspace_dir": workspace_dir,
                },
            )
            return record

    async def update_status(self, task_id: str, status: str) -> SessionRecord | None:
        """仅更新指定任务的状态；任务不存在时返回 None。

        ADR-055 P2-12：**单次加载**——旧实现先 ``_load().get()`` 再 ``_load()``
        取全表，两次之间文件损坏会把单条记录写进空表、覆盖全量记录。
        """
        async with self._lock:
            records = self._load()
            existing = records.get(task_id)
            if existing is None:
                return None
            record = SessionRecord.from_dict(existing)
            record.status = status
            record.updated_at = datetime.now(timezone.utc)
            records[task_id] = record.to_dict()
            self._save(records)
            self._logger.info(
                "session_status_updated",
                extra={"task_id": task_id, "status": status},
            )
            return record

    def _load(self, *, strict: bool = False) -> dict[str, dict[str, str]]:
        """读取登记簿文件。

        容错策略（ADR-055 P2-12）：文件不存在返回空表；读取失败、JSON 损坏
        或结构非字典时**备份损坏文件**（``<name>.corrupt-<ts>``）并记录 ERROR
        日志——磁盘上不可解析的内容无法恢复，备份只用于事后取证，随后按空表
        继续（保持既有「损坏则重建」的可用性语义）。

        真正要防的「静默覆盖全量」由**写路径**承担：``update_status`` 以
        ``strict=True`` 调用并在损坏时抛 ``SessionStoreError``——它只改一行
        状态却会重写整个文件，一旦读到空表就会把全量历史洗掉；而 ``upsert``
        是携带完整字段的显式登记（如「首次承接该任务」），重建是该场景的
        期望行为，保持非严格。
        """
        if not self._file_path.exists():
            return {}
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            self._handle_corrupt(f"读取失败：{e}", strict=strict)
            return {}
        if not isinstance(data, dict):
            self._handle_corrupt(f"结构非字典：{type(data).__name__}", strict=strict)
            return {}
        return data

    def _handle_corrupt(self, reason: str, *, strict: bool) -> None:
        """备份损坏的登记簿文件；strict 模式下抛出 SessionStoreError。"""
        backup_path = self._backup_corrupt_file()
        self._logger.error(
            "session_store_load_failed",
            extra={
                "file_path": str(self._file_path),
                "reason": reason,
                "backup_path": str(backup_path) if backup_path else None,
                "strict": strict,
            },
        )
        if strict:
            raise SessionStoreError("load", self._file_path, reason)

    def _backup_corrupt_file(self) -> Path | None:
        """把损坏文件改名为带时间戳的备份，返回备份路径（失败返回 None）。"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_path = self._file_path.with_name(f"{self._file_path.name}.corrupt-{timestamp}")
        try:
            os.replace(self._file_path, backup_path)
        except OSError as e:
            self._logger.error(
                "session_store_backup_failed",
                extra={"file_path": str(self._file_path), "error": str(e)},
            )
            return None
        return backup_path

    def _save(self, records: dict[str, dict[str, str]]) -> None:
        """原子写入：先写同目录临时文件，再 os.replace 覆盖目标文件。"""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._file_path.parent / _TMP_FILE_NAME
            tmp_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._file_path)
        except OSError as e:
            raise SessionStoreError("save", self._file_path, str(e)) from e


class TaskMappingStore:
    """任务派发映射的持久化登记簿（task_id → 项目键 + 显式工作目录）。

    背景（2026-09-23）：任务→(project_key, output_dir) 映射原先仅存内存，
    Agent 进程重启后丢失，产物按需拉取（hub.file_fetch）回退到默认空目录
    解析，恒报「文件不存在」而清单仍显示历史文件。本登记簿以 JSON 文件
    （如 ``data/task_mappings.json``）持久化映射，重启后 file_fetch 仍能
    以执行时相同参数反查工作目录。读写容错策略与 SessionStore 一致。
    """

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = asyncio.Lock()
        self._logger = logging.getLogger(__name__)

    async def get(self, task_id: str) -> tuple[str, str | None] | None:
        """查询任务派发映射；不存在返回 None。"""
        async with self._lock:
            data = self._load().get(task_id)
            if data is None:
                return None
            return data.get("project_key", task_id), data.get("output_dir")

    async def put(self, task_id: str, project_key: str, output_dir: str | None) -> None:
        """写入/覆盖任务派发映射。"""
        async with self._lock:
            records = self._load()
            records[task_id] = {"project_key": project_key, "output_dir": output_dir}
            self._save(records)

    def _load(self, *, strict: bool = False) -> dict[str, dict[str, str | None]]:
        """读取映射文件；容错策略与 SessionStore._load 一致（ADR-055 P2-12）。"""
        if not self._file_path.exists():
            return {}
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            return self._handle_corrupt(f"读取失败：{e}", strict=strict)
        if not isinstance(data, dict):
            return self._handle_corrupt(f"结构非字典：{type(data).__name__}", strict=strict)
        return data

    def _handle_corrupt(self, reason: str, *, strict: bool) -> dict:
        """备份损坏的映射文件；strict 模式下抛出 SessionStoreError。"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_path = self._file_path.with_name(f"{self._file_path.name}.corrupt-{timestamp}")
        try:
            os.replace(self._file_path, backup_path)
        except OSError as e:
            backup_path = None
            self._logger.error(
                "task_mapping_store_backup_failed",
                extra={"file_path": str(self._file_path), "error": str(e)},
            )
        self._logger.error(
            "task_mapping_store_load_failed",
            extra={
                "file_path": str(self._file_path),
                "reason": reason,
                "backup_path": str(backup_path) if backup_path else None,
                "strict": strict,
            },
        )
        if strict:
            raise SessionStoreError("load", self._file_path, reason)
        return {}

    def _save(self, records: dict[str, dict[str, str | None]]) -> None:
        """原子写入（同 SessionStore._save）。"""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._file_path.parent / ".task_mappings.json.tmp"
            tmp_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._file_path)
        except OSError as e:
            raise SessionStoreError("save", self._file_path, str(e)) from e
