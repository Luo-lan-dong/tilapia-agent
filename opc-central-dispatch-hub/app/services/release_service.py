"""产品版本发布服务（ADR-026）：更新日志云端拉取与中枢检查更新。

数据源为云端资源仓库管理后台 `hub_releases` 表，经 `/portal/open/hub/releases`
（X-Hub-Token 认证）下发；服务端地址与 Token 复用 ADR-025 配置文件项
（AGENT_RESOURCE_HUB_CONFIG_URL / AGENT_RESOURCE_HUB_TOKEN，用户零配置）。

中枢本地不维护历史更新日志（ADR-018 修订）：`system_versions` 仅保留一条
当前运行版本记录，历史日志完全以云端为准。
"""

import logging
from dataclasses import dataclass, field

import httpx
from packaging.version import InvalidVersion, Version

from app.core.config import get_settings

logger = logging.getLogger(__name__)

HUB_API_TIMEOUT_SECONDS = 10

# 产品线展示名（未知 product 原样展示）
PRODUCT_LABELS: dict[str, str] = {
    "hub": "中枢",
    "agent-code": "Code Agent",
}

# 检查更新状态
UPDATE_STATUS_AVAILABLE = "update-available"
UPDATE_STATUS_UP_TO_DATE = "up-to-date"
UPDATE_STATUS_FAILED = "failed"


@dataclass
class ReleaseItem:
    """单条版本发布记录。"""

    product: str
    version: str
    title: str
    content: str
    release_date: str | None = None
    download_url: str | None = None


@dataclass
class ProductGroup:
    """按产品线分组的版本列表。"""

    product: str
    label: str
    releases: list[ReleaseItem] = field(default_factory=list)


@dataclass
class HubUpdateCheckResult:
    """检查中枢更新的结果。"""

    status: str
    latest_version: str | None = None
    current_version: str | None = None
    title: str | None = None
    content: str | None = None
    download_url: str | None = None
    message: str | None = None


def _version_sort_key(version: str) -> tuple[int, object]:
    """版本排序 key：合法语义化版本按 Version 排（组 1），非法回退字符串（组 0）。"""
    try:
        return (1, Version(version.lower().lstrip("v")))
    except InvalidVersion:
        return (0, version)


def compare_versions(left: str, right: str) -> int:
    """语义化版本比较（v 前缀兼容）；无法解析时按字符串比较。

    Returns:
        left > right → 1；相等 → 0；left < right → -1。
    """
    try:
        lv = Version(left.lower().lstrip("v"))
        rv = Version(right.lower().lstrip("v"))
    except InvalidVersion:
        lv_s, rv_s = left.strip(), right.strip()
        return 0 if lv_s == rv_s else (1 if lv_s > rv_s else -1)
    if lv > rv:
        return 1
    if lv < rv:
        return -1
    return 0


def releases_api_url(hub_config_url: str) -> str | None:
    """从服务端配置下发接口地址推导版本列表接口地址。

    `.../portal/open/hub/config` → `.../portal/open/hub/releases`；
    未配置或无法推导时返回 None。
    """
    url = (hub_config_url or "").strip().rstrip("/")
    if not url:
        return None
    if url.endswith("/config"):
        return url[: -len("config")] + "releases"
    return f"{url}/portal/open/hub/releases"


class ReleaseService:
    """产品版本发布服务（进程内单例，见 api/deps.py）。"""

    async def fetch_releases(self) -> tuple[list[ReleaseItem] | None, str | None]:
        """从服务端拉取全部产品线版本列表。

        Returns:
            (releases, error)：成功时 releases 为记录列表（release_date 倒序）；
            失败时 releases 为 None，error 为用户可读的错误描述。
        """
        cfg = get_settings()
        url = releases_api_url(cfg.agent_resource_hub_config_url)
        if not url:
            return None, "未配置版本服务地址（AGENT_RESOURCE_HUB_CONFIG_URL）"
        try:
            async with httpx.AsyncClient(timeout=HUB_API_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    url,
                    headers={"X-Hub-Token": cfg.agent_resource_hub_token or ""},
                )
                resp.raise_for_status()
                payload = resp.json()
            raw = ((payload.get("data") or {}).get("releases")) or []
            releases = [
                ReleaseItem(
                    product=str(item.get("product") or ""),
                    version=str(item.get("version") or ""),
                    title=str(item.get("title") or ""),
                    content=str(item.get("content") or ""),
                    release_date=item.get("releaseDate"),
                    download_url=item.get("downloadUrl"),
                )
                for item in raw
                if item.get("product") and item.get("version")
            ]
            logger.info(
                "hub_releases_fetch_completed",
                extra={"event": "hub_releases_fetch_completed", "count": len(releases)},
            )
            return releases, None
        except Exception as exc:  # noqa: BLE001 网络异常统一降级
            logger.warning(
                "hub_releases_fetch_failed",
                extra={"event": "hub_releases_fetch_failed", "error": str(exc)},
            )
            return None, f"版本服务不可达：{exc}"

    def group_releases(self, releases: list[ReleaseItem]) -> list[ProductGroup]:
        """按产品线分组（分组间按 product 排序，组内按 release_date 倒序）。"""
        groups: dict[str, list[ReleaseItem]] = {}
        for item in releases:
            groups.setdefault(item.product, []).append(item)
        result = []
        for product in sorted(groups):
            items = sorted(
                groups[product],
                key=lambda r: (r.release_date or "", r.version),
                reverse=True,
            )
            result.append(
                ProductGroup(
                    product=product,
                    label=PRODUCT_LABELS.get(product, product),
                    releases=items,
                )
            )
        return result

    async def check_hub_update(self, current_version: str | None) -> HubUpdateCheckResult:
        """检查中枢更新：云端 hub 最新版本 vs 本地当前版本。

        Args:
            current_version: 本地当前版本号（system_versions.is_current，可为 None）。

        Returns:
            检查结果（update-available / up-to-date / failed）。
        """
        logger.info(
            "hub_update_check_started",
            extra={"event": "hub_update_check_started"},
        )
        releases, error = await self.fetch_releases()
        if releases is None:
            return HubUpdateCheckResult(
                status=UPDATE_STATUS_FAILED, message=error
            )
        hub_releases = [r for r in releases if r.product == "hub"]
        if not hub_releases:
            return HubUpdateCheckResult(
                status=UPDATE_STATUS_FAILED,
                current_version=current_version,
                message="版本服务尚未发布过中枢版本",
            )
        latest = max(hub_releases, key=lambda r: _version_sort_key(r.version))
        current = current_version or ""
        if current and compare_versions(latest.version, current) > 0:
            result = HubUpdateCheckResult(
                status=UPDATE_STATUS_AVAILABLE,
                latest_version=latest.version,
                current_version=current or None,
                title=latest.title,
                content=latest.content,
                download_url=latest.download_url,
                message=f"发现新版本 {latest.version}",
            )
        else:
            result = HubUpdateCheckResult(
                status=UPDATE_STATUS_UP_TO_DATE,
                latest_version=latest.version,
                current_version=current or None,
                message="当前已是最新版本" if current else "本地版本未设置",
            )
        logger.info(
            "hub_update_check_completed",
            extra={"event": "hub_update_check_completed", "status": result.status},
        )
        return result
