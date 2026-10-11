"""Agent 资源同步服务单元测试（ADR-022）。

重点覆盖部分更新场景：单独更新某一资源时，本地清单必须保留
其他资源条目（回归：旧实现整体覆盖导致其余资源误判「未落盘」）。
"""

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.resource_sync_service import ResourceSyncService


def _entry(key: str, sha: str, version: str = "") -> dict:
    """构造 manifest 资源条目。"""
    return {
        "key": key,
        "path": f"install-scripts/{key}.sh",
        "sha256": sha,
        "size": 100,
        "version": version,
    }


@pytest.fixture
def service(tmp_path: Path) -> ResourceSyncService:
    """提供以临时目录为缓存的服务实例。"""
    return ResourceSyncService(cache_dir=tmp_path)


def _seed_local(
    service: ResourceSyncService,
    items: list[dict],
    revision: str,
) -> None:
    """写入本地清单并为每个条目创建已落盘的空文件。"""
    service._save_local_manifest({"revision": revision, "resources": items})
    for item in items:
        dest = service.cache_dir / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"old")


async def test_apply_partial_update_preserves_other_entries(
    service: ResourceSyncService,
) -> None:
    """单独更新 A 时，本地清单中 B 的条目必须保留。"""
    local_a = _entry("install-script-linux", "a" * 64)
    local_b = _entry("agent-template-programming", "b" * 64, "0.4.3")
    _seed_local(service, [local_a, local_b], "2026-01-01T00:00:00Z")

    cloud_a = _entry("install-script-linux", "a2" * 32, "0.4.4")
    cloud_b = _entry("agent-template-programming", "b" * 64, "0.4.3")
    cloud_manifest = {"revision": "2026-01-02T00:00:00Z", "resources": [cloud_a, cloud_b]}

    async def _fake_download(url: str, dest: Path, sha: str, size: int) -> None:
        dest.write_bytes(b"new-content")

    with (
        patch.object(
            ResourceSyncService,
            "_fetch_cloud_manifest",
            AsyncMock(return_value=cloud_manifest),
        ),
        patch.object(
            ResourceSyncService, "_download_and_verify", side_effect=_fake_download
        ),
    ):
        result = await service.apply("https://example.com/", keys=["install-script-linux"])

    assert result.status == "up-to-date"
    assert result.updated_keys == ["install-script-linux"]

    by_key = {item["key"]: item for item in service.local_manifest()["resources"]}
    # 更新的条目写入新 sha256
    assert by_key["install-script-linux"]["sha256"] == "a2" * 32
    # 回归点：未更新条目保留在本地清单中
    assert by_key["agent-template-programming"]["sha256"] == "b" * 64


async def test_apply_full_update_writes_all_entries(service: ResourceSyncService) -> None:
    """全量更新时所有云端条目均写入本地清单。"""
    cloud_a = _entry("install-script-linux", "a" * 64)
    cloud_b = _entry("agent-template-programming", "b" * 64, "0.4.4")
    cloud_manifest = {"revision": "2026-01-02T00:00:00Z", "resources": [cloud_a, cloud_b]}

    async def _fake_download(url: str, dest: Path, sha: str, size: int) -> None:
        dest.write_bytes(b"content")

    with (
        patch.object(
            ResourceSyncService,
            "_fetch_cloud_manifest",
            AsyncMock(return_value=cloud_manifest),
        ),
        patch.object(
            ResourceSyncService, "_download_and_verify", side_effect=_fake_download
        ),
    ):
        result = await service.apply("https://example.com/")

    assert result.status == "up-to-date"
    assert sorted(result.updated_keys) == ["agent-template-programming", "install-script-linux"]
    assert len(service.local_manifest()["resources"]) == 2


async def test_apply_skips_up_to_date_entries(service: ResourceSyncService) -> None:
    """类型/指定 keys 更新时，本地已最新（sha 一致且文件在盘）的条目跳过下载。"""
    # 本地：A 已是云端版本（sha 一致），B 过时
    local_a = _entry("agent-code-linux-x64", "a" * 64, "0.5.0")
    local_b = _entry("install-script-code", "b" * 64)
    _seed_local(service, [local_a, local_b], "2026-01-01T00:00:00Z")

    cloud_a = _entry("agent-code-linux-x64", "a" * 64, "0.5.0")
    cloud_b = _entry("install-script-code", "b2" * 32)
    cloud_manifest = {"revision": "2026-01-02T00:00:00Z", "resources": [cloud_a, cloud_b]}

    downloaded_urls: list[str] = []

    async def _fake_download(url: str, dest: Path, sha: str, size: int) -> None:
        downloaded_urls.append(url)
        dest.write_bytes(b"new-content")

    with (
        patch.object(
            ResourceSyncService,
            "_fetch_cloud_manifest",
            AsyncMock(return_value=cloud_manifest),
        ),
        patch.object(
            ResourceSyncService, "_download_and_verify", side_effect=_fake_download
        ),
    ):
        result = await service.apply(
            "https://example.com/", keys=["agent-code-linux-x64", "install-script-code"]
        )

    assert result.status == "up-to-date"
    assert result.updated_keys == ["install-script-code"]
    # 仅过时条目触发下载，已最新条目跳过
    assert len(downloaded_urls) == 1
    assert "install-script-code" in downloaded_urls[0]

    by_key = {item["key"]: item for item in service.local_manifest()["resources"]}
    assert by_key["install-script-code"]["sha256"] == "b2" * 32
    assert by_key["agent-code-linux-x64"]["sha256"] == "a" * 64


async def test_apply_all_up_to_date_short_circuits(service: ResourceSyncService) -> None:
    """指定 keys 全部最新时不发起任何下载，直接返回 up-to-date。"""
    local_a = _entry("agent-code-linux-x64", "a" * 64, "0.5.0")
    _seed_local(service, [local_a], "2026-01-01T00:00:00Z")

    cloud_manifest = {
        "revision": "2026-01-02T00:00:00Z",
        "resources": [_entry("agent-code-linux-x64", "a" * 64, "0.5.0")],
    }

    async def _fail_download(url: str, dest: Path, sha: str, size: int) -> None:
        raise AssertionError("不应触发下载")

    with (
        patch.object(
            ResourceSyncService,
            "_fetch_cloud_manifest",
            AsyncMock(return_value=cloud_manifest),
        ),
        patch.object(
            ResourceSyncService, "_download_and_verify", side_effect=_fail_download
        ),
    ):
        result = await service.apply("https://example.com/", keys=["agent-code-linux-x64"])

    assert result.status == "up-to-date"
    assert result.message == "全部资源已是最新"
    assert result.updated_keys == []


async def test_apply_full_update_mirrors_cloud_removal(service: ResourceSyncService) -> None:
    """云端下架条目后，全量更新时本地清单同步移除该条目（镜像云端）。"""
    local_a = _entry("install-script-linux", "a" * 64)
    local_b = _entry("agent-template-programming", "b" * 64, "0.4.4")
    _seed_local(service, [local_a, local_b], "2026-01-01T00:00:00Z")

    # 云端清单已不含 agent-template-programming（发布端已删除条目）
    cloud_a = _entry("install-script-linux", "a2" * 32)
    cloud_manifest = {"revision": "2026-01-02T00:00:00Z", "resources": [cloud_a]}

    async def _fake_download(url: str, dest: Path, sha: str, size: int) -> None:
        dest.write_bytes(b"new-content")

    with (
        patch.object(
            ResourceSyncService,
            "_fetch_cloud_manifest",
            AsyncMock(return_value=cloud_manifest),
        ),
        patch.object(
            ResourceSyncService, "_download_and_verify", side_effect=_fake_download
        ),
    ):
        result = await service.apply("https://example.com/")

    assert result.status == "up-to-date"
    keys = [item["key"] for item in service.local_manifest()["resources"]]
    assert keys == ["install-script-linux"]


def test_status_view_hides_cloud_removed_entry(service: ResourceSyncService) -> None:
    """云端快照已下架的条目不再出现在状态视图（避免历史遗留）。"""
    local_a = _entry("install-script-linux", "a" * 64)
    local_b = _entry("agent-template-programming", "b" * 64, "0.4.4")
    _seed_local(service, [local_a, local_b], "2026-01-01T00:00:00Z")

    # 模拟一次检查后：云端快照只含 install-script-linux
    service._update_state(
        status="update-available",
        cloud_revision="2026-01-02T00:00:00Z",
        cloud_snapshot={"revision": "2026-01-02T00:00:00Z", "resources": [local_a]},
    )

    view = service.get_status_view()
    keys = [item["key"] for item in view["resources"]]
    # 云端为准：已下架条目不展示
    assert keys == ["install-script-linux"]


async def test_import_zip_merges_entries(service: ResourceSyncService) -> None:
    """增量 zip 导入（仅含单一资源）时保留本地已有条目。"""
    local_a = _entry("install-script-linux", "a" * 64)
    _seed_local(service, [local_a], "2026-01-01T00:00:00Z")

    zip_b = _entry(
        "agent-template-programming",
        hashlib.sha256(b"content").hexdigest(),
        "0.4.4",
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"revision": "2026-01-03T00:00:00Z", "resources": [zip_b]}),
        )
        zf.writestr(zip_b["path"], b"content")

    result = await service.import_zip(buf.getvalue())

    assert result.status == "imported"
    assert result.updated_keys == ["agent-template-programming"]
    by_key = {item["key"]: item for item in service.local_manifest()["resources"]}
    # 回归点：zip 未包含的本地条目保留
    assert by_key["install-script-linux"]["sha256"] == "a" * 64
    assert by_key["agent-template-programming"]["sha256"] == hashlib.sha256(b"content").hexdigest()
    # zip 资源落盘
    assert (service.cache_dir / zip_b["path"]).is_file()


def _make_tarball(version: str = "0.6.0", with_build_info: bool = True) -> bytes:
    """构造与 build-code-agent.sh 产物同构的假节点整体包。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        root = f"opc-agent-code-{version}"
        binary = b"fake-opc-agent-binary"
        info = tarfile.TarInfo(f"{root}/bin/opc-agent")
        info.size = len(binary)
        tar.addfile(info, io.BytesIO(binary))
        if with_build_info:
            body = json.dumps(
                {"artifact_version": version, "built_at": "2026-10-10T00:00:00Z"}
            ).encode()
            info_bj = tarfile.TarInfo(f"{root}/build-info.json")
            info_bj.size = len(body)
            tar.addfile(info_bj, io.BytesIO(body))
    return buf.getvalue()


async def test_import_tarball_success(service: ResourceSyncService) -> None:
    """合法整体包：按文件名/包内 build-info 生成条目并落盘。"""
    data = _make_tarball("0.6.0")
    result = await service.import_tarball(data, "opc-agent-code-0.6.0-linux-x64.tar.gz")

    assert result.status == "imported"
    assert result.updated_keys == ["agent-code-linux-x64"]
    item = next(
        r
        for r in service.local_manifest()["resources"]
        if r["key"] == "agent-code-linux-x64"
    )
    assert item["version"] == "0.6.0"
    assert item["path"] == "agent-code/0.6.0/opc-agent-code-0.6.0-linux-x64.tar.gz"
    assert item["sha256"] == hashlib.sha256(data).hexdigest()
    assert item["size"] == len(data)
    assert (service.cache_dir / item["path"]).is_file()


async def test_import_tarball_idempotent(service: ResourceSyncService) -> None:
    """同包重复导入：第二次返回 up-to-date，不重复写清单。"""
    data = _make_tarball("0.6.0")
    filename = "opc-agent-code-0.6.0-linux-x64.tar.gz"
    first = await service.import_tarball(data, filename)
    assert first.status == "imported"

    again = await service.import_tarball(data, filename)
    assert again.status == "up-to-date"
    assert service.local_manifest()["resources"].count(
        next(
            r
            for r in service.local_manifest()["resources"]
            if r["key"] == "agent-code-linux-x64"
        )
    ) == 1


async def test_import_tarball_bad_filename(service: ResourceSyncService) -> None:
    """文件名不符合规范时明确报错，不落盘。"""
    result = await service.import_tarball(_make_tarball(), "random-package.tar.gz")

    assert result.status == "failed"
    assert result.message is not None and "无法从文件名识别" in result.message
    assert service.local_manifest() is None


async def test_import_tarball_without_build_info_uses_filename_version(
    service: ResourceSyncService,
) -> None:
    """包内无 build-info.json 时回退文件名版本（office 包等场景）。"""
    data = _make_tarball("0.7.0", with_build_info=False)
    result = await service.import_tarball(data, "opc-agent-office-0.7.0-linux-x64.tar.gz")

    assert result.status == "imported"
    assert result.updated_keys == ["agent-office-linux-x64"]
    item = next(
        r
        for r in service.local_manifest()["resources"]
        if r["key"] == "agent-office-linux-x64"
    )
    assert item["version"] == "0.7.0"
    assert item["path"].startswith("agent-office/0.7.0/")
