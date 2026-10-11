"""任务产物缓存与拉取编排服务（ADR-028）。

职责：
- 接收并存储 Agent 上报的文件清单（``agent.file_tree`` → manifest.json）；
- 按需向 Agent 下发 ``hub.file_fetch``（单文件 / 整目录 zip）并等待分块回传
  ``agent.file_chunk``（超时/错误经 ``agent.file_error`` 取消）；
- 将回传内容落盘至 ``{artifact_cache_dir}/{task_id}/``，供预览与打包下载。

路径安全：Agent 上报与请求的路径均为工作目录内相对路径，落盘前统一清洗
（拒绝 ``..`` 片段与绝对路径），保证缓存目录不被越界写入。
"""

import asyncio
import base64
import json
import logging
import time
import uuid
import zipfile
from pathlib import Path

from app.core.config import get_settings
from app.schemas.ws_messages import HUB_FILE_FETCH
from app.services.connection_manager import connection_manager

logger = logging.getLogger(__name__)

#: 单次文件拉取等待分块回传的超时（秒）
FETCH_TIMEOUT_SECONDS = 30
#: 单文件大小上限（字节）：超过则拒绝预览请求
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
#: 打包产物大小上限（字节）
MAX_ZIP_SIZE_BYTES = 500 * 1024 * 1024
#: 分块 base64 数据大小（Agent 侧按此切片）
CHUNK_SIZE_BYTES = 512 * 1024
#: 增量同步单轮最多拉取的文件数（ADR-059 D4）
MAX_INCREMENTAL_FILES_PER_CYCLE = 20
#: 增量同步单文件大小上限（字节）：更大的文件留待完成时全量 zip 兜底
MAX_INCREMENTAL_FILE_BYTES = 2 * 1024 * 1024


def _sanitize_relative_path(path: str) -> str | None:
    """清洗工作目录内相对路径，非法（越界/绝对路径）时返回 None。"""
    normalized = path.strip().lstrip("/")
    if not normalized or "\\" in normalized:
        return None
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


class ArtifactService:
    """任务产物清单存储、按需拉取编排与本地落盘。"""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}
        self._buffers: dict[str, dict[int, bytes]] = {}
        self._buffer_totals: dict[str, int] = {}

    # ---------- 目录与清单 ----------

    def _task_dir(self, task_id: str) -> Path:
        return Path(get_settings().artifact_cache_dir) / task_id

    def _manifest_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / ".manifest.json"

    def save_file_tree(self, task_id: str, files: list[dict], truncated: bool = False) -> None:
        """保存 Agent 上报的文件清单快照。

        Args:
            task_id: 任务 ID。
            files: 清单项列表，每项含 path/size/mtime。
            truncated: 清单是否因超上限被截断。
        """
        task_dir = self._task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "updated_at": time.time(),
            "truncated": truncated,
            "files": files,
        }
        manifest = self._manifest_path(task_id)
        tmp = manifest.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(manifest)
        logger.info(
            "artifact_file_tree_saved",
            extra={
                "event": "artifact_file_tree_saved",
                "task_id": task_id,
                "file_count": len(files),
                "truncated": truncated,
            },
        )

    def get_tree(self, task_id: str) -> dict | None:
        """读取缓存的文件清单；无缓存时返回 None。"""
        manifest = self._manifest_path(task_id)
        if not manifest.exists():
            return None
        try:
            return json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("artifact_manifest_read_failed", extra={"task_id": task_id})
            return None

    # ---------- 增量同步（ADR-059 D4） ----------

    def _synced_state_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / ".synced.json"

    def get_synced_state(self, task_id: str) -> dict[str, dict]:
        """读取「已同步文件指纹」状态（path -> {size, mtime}）。"""
        path = self._synced_state_path(task_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning(
                "artifact_synced_state_read_failed",
                extra={"event": "artifact_synced_state_read_failed", "task_id": task_id},
            )
            return {}
        files = data.get("files") if isinstance(data, dict) else None
        return files if isinstance(files, dict) else {}

    def _save_synced_state(self, task_id: str, state: dict[str, dict]) -> None:
        path = self._synced_state_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"task_id": task_id, "updated_at": time.time(), "files": state}),
            encoding="utf-8",
        )
        tmp.replace(path)

    def select_changed_files(
        self,
        task_id: str,
        files: list[dict],
        *,
        limit: int = MAX_INCREMENTAL_FILES_PER_CYCLE,
    ) -> list[str]:
        """从最新文件清单中挑出**尚未同步或已变更**的相对路径（ADR-059 D4）。

        变更判定：本地缓存缺失，或 ``size``/``mtime`` 与已同步指纹不同。
        zip/二进制等大文件按 ``MAX_INCREMENTAL_FILE_BYTES`` 跳过（避免拖垮
        单轮同步；完成时仍走全量 zip 兜底）。

        Args:
            task_id: 任务 ID。
            files: Agent 上报的清单项（每项含 path/size/mtime）。
            limit: 单轮最多拉取的文件数（防止一次性打爆节点）。

        Returns:
            待拉取路径列表（按清单顺序，最多 ``limit`` 个）。
        """
        state = self.get_synced_state(task_id)
        task_dir = self._task_dir(task_id)
        changed: list[str] = []
        for item in files:
            if len(changed) >= limit:
                break
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "")
            relative = _sanitize_relative_path(raw_path)
            if not relative:
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                continue
            if size > MAX_INCREMENTAL_FILE_BYTES:
                continue
            fingerprint = {"size": size, "mtime": item.get("mtime")}
            if state.get(relative) == fingerprint and (task_dir / relative).is_file():
                continue
            changed.append(relative)
        return changed

    async def sync_incremental(
        self,
        task_id: str,
        node_id: str | None,
        files: list[dict],
    ) -> int:
        """对最新文件清单做一轮增量同步（ADR-059 D4，执行中周期调用）。

        单文件失败只记 WARNING 并跳过（下轮重试），不阻断任务；
        节点离线时整体失败同样只告警——已同步部分即改派现场。

        Args:
            task_id: 任务 ID。
            node_id: 执行节点 ID。
            files: 最新文件清单。

        Returns:
            本轮成功同步的文件数。
        """
        if not node_id:
            return 0
        targets = self.select_changed_files(task_id, files)
        if not targets:
            return 0
        state = self.get_synced_state(task_id)
        by_path = {str(item.get("path")): item for item in files if isinstance(item, dict)}
        synced = 0
        for relative in targets:
            try:
                await self.fetch_file(task_id, node_id, relative)
            except Exception as exc:  # noqa: BLE001 — 单文件失败不阻断
                logger.warning(
                    "artifact_incremental_file_failed",
                    extra={
                        "event": "artifact_incremental_file_failed",
                        "task_id": task_id,
                        "path": relative,
                        "reason": str(exc),
                    },
                )
                continue
            item = by_path.get(relative) or {}
            state[relative] = {
                "size": int(item.get("size") or 0),
                "mtime": item.get("mtime"),
            }
            synced += 1
        if synced:
            self._save_synced_state(task_id, state)
        logger.info(
            "artifact_incremental_sync_completed",
            extra={
                "event": "artifact_incremental_sync_completed",
                "task_id": task_id,
                "candidates": len(targets),
                "synced": synced,
            },
        )
        return synced

    # ---------- 按需拉取 ----------

    async def fetch_file(self, task_id: str, node_id: str | None, path: str) -> Path:
        """拉取单文件内容并落盘缓存，返回本地缓存路径。

        Args:
            task_id: 任务 ID。
            node_id: 执行该任务的节点 ID；离线时抛错。
            path: 工作目录内相对路径。

        Raises:
            ValueError: 路径非法。
            RuntimeError: 节点未分配 / 离线 / 拉取超时 / Agent 报错。
        """
        relative = _sanitize_relative_path(path)
        if not relative:
            raise ValueError("非法的文件路径")
        payload = await self._request(task_id, node_id, mode="file", path=relative)
        return self.store_payload(task_id, mode="file", path=relative, payload=payload)

    async def fetch_zip(self, task_id: str, node_id: str | None) -> Path:
        """请求 Agent 打包整个工作目录，返回本地 zip 路径。"""
        payload = await self._request(task_id, node_id, mode="zip", path=None)
        return self.store_payload(task_id, mode="zip", path=None, payload=payload)

    async def _request(
        self, task_id: str, node_id: str | None, mode: str, path: str | None
    ) -> bytes:
        """下发 hub.file_fetch 并等待分块组装完成。

        Returns:
            组装完成的完整字节数据。

        Raises:
            RuntimeError: 节点缺失/离线、超时、Agent 侧错误。
        """
        if not node_id:
            raise RuntimeError("任务尚未分配执行节点")
        request_id = f"req_{uuid.uuid4().hex}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            sent = await connection_manager.send_to_node(
                node_id,
                {
                    "event": HUB_FILE_FETCH,
                    "payload": {
                        "request_id": request_id,
                        "task_id": task_id,
                        "mode": mode,
                        "path": path,
                    },
                    "timestamp": None,
                    "request_id": None,
                },
            )
            if not sent:
                raise RuntimeError("Agent 节点离线，无法拉取产物")
            return await asyncio.wait_for(future, timeout=FETCH_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise RuntimeError("拉取产物超时，请稍后重试") from exc
        finally:
            self._pending.pop(request_id, None)
            self._buffers.pop(request_id, None)
            self._buffer_totals.pop(request_id, None)

    # ---------- 分块回传处理（由 task_event_handler 调用） ----------

    def handle_file_chunk(
        self,
        request_id: str,
        seq: int,
        total: int,
        data_b64: str,
        done: bool,
    ) -> None:
        """接收 agent.file_chunk 分块，全部到齐后完成 pending Future。

        Args:
            request_id: 拉取请求 ID。
            seq: 分块序号（从 0 开始）。
            total: 分块总数。
            data_b64: base64 编码的数据分片。
            done: 是否为最后一个分块。
        """
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        try:
            chunk = base64.b64decode(data_b64)
        except (ValueError, TypeError):
            future.set_exception(RuntimeError("分块数据解码失败"))
            return
        self._buffer_totals.setdefault(request_id, total)
        self._buffers.setdefault(request_id, {})[seq] = chunk
        if not done:
            return
        buffer = self._buffers.get(request_id, {})
        expected = self._buffer_totals.get(request_id, total)
        if len(buffer) != expected:
            future.set_exception(RuntimeError("分块数量不完整"))
            return
        payload = b"".join(buffer[index] for index in range(expected))
        future.set_result(payload)

    def handle_file_error(self, request_id: str, reason: str) -> None:
        """接收 agent.file_error，取消对应 pending Future。"""
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_exception(RuntimeError(f"Agent 拉取失败: {reason}"))

    # ---------- 落盘（task_event_handler 调用） ----------

    def store_payload(self, task_id: str, mode: str, path: str | None, payload: bytes) -> Path:
        """将拉取完成的数据落盘；zip 落为 export.zip，file 落为工作目录相对路径。

        Args:
            task_id: 任务 ID。
            mode: file | zip。
            path: file 模式的工作目录相对路径。
            payload: 完整字节数据。

        Raises:
            ValueError: 路径非法或超出大小上限。
        """
        task_dir = self._task_dir(task_id)
        if mode == "zip":
            if len(payload) > MAX_ZIP_SIZE_BYTES:
                raise ValueError("打包产物超出大小上限（500MB）")
            target = task_dir / "export.zip"
        else:
            relative = _sanitize_relative_path(path or "")
            if not relative:
                raise ValueError("非法的文件路径")
            if len(payload) > MAX_FILE_SIZE_BYTES:
                raise ValueError("文件超出预览大小上限（10MB）")
            target = task_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        logger.info(
            "artifact_payload_stored",
            extra={
                "event": "artifact_payload_stored",
                "task_id": task_id,
                "mode": mode,
                "path": path,
                "size": len(payload),
            },
        )
        return target

    def get_cached_file_path(self, task_id: str, path: str) -> Path | None:
        """读取缓存文件路径；不存在或路径非法时返回 None。"""
        relative = _sanitize_relative_path(path)
        if not relative:
            return None
        candidate = (self._task_dir(task_id) / relative).resolve()
        if (
            not candidate.is_relative_to(self._task_dir(task_id).resolve())
            or not candidate.is_file()
        ):
            return None
        return candidate

    def get_cached_zip_path(self, task_id: str) -> Path | None:
        """读取缓存的打包 zip 路径；不存在时返回 None。"""
        candidate = self._task_dir(task_id) / "export.zip"
        return candidate if candidate.is_file() else None

    def list_cached_files(self, task_id: str) -> list[dict]:
        """产物文件清单：manifest 优先，缺失时目录扫描兜底（ADR-068）。

        目录扫描排除缓存元数据（``export.zip``/``.manifest.json``/
        ``.synced.json``/``*.tmp``），按路径排序返回 ``[{path, size}]``。
        """
        tree = self.get_tree(task_id)
        if tree is not None:
            return [item for item in tree.get("files") or [] if isinstance(item, dict)]
        task_dir = self._task_dir(task_id)
        if not task_dir.is_dir():
            return []
        items: list[dict] = []
        for path in sorted(task_dir.rglob("*")):
            if (
                not path.is_file()
                or path.name in self._ZIP_EXCLUDED_NAMES
                or path.name.endswith(".tmp")
            ):
                continue
            items.append(
                {
                    "path": path.relative_to(task_dir).as_posix(),
                    "size": path.stat().st_size,
                }
            )
        return items

    # ---------- 本地打包兜底（ADR-068） ----------

    #: 打包兜底时排除的缓存元数据文件
    _ZIP_EXCLUDED_NAMES = {"export.zip", ".manifest.json", ".synced.json"}

    def build_zip_from_cache(self, task_id: str) -> Path:
        """用本地已同步的文件树散件打包 export.zip（兜底，ADR-068）。

        仅当 export.zip 不存在时生成（节点全量同步的包优先，不覆盖）；
        本地散件来自增量同步（ADR-059 D4），有单文件 2MB 上限——打包结果
        可能缺大文件，调用方必须向 AI 显式转达不完整性警告。

        Raises:
            RuntimeError: 本地无任何可打包的缓存文件（增量同步从未成功）。
        """
        existing = self.get_cached_zip_path(task_id)
        if existing is not None:
            return existing
        task_dir = self._task_dir(task_id)
        files = [
            path
            for path in task_dir.rglob("*")
            if path.is_file()
            and path.name not in self._ZIP_EXCLUDED_NAMES
            and not path.name.endswith(".tmp")
        ]
        if not files:
            raise RuntimeError("本地缓存无可打包文件（增量同步从未成功，需节点在线重新同步）")
        target = task_dir / "export.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path in files:
                archive.write(file_path, file_path.relative_to(task_dir).as_posix())
        logger.info(
            "artifact_zip_built_from_cache",
            extra={
                "event": "artifact_zip_built_from_cache",
                "task_id": task_id,
                "file_count": len(files),
                "size": target.stat().st_size,
            },
        )
        return target


artifact_service = ArtifactService()
