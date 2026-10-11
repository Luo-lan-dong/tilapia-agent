"""ADR-026 版本发布服务单元测试：版本比较 / 分组 / 检查更新三态 / 地址推导。"""

from unittest.mock import AsyncMock

import pytest

from app.services.release_service import (
    ReleaseItem,
    ReleaseService,
    compare_versions,
    releases_api_url,
)


class TestCompareVersions:
    """语义化版本比较。"""

    def test_greater(self) -> None:
        assert compare_versions("v0.6.0", "v0.5.0") == 1

    def test_equal(self) -> None:
        assert compare_versions("v0.5.0", "0.5.0") == 0

    def test_less(self) -> None:
        assert compare_versions("0.5.0", "v0.6.0") == -1

    def test_invalid_fallback_string_compare(self) -> None:
        assert compare_versions("abc", "abd") == -1


class TestReleasesApiUrl:
    """服务端配置地址推导。"""

    def test_from_config_url(self) -> None:
        assert releases_api_url("http://localhost:8080/portal/open/hub/config") == (
            "http://localhost:8080/portal/open/hub/releases"
        )

    def test_from_base_url(self) -> None:
        assert releases_api_url("http://localhost:8080") == (
            "http://localhost:8080/portal/open/hub/releases"
        )

    def test_empty(self) -> None:
        assert releases_api_url("  ") is None


def _release(
    product: str,
    version: str,
    date: str = "2026-09-10",
    download_url: str | None = None,
) -> ReleaseItem:
    return ReleaseItem(
        product=product,
        version=version,
        title=f"{product} {version}",
        content="- change",
        release_date=date,
        download_url=download_url,
    )


class TestGroupReleases:
    """按产品线分组与排序。"""

    def test_grouped_and_sorted(self) -> None:
        service = ReleaseService()
        groups = service.group_releases(
            [
                _release("agent-code", "0.4.4", "2026-09-01"),
                _release("hub", "v0.5.0", "2026-09-02"),
                _release("agent-code", "0.5.0", "2026-09-05"),
            ]
        )
        assert [(g.product, g.label) for g in groups] == [
            ("agent-code", "Code Agent"),
            ("hub", "中枢"),
        ]
        assert [r.version for r in groups[0].releases] == ["0.5.0", "0.4.4"]

    def test_unknown_product_label_fallback(self) -> None:
        service = ReleaseService()
        groups = service.group_releases([_release("agent-video", "1.0.0")])
        assert groups[0].label == "agent-video"


class TestCheckHubUpdate:
    """检查中枢更新三态。"""

    @pytest.mark.asyncio
    async def test_update_available(self) -> None:
        service = ReleaseService()
        service.fetch_releases = AsyncMock(  # type: ignore[method-assign]
            return_value=([_release("hub", "v0.6.0", download_url="http://x/a.exe")], None)
        )
        result = await service.check_hub_update("v0.5.0")
        assert result.status == "update-available"
        assert result.latest_version == "v0.6.0"
        assert result.current_version == "v0.5.0"
        assert result.download_url == "http://x/a.exe"

    @pytest.mark.asyncio
    async def test_latest_picks_max_version_not_first(self) -> None:
        """多条 hub 记录时取语义化最大版本，而非列表第一条（回退 bug 回归）。"""
        service = ReleaseService()
        service.fetch_releases = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                [_release("hub", "0.0.6", "2026-09-09"), _release("hub", "v0.5.1", "2026-09-09")],
                None,
            )
        )
        result = await service.check_hub_update("v0.5.0")
        assert result.latest_version == "v0.5.1"
        assert result.status == "update-available"

    @pytest.mark.asyncio
    async def test_up_to_date(self) -> None:
        service = ReleaseService()
        service.fetch_releases = AsyncMock(  # type: ignore[method-assign]
            return_value=([_release("hub", "v0.5.0")], None)
        )
        result = await service.check_hub_update("v0.5.0")
        assert result.status == "up-to-date"

    @pytest.mark.asyncio
    async def test_no_hub_releases(self) -> None:
        service = ReleaseService()
        service.fetch_releases = AsyncMock(  # type: ignore[method-assign]
            return_value=([_release("agent-code", "0.5.0")], None)
        )
        result = await service.check_hub_update("v0.5.0")
        assert result.status == "failed"
        assert result.message is not None and "尚未发布" in result.message

    @pytest.mark.asyncio
    async def test_fetch_failed(self) -> None:
        service = ReleaseService()
        service.fetch_releases = AsyncMock(  # type: ignore[method-assign]
            return_value=(None, "版本服务不可达：连接超时")
        )
        result = await service.check_hub_update("v0.5.0")
        assert result.status == "failed"
        assert result.message is not None and "版本服务不可达" in result.message
