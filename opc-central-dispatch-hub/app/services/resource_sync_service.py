"""Agent 资源同步服务（ADR-022）。

负责云端资源仓库（manifest.json + 资源文件）与本地缓存
``data/resources/`` 之间的检查（check）与应用（update）：

- 检查：仅比对云端与本地 manifest 的差异，不下载资源
- 应用：按资源条目下载 → sha256/size 校验 → 原子落盘
- 导入：zip 资源包（云端仓库同构）或节点整体包 tar.gz 离线兜底，校验后直接应用

状态持久化在缓存目录 ``sync_state.json``；进程内 asyncio.Lock
防止检查/更新并发执行造成半更新。
"""

import asyncio
import hashlib
import io
import json
import logging
import re
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 节点整体包文件名规范（build-code-agent.sh 产物）：opc-agent-<type>-<版本>-<平台>.tar.gz
_TARBALL_NAME_PATTERN = re.compile(
    r"^opc-agent-(?P<type>[a-z]+)-(?P<version>v?[0-9][\w.]*)-(?P<platform>[a-z0-9-]+)\.tar\.gz$"
)

# 资源同步状态（见 Spec §4 降级链路）
STATUS_DISABLED = "disabled"
STATUS_UP_TO_DATE = "up-to-date"
STATUS_UPDATE_AVAILABLE = "update-available"
STATUS_OFFLINE = "offline"
STATUS_FAILED = "failed"
STATUS_SYNCING = "syncing"
STATUS_IMPORTED = "imported"

MANIFEST_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 600
MAX_IMPORT_ZIP_BYTES = 500 * 1024 * 1024
HUB_API_TIMEOUT_SECONDS = 10


def parse_resource_key(key: str) -> tuple[str, str] | None:
    """解析资源 key 的 (kind, agent_type)（ADR-025 key v2 规范）。

    - ``agent-{type}-{platform}`` → ("package", type)
    - ``install-script-{type}`` → ("script", type)
    - 无法识别 → None（归入「未分组」展示）
    """
    if key.startswith("install-script-"):
        type_ = key[len("install-script-"):]
        return ("script", type_) if type_ else None
    if key.startswith("agent-"):
        rest = key[len("agent-"):]
        # 剩余部分为 {type}-{platform}，type 为第一段（platform 可能含连字符）
        type_ = rest.split("-", 1)[0]
        return ("package", type_) if type_ else None
    return None


class ManifestError(Exception):
    """云端 manifest 缺失字段或结构非法。"""


class ShaMismatchError(Exception):
    """资源文件 sha256 校验失败。"""


@dataclass
class SyncResult:
    """检查/应用/导入的结果。"""

    status: str
    message: str | None = None
    updated_keys: list[str] = field(default_factory=list)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_of(data: bytes) -> str:
    """计算字节流的 SHA-256 十六进制值。"""
    return hashlib.sha256(data).hexdigest()


class ResourceSyncService:
    """Agent 资源同步服务（进程内单例，见 api/deps.py）。"""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.tmp_dir = self.cache_dir / ".tmp"
        self.manifest_path = self.cache_dir / "manifest.json"
        self.state_path = self.cache_dir / "sync_state.json"
        self._lock = asyncio.Lock()

    # ---------------- 本地清单与状态 ----------------

    def local_manifest(self) -> dict | None:
        """读取本地缓存 manifest；缺失或损坏返回 None。"""
        if not self.manifest_path.is_file():
            return None
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning(
                "resource_local_manifest_corrupted", extra={"path": str(self.manifest_path)}
            )
            return None

    def local_revision(self) -> str | None:
        manifest = self.local_manifest()
        return manifest.get("revision") if manifest else None

    def _save_local_manifest(self, manifest: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _merge_local_manifest(self, revision: str | None, new_items: list[dict]) -> None:
        """合并写入本地清单：覆盖/追加给定条目，保留未涉及资源。

        部分更新（keys 指定子集）或增量 zip 导入时，未更新资源的条目
        必须保留，否则状态视图会将其误判为「未落盘」。
        """
        merged = self._local_by_key(self.local_manifest())
        for item in new_items:
            merged[item["key"]] = item
        resources = [merged[key] for key in sorted(merged)]
        self._save_local_manifest({"revision": revision, "resources": resources})

    def _read_state(self) -> dict:
        if not self.state_path.is_file():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _update_state(self, **fields) -> None:
        state = self._read_state()
        state.update(fields)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------------- 云端 manifest ----------------

    @staticmethod
    def _repo_base(manifest_url: str) -> str:
        """manifest_url 规整为仓库根（去掉末尾 manifest.json 与多余斜杠）。"""
        base = manifest_url.strip()
        if base.endswith("manifest.json"):
            base = base[: -len("manifest.json")]
        return base if base.endswith("/") else f"{base}/"

    async def _fetch_cloud_manifest(self, manifest_url: str) -> dict:
        """下载并校验云端 manifest 结构。"""
        url = f"{self._repo_base(manifest_url)}manifest.json"
        async with httpx.AsyncClient(timeout=MANIFEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            manifest = resp.json()
        if not isinstance(manifest.get("revision"), str) or not isinstance(
            manifest.get("resources"), list
        ):
            raise ManifestError("manifest 缺少 revision 或 resources 字段")
        for item in manifest["resources"]:
            if not all(k in item for k in ("key", "path", "sha256", "size")):
                raise ManifestError(f"资源条目字段不完整: {item}")
            path_value = str(item["path"])
            if path_value.startswith(("/", "\\")) or ".." in path_value.split("/"):
                raise ManifestError(f"资源 path 非法: {path_value}")
        return manifest

    @staticmethod
    def _cloud_by_key(manifest: dict) -> dict[str, dict]:
        return {item["key"]: item for item in manifest.get("resources", [])}

    @staticmethod
    def _local_by_key(manifest: dict | None) -> dict[str, dict]:
        if not manifest:
            return {}
        return {item["key"]: item for item in manifest.get("resources", [])}

    def _diff(self, cloud_manifest: dict) -> list[dict]:
        """计算云端与本地差异（本地缺失或 sha256 不同视为过时）。"""
        local = self._local_by_key(self.local_manifest())
        cloud = self._cloud_by_key(cloud_manifest)
        result = []
        for key, item in cloud.items():
            local_item = local.get(key)
            outdated = local_item is None or local_item.get("sha256") != item.get("sha256")
            result.append(
                {
                    "key": key,
                    "cloud_path": item.get("path"),
                    "cloud_sha256": item.get("sha256"),
                    "cloud_version": item.get("version"),
                    "size": item.get("size"),
                    "local_version": (local_item or {}).get("version"),
                    "local_sha256": (local_item or {}).get("sha256"),
                    "present": local_item is not None and (self.cache_dir / item["path"]).is_file(),
                    "outdated": outdated,
                }
            )
        return result

    # ---------------- 仓库地址云端下发（ADR-025） ----------------

    async def resolve_manifest_url(self) -> str | None:
        """从服务端配置接口获取最新资源仓库地址；失败回退 state 缓存。

        服务端地址与 Token 来自配置文件（AGENT_RESOURCE_HUB_CONFIG_URL /
        AGENT_RESOURCE_HUB_TOKEN，发布前手动指定，用户零配置）：
        - 未配置服务端地址 → 返回 None（调用方回退本地配置/缓存）
        - 拉取成功 → 缓存到 state 并返回新地址
        - 拉取失败（网络/认证）→ 返回 state 缓存的最近地址（可为 None）
        """
        cfg = get_settings()
        hub_config_url = (cfg.agent_resource_hub_config_url or "").strip()
        if not hub_config_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=HUB_API_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    hub_config_url,
                    headers={"X-Hub-Token": cfg.agent_resource_hub_token or ""},
                )
                resp.raise_for_status()
                payload = resp.json()
                url = (payload.get("data") or {}).get("manifestUrl")
            if url:
                self._update_state(manifest_url=url)
                logger.info(
                    "resource_manifest_url_resolved_from_hub",
                    extra={"event": "resource_manifest_url_resolved_from_hub"},
                )
                return url
            logger.warning("hub_api_returned_empty_manifest_url")
        except Exception as exc:
            logger.warning(
                "hub_api_fetch_failed_fallback_cache",
                extra={"event": "hub_api_fetch_failed_fallback_cache", "error": str(exc)},
            )
        return self._read_state().get("manifest_url")

    # ---------------- 状态组装（供 API 展示） ----------------

    def get_status_view(self) -> dict:
        """组装当前状态视图：状态、revision、资源对比列表与按类型分组（ADR-025）。

        key 集合以云端快照为准（云端已下架的条目不再展示，避免历史遗留）；
        仅当从未检查过云端（快照为空，如纯 zip 导入场景）时退回本地清单。
        """
        state = self._read_state()
        local = self._local_by_key(self.local_manifest())
        cloud_snapshot = state.get("cloud_snapshot") or {}
        cloud = self._cloud_by_key(cloud_snapshot)
        keys = sorted(cloud if cloud else local)
        resources = []
        groups: dict[str, list[dict]] = {}
        for key in keys:
            local_item = local.get(key) or {}
            cloud_item = cloud.get(key) or {}
            cloud_sha = cloud_item.get("sha256")
            local_sha = local_item.get("sha256")
            item = {
                "key": key,
                "cloud_path": cloud_item.get("path"),
                "cloud_sha256": cloud_sha,
                "cloud_version": cloud_item.get("version"),
                "size": cloud_item.get("size") or local_item.get("size"),
                "local_version": local_item.get("version"),
                "local_sha256": local_sha,
                "present": bool(local_sha)
                and (self.cache_dir / str(local_item.get("path", ""))).is_file(),
                "outdated": bool(cloud_sha and cloud_sha != local_sha),
            }
            resources.append(item)
            parsed = parse_resource_key(key)
            group = parsed[1] if parsed else "_ungrouped"
            groups.setdefault(group, []).append(item)
        agent_types = [
            {
                "type": type_,
                "has_update": any(item["outdated"] for item in items),
                "resources": [item["key"] for item in items],
            }
            for type_, items in sorted(groups.items())
        ]
        return {
            "status": state.get("status", STATUS_DISABLED)
            if state.get("status") or self.manifest_path.is_file()
            else STATUS_DISABLED,
            "manifest_url": state.get("manifest_url"),
            "local_revision": self.local_revision(),
            "cloud_revision": state.get("cloud_revision"),
            "last_checked_at": state.get("last_checked_at"),
            "last_synced_at": state.get("last_synced_at"),
            "resources": resources,
            "agent_types": agent_types,
        }

    def keys_for_type(self, agent_type: str) -> list[str]:
        """返回属于指定 agent 类型的全部资源 key（按云端快照/本地清单）。"""
        state = self._read_state()
        cloud_snapshot = state.get("cloud_snapshot") or {}
        keys = set(self._cloud_by_key(cloud_snapshot)) or set(
            self._local_by_key(self.local_manifest())
        )
        matched = []
        for key in sorted(keys):
            parsed = parse_resource_key(key)
            if parsed and parsed[1] == agent_type:
                matched.append(key)
        return matched

    # ---------------- 检查（不下载资源） ----------------

    async def check(self, manifest_url: str | None) -> SyncResult:
        """检查云端是否有更新；只比对，不下载资源。"""
        if not manifest_url:
            self._update_state(status=STATUS_DISABLED)
            return SyncResult(status=STATUS_DISABLED, message="未配置云端仓库 URL")

        try:
            cloud = await self._fetch_cloud_manifest(manifest_url)
        except (httpx.HTTPError, ManifestError, json.JSONDecodeError) as exc:
            self._update_state(
                status=STATUS_OFFLINE,
                last_checked_at=_utc_now_iso(),
                manifest_url=manifest_url,
            )
            logger.warning("resource_check_offline", extra={"error": str(exc)}, exc_info=True)
            return SyncResult(status=STATUS_OFFLINE, message=f"云端仓库不可达：{exc}")

        diff = self._diff(cloud)
        outdated = [item for item in diff if item["outdated"]]
        status = STATUS_UPDATE_AVAILABLE if outdated else STATUS_UP_TO_DATE
        self._update_state(
            status=status,
            cloud_revision=cloud.get("revision"),
            last_checked_at=_utc_now_iso(),
            cloud_snapshot=cloud,
            manifest_url=manifest_url,
        )
        logger.info(
            "resource_check_completed",
            extra={"status": status, "outdated": len(outdated)},
        )
        message = f"发现 {len(outdated)} 项资源更新" if outdated else "资源已是最新"
        return SyncResult(status=status, message=message)

    # ---------------- 应用（下载选定资源） ----------------

    async def apply(self, manifest_url: str | None, keys: list[str] | None = None) -> SyncResult:
        """应用更新：下载选定资源，校验后原子落盘。"""
        if self._lock.locked():
            return SyncResult(status=STATUS_SYNCING, message="已有同步任务进行中")
        async with self._lock:
            return await self._apply_locked(manifest_url, keys)

    async def _apply_locked(self, manifest_url: str | None, keys: list[str] | None) -> SyncResult:
        if not manifest_url:
            return SyncResult(status=STATUS_DISABLED, message="未配置云端仓库 URL")
        try:
            cloud_manifest = await self._fetch_cloud_manifest(manifest_url)
        except (httpx.HTTPError, ManifestError, json.JSONDecodeError) as exc:
            self._update_state(status=STATUS_OFFLINE, last_checked_at=_utc_now_iso())
            return SyncResult(status=STATUS_OFFLINE, message=f"云端仓库不可达：{exc}")

        cloud = self._cloud_by_key(cloud_manifest)
        if keys:
            cloud = {k: v for k, v in cloud.items() if k in keys}
            unknown = set(keys) - set(cloud)
            if unknown:
                return SyncResult(
                    status=STATUS_FAILED,
                    message=f"未知资源 key: {sorted(unknown)}",
                )

        # 幂等跳过：本地 sha 与云端一致且文件在盘的条目无需重下载
        # （类型更新会指定该类型全部条目，其中未过时的条目应跳过）
        local = self._local_by_key(self.local_manifest())
        for k in list(cloud.keys()):
            item = cloud[k]
            local_item = local.get(k)
            if (
                local_item
                and local_item.get("sha256") == item.get("sha256")
                and (self.cache_dir / str(item["path"])).is_file()
            ):
                del cloud[k]
        if not cloud:
            self._update_state(
                status=STATUS_UP_TO_DATE,
                last_checked_at=_utc_now_iso(),
            )
            return SyncResult(status=STATUS_UP_TO_DATE, message="全部资源已是最新")

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[tuple[dict, Path]] = []
        try:
            for item in cloud.values():
                dest_tmp = self.tmp_dir / item["path"]
                dest_tmp.parent.mkdir(parents=True, exist_ok=True)
                url = f"{self._repo_base(manifest_url)}{item['path']}"
                await self._download_and_verify(url, dest_tmp, item["sha256"], int(item["size"]))
                downloaded.append((item, dest_tmp))
        except (httpx.HTTPError, ShaMismatchError, OSError) as exc:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
            self._update_state(status=STATUS_FAILED, last_checked_at=_utc_now_iso())
            logger.error("resource_apply_failed", extra={"error": str(exc)})
            return SyncResult(status=STATUS_FAILED, message=f"更新失败：{exc}")

        # 全部校验通过，原子替换；本地 revision 记录为云端 revision
        updated_keys = []
        for item, tmp in downloaded:
            dest = self.cache_dir / item["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp.replace(dest)
            updated_keys.append(item["key"])
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

        # 全量更新（未指定 keys）时本地清单整体镜像云端清单——云端已下架的
        # 条目同步从本地清单移除，避免历史遗留；部分更新走合并保留未涉及条目
        if keys is None:
            self._save_local_manifest(cloud_manifest)
        else:
            self._merge_local_manifest(cloud_manifest.get("revision"), list(cloud.values()))
        self._update_state(
            status=STATUS_UP_TO_DATE,
            last_synced_at=_utc_now_iso(),
            last_checked_at=_utc_now_iso(),
        )
        logger.info("resource_apply_completed", extra={"updated_keys": updated_keys})
        return SyncResult(status=STATUS_UP_TO_DATE, updated_keys=updated_keys)

    async def _download_and_verify(
        self, url: str, dest: Path, expected_sha: str, expected_size: int
    ) -> None:
        """下载文件到 dest 并做 size/sha256 校验，失败抛异常并清理半成品。"""
        async with (
            httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client,
            client.stream("GET", url) as resp,
        ):
                resp.raise_for_status()
                digest = hashlib.sha256()
                size = 0
                with dest.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(1024 * 256):
                        fh.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
        if size != expected_size:
            dest.unlink(missing_ok=True)
            raise ShaMismatchError(f"{url} 大小不符（期望 {expected_size}，实际 {size}）")
        if digest.hexdigest() != expected_sha.lower():
            dest.unlink(missing_ok=True)
            raise ShaMismatchError(f"{url} sha256 校验失败")

    # ---------------- zip 导入（离线兜底） ----------------

    async def import_zip(self, data: bytes) -> SyncResult:
        """导入 zip 资源包：校验后原子应用（不经检查阶段）。"""
        if len(data) > MAX_IMPORT_ZIP_BYTES:
            return SyncResult(status=STATUS_FAILED, message="zip 超过大小限制（500MB）")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            return SyncResult(status=STATUS_FAILED, message=f"非法 zip 文件：{exc}")

        if "manifest.json" not in archive.namelist():
            return SyncResult(status=STATUS_FAILED, message="zip 根目录缺少 manifest.json")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return SyncResult(status=STATUS_FAILED, message=f"manifest.json 非法：{exc}")

        resources = manifest.get("resources", [])
        extracted: list[tuple[dict, bytes]] = []
        for item in resources:
            path_value = str(item.get("path", ""))
            if path_value not in archive.namelist():
                return SyncResult(status=STATUS_FAILED, message=f"zip 缺少资源文件: {path_value}")
            content = archive.read(path_value)
            if sha256_of(content) != str(item.get("sha256", "")).lower():
                return SyncResult(
                    status=STATUS_FAILED, message=f"资源 sha256 校验失败: {path_value}"
                )
            extracted.append((item, content))

        # 校验全部通过，原子落盘
        for item, content in extracted:
            dest = self.cache_dir / item["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        self._merge_local_manifest(
            manifest.get("revision"), [item for item, _ in extracted]
        )
        self._update_state(
            status=STATUS_IMPORTED,
            last_synced_at=_utc_now_iso(),
            last_checked_at=_utc_now_iso(),
        )
        logger.info(
            "resource_import_completed",
            extra={"keys": [item["key"] for item, _ in extracted]},
        )
        return SyncResult(
            status=STATUS_IMPORTED, updated_keys=[item["key"] for item, _ in extracted]
        )

    # ---------------- 节点整体包 tar.gz 导入（离线兜底） ----------------

    async def import_tarball(self, data: bytes, filename: str) -> SyncResult:
        """导入节点整体包 tar.gz（构建脚本产物，离线兜底）。

        从文件名解析资源 key（``agent-<type>-<platform>``）与路径，从包内
        ``build-info.json`` 读取版本号（缺失时回退文件名版本）；计算 sha256
        后生成 manifest 条目，与云端仓库同构地并入本地清单。

        Args:
            data: tar.gz 完整字节流。
            filename: 上传文件名（如 ``opc-agent-code-0.6.0-linux-x64.tar.gz``）。
        """
        if len(data) > MAX_IMPORT_ZIP_BYTES:
            return SyncResult(status=STATUS_FAILED, message="整体包超过大小限制（500MB）")
        name = (filename or "").strip().lower()
        matched = _TARBALL_NAME_PATTERN.match(name)
        if matched is None:
            return SyncResult(
                status=STATUS_FAILED,
                message=(
                    "无法从文件名识别资源类型（期望 opc-agent-<type>-<版本>-<平台>.tar.gz，"
                    f"实际: {filename or '（空）'}）"
                ),
            )
        agent_type = matched.group("type")
        name_version = matched.group("version")
        platform = matched.group("platform")
        key = f"agent-{agent_type}-{platform}"

        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                # build-info.json 位于组包根（tar 内一层目录之下），按文件名定位
                info_member = next(
                    (
                        member
                        for member in archive.getmembers()
                        if member.isfile() and member.name.split("/")[-1] == "build-info.json"
                    ),
                    None,
                )
                version = name_version
                if info_member is not None:
                    try:
                        build_info = json.loads(
                            archive.extractfile(info_member).read().decode("utf-8")
                        )
                        version = str(build_info.get("artifact_version") or name_version)
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        logger.warning("resource_import_build_info_read_failed", exc_info=True)
        except tarfile.TarError as exc:
            return SyncResult(status=STATUS_FAILED, message=f"非法 tar.gz 文件：{exc}")

        version = version.lstrip("v")
        path = f"agent-{agent_type}/{version}/{filename}"
        item = {
            "key": key,
            "path": path,
            "sha256": sha256_of(data),
            "size": len(data),
            "version": version,
        }

        # 幂等：同 key 同 sha256 且文件在盘 → 已是最新
        local_item = self._local_by_key(self.local_manifest()).get(key)
        if (
            local_item
            and local_item.get("sha256") == item["sha256"]
            and (self.cache_dir / path).is_file()
        ):
            return SyncResult(status=STATUS_UP_TO_DATE, message="该整体包已是最新，无需导入")

        dest = self.cache_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self._merge_local_manifest(f"local-import-{_utc_now_iso()}", [item])
        self._update_state(
            status=STATUS_IMPORTED,
            last_synced_at=_utc_now_iso(),
            last_checked_at=_utc_now_iso(),
        )
        logger.info(
            "resource_tarball_import_completed",
            extra={"key": key, "version": version, "size": len(data)},
        )
        return SyncResult(status=STATUS_IMPORTED, updated_keys=[key])
