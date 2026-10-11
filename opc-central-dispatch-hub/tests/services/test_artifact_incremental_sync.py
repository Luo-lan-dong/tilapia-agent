"""增量产物同步测试（ADR-059 D4）：diff 判定、单文件失败隔离、状态推进。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.artifact_service import (
    MAX_INCREMENTAL_FILES_PER_CYCLE,
    ArtifactService,
)


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ArtifactService:
    """构造缓存目录指向 tmp_path 的服务实例。"""
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "artifact_cache_dir", str(tmp_path), raising=False)
    return ArtifactService()


def _entry(path: str, size: int = 10, mtime: float = 1000.0) -> dict:
    return {"path": path, "size": size, "mtime": mtime}


def test_first_cycle_selects_everything(service: ArtifactService) -> None:
    """首次同步：所有文件都未同步 → 全部入选（受单轮上限约束）。"""
    files = [_entry("src/a.py"), _entry("src/b.py")]
    assert service.select_changed_files("t1", files) == ["src/a.py", "src/b.py"]


def test_unchanged_files_are_skipped(service: ArtifactService) -> None:
    """已同步且 size/mtime 未变 → 不再重复拉取（增量语义的核心）。"""
    files = [_entry("src/a.py", size=10, mtime=1000.0)]
    service._save_synced_state("t1", {"src/a.py": {"size": 10, "mtime": 1000.0}})
    # 本地缓存文件存在是「已同步」的另一半条件
    cached = Path(service._task_dir("t1")) / "src/a.py"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("x", encoding="utf-8")

    assert service.select_changed_files("t1", files) == []


def test_size_or_mtime_change_is_detected(service: ArtifactService) -> None:
    """size 或 mtime 变化即视为变更（执行中改文件必须被再次拉取）。"""
    service._save_synced_state("t1", {"src/a.py": {"size": 10, "mtime": 1000.0}})
    cached = Path(service._task_dir("t1")) / "src/a.py"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("x", encoding="utf-8")

    assert service.select_changed_files("t1", [_entry("src/a.py", size=11)]) == ["src/a.py"]
    assert service.select_changed_files("t1", [_entry("src/a.py", mtime=1001.0)]) == ["src/a.py"]


def test_unsynced_entry_missing_locally_is_reselected(service: ArtifactService) -> None:
    """指纹一致但本地缓存文件丢失（被清理）→ 重新拉取，避免现场不完整。"""
    service._save_synced_state("t1", {"src/a.py": {"size": 10, "mtime": 1000.0}})
    assert service.select_changed_files("t1", [_entry("src/a.py")]) == ["src/a.py"]


def test_invalid_and_oversized_entries_are_ignored(service: ArtifactService) -> None:
    """越界路径与超大文件不得进入增量拉取（安全 + 防拖垮）。

    说明：``_sanitize_relative_path`` 的既定语义是「前导 ``/`` 视为工作目录内
    根相对路径」（``/abs.py`` → ``abs.py``），真正的越界靠拒绝 ``..`` 片段
    与反斜杠实现——这里连同该语义一起锁定，避免后续误改成更宽松的实现。
    """
    files = [
        _entry("../escape.py"),
        _entry("a/../../b.py"),
        _entry("..\\win.py"),
        _entry("big.bin", size=50 * 1024 * 1024),
        _entry("ok.py"),
    ]
    assert service.select_changed_files("t1", files) == ["ok.py"]
    # 前导 / 与重复 / 归一为工作目录内相对路径（不越界）
    assert service.select_changed_files("t1", [_entry("/abs.py")]) == ["abs.py"]


def test_cycle_limit_caps_selection(service: ArtifactService) -> None:
    """单轮拉取数量有上限，避免首次同步把节点打爆。"""
    files = [_entry(f"src/f{i}.py") for i in range(MAX_INCREMENTAL_FILES_PER_CYCLE + 5)]
    assert len(service.select_changed_files("t1", files)) == MAX_INCREMENTAL_FILES_PER_CYCLE


async def test_sync_incremental_persists_state(
    service: ArtifactService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功拉取后推进指纹；下一轮同一清单不再重复拉取。"""
    fetched: list[str] = []

    async def _fake_fetch(task_id: str, node_id: str | None, path: str) -> Path:
        fetched.append(path)
        target = Path(service._task_dir(task_id)) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("content", encoding="utf-8")
        return target

    monkeypatch.setattr(service, "fetch_file", _fake_fetch)
    files = [_entry("src/a.py"), _entry("src/b.py")]

    assert await service.sync_incremental("t1", "node-1", files) == 2
    assert fetched == ["src/a.py", "src/b.py"]
    assert service.get_synced_state("t1") == {
        "src/a.py": {"size": 10, "mtime": 1000.0},
        "src/b.py": {"size": 10, "mtime": 1000.0},
    }
    # 第二轮：清单未变 → 零拉取
    assert await service.sync_incremental("t1", "node-1", files) == 0
    assert fetched == ["src/a.py", "src/b.py"]


async def test_sync_incremental_isolates_single_file_failure(
    service: ArtifactService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单文件失败只跳过该文件并保留其未同步状态，不阻断其余文件。"""

    async def _flaky_fetch(task_id: str, node_id: str | None, path: str) -> Path:
        if path.endswith("b.py"):
            raise RuntimeError("节点离线")
        target = Path(service._task_dir(task_id)) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("content", encoding="utf-8")
        return target

    monkeypatch.setattr(service, "fetch_file", _flaky_fetch)
    files = [_entry("src/a.py"), _entry("src/b.py")]

    assert await service.sync_incremental("t1", "node-1", files) == 1
    state = service.get_synced_state("t1")
    assert "src/a.py" in state
    assert "src/b.py" not in state  # 下轮重试


async def test_sync_incremental_without_node_is_noop(service: ArtifactService) -> None:
    """节点未知（未分配）时不做任何拉取。"""
    assert await service.sync_incremental("t1", None, [_entry("src/a.py")]) == 0


def test_synced_state_survives_corruption(service: ArtifactService) -> None:
    """状态文件损坏时按「全部未同步」处理，不抛异常。"""
    path = service._synced_state_path("t1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert service.get_synced_state("t1") == {}
    assert service.select_changed_files("t1", [_entry("src/a.py")]) == ["src/a.py"]


def test_manifest_and_state_files_are_separate(service: ArtifactService) -> None:
    """清单与同步指纹分文件存放：清单可整体覆盖，指纹须逐文件累积。"""
    service.save_file_tree("t1", [_entry("src/a.py")], truncated=False)
    service._save_synced_state("t1", {"src/a.py": {"size": 10, "mtime": 1000.0}})

    manifest = json.loads(service._manifest_path("t1").read_text(encoding="utf-8"))
    state = json.loads(service._synced_state_path("t1").read_text(encoding="utf-8"))
    assert manifest["files"][0]["path"] == "src/a.py"
    assert state["files"]["src/a.py"]["size"] == 10
