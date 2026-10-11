"""编排 AI 产物感知与文件访问测试（ADR-068）。

覆盖：list_artifacts 产物状态/打包兜底、read_file 三类白名单来源与
二进制/越界拒绝、Upload 代执行产物文件源扩展、唤醒消息产物指引。
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session_attachment import SessionAttachment
from app.models.task import Task
from app.models.task_building_session import TaskBuildingSession
from app.services.artifact_service import artifact_service
from app.services.orchestrator_wake_service import build_wake_message
from app.skills.artifact_access import ListArtifactsSkill, ReadFileSkill
from app.skills.base import SkillContext
from app.skills.bt_call_guard import maybe_run_local_command


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理任务/会话/附件表（外键顺序，用例前后各一遍）。"""
    for _phase in range(2):
        await db_session.execute(
            TaskBuildingSession.__table__.update().values(task_id=None)
        )
        await db_session.execute(delete(SessionAttachment))
        await db_session.execute(delete(TaskBuildingSession))
        await db_session.execute(delete(Task))
        await db_session.commit()
        if _phase == 0:
            yield


@pytest.fixture()
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """产物缓存目录指向 tmp_path。"""
    from app.core import config

    settings = config.get_settings()
    target = tmp_path / "artifacts"
    monkeypatch.setattr(settings, "artifact_cache_dir", str(target), raising=False)
    return target


@pytest.fixture()
def attachment_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """附件存储目录指向 tmp_path。"""
    from app.core import config

    settings = config.get_settings()
    target = tmp_path / "attachments"
    monkeypatch.setattr(settings, "attachment_storage_dir", str(target), raising=False)
    return target


_SAMPLE_FILES: dict[str, str] = {
    "src/main.py": "print('hi')",
    "README.md": "# demo 文档",
    "requirements.txt": "flask\n",
}


def _seed_artifacts(
    cache_dir: Path,
    task_id: str,
    *,
    with_zip: bool = True,
    with_manifest: bool = True,
    files: dict[str, str] | None = None,
) -> Path:
    """在缓存目录造任务产物（散件 + manifest + 可选 zip）。"""
    content = dict(_SAMPLE_FILES if files is None else files)
    task_dir = cache_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    for rel, text in content.items():
        target = task_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if with_manifest:
        manifest = {
            "task_id": task_id,
            "updated_at": 0.0,
            "truncated": False,
            "files": [
                {"path": rel, "size": len(text.encode()), "mtime": 1.0}
                for rel, text in content.items()
            ],
        }
        (task_dir / ".manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
    if with_zip:
        (task_dir / "export.zip").write_bytes(b"PK-zip-original")
    return task_dir


async def _make_task_with_session(
    db_session: AsyncSession,
) -> tuple[Task, TaskBuildingSession]:
    """造「任务 + 关联构建会话」并返回。"""
    task = Task(
        title="测试任务",
        agent_type="code",
        status="completed",
        execution_mode="build",
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add(task)
    await db_session.flush()
    chat = TaskBuildingSession(title="s", messages=[], task_id=task.id)
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    return task, chat


async def _collect(skill, db_session: AsyncSession, context: SkillContext):
    """执行技能并收集事件，返回最后一个 _result 数据。"""
    events = [event async for event in skill.execute(db_session, context)]
    return events[-1]["data"]


# ---------------------------------------------------------------------------
# list_artifacts
# ---------------------------------------------------------------------------


async def test_list_without_task_fails(db_session: AsyncSession) -> None:
    """UT-1：会话无关联任务时失败并指引。"""
    result = await _collect(ListArtifactsSkill(), db_session, SkillContext(args={}))
    assert result.success is False
    assert "没有关联任务" in result.summary


async def test_list_without_artifacts(db_session: AsyncSession, cache_dir: Path) -> None:
    """UT-2：任务无产物目录时提示缺失与兜底方式。"""
    _, chat = await _make_task_with_session(db_session)
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _collect(ListArtifactsSkill(), db_session, context)
    assert result.success is True
    assert "export.zip：不存在" in result.summary
    assert "build_zip=true" in result.summary


async def test_list_full_status(db_session: AsyncSession, cache_dir: Path) -> None:
    """UT-3：export.zip + manifest 齐全时返回大小、清单与同步数。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id))
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _collect(ListArtifactsSkill(), db_session, context)
    assert "export.zip：存在" in result.summary
    assert "README.md" in result.summary
    assert "src/main.py" in result.summary
    assert "3 项" in result.summary


async def test_list_manifest_missing_scans_directory(
    db_session: AsyncSession, cache_dir: Path
) -> None:
    """UT-4：manifest 缺失时降级为目录扫描列举散件。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id), with_manifest=False)
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _collect(ListArtifactsSkill(), db_session, context)
    assert "README.md" in result.summary
    assert "3 项" in result.summary


async def test_list_truncated_for_large_project(
    db_session: AsyncSession, cache_dir: Path
) -> None:
    """UT-5：清单超上限时截断展示并提示总数。"""
    task, chat = await _make_task_with_session(db_session)
    files = {f"m{index:02d}.txt": "x" for index in range(60)}
    _seed_artifacts(cache_dir, str(task.id), with_zip=False, files=files)
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _collect(ListArtifactsSkill(), db_session, context)
    assert "60 项" in result.summary
    assert "其余 10 项略" in result.summary


async def test_build_zip_from_cache_success(
    db_session: AsyncSession, cache_dir: Path
) -> None:
    """UT-6：export.zip 缺失时本地打包成功且附不完整性警告。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id), with_zip=False)
    context = SkillContext(args={"build_zip": True}, session_id=str(chat.id))
    result = await _collect(ListArtifactsSkill(), db_session, context)
    assert "export.zip：存在" in result.summary
    assert "⚠️" in result.summary and "可能缺大文件" in result.summary
    built = artifact_service.get_cached_zip_path(str(task.id))
    assert built is not None
    with zipfile.ZipFile(built) as archive:
        names = set(archive.namelist())
    assert {"src/main.py", "README.md", "requirements.txt"} <= names
    assert ".manifest.json" not in names


async def test_build_zip_rejected_when_cache_empty(
    db_session: AsyncSession, cache_dir: Path
) -> None:
    """UT-7：无任何本地缓存时兜底拒绝。"""
    _, chat = await _make_task_with_session(db_session)
    context = SkillContext(args={"build_zip": True}, session_id=str(chat.id))
    result = await _collect(ListArtifactsSkill(), db_session, context)
    assert result.success is False
    assert "无可打包文件" in result.summary


async def test_build_zip_keeps_existing_package(
    db_session: AsyncSession, cache_dir: Path
) -> None:
    """UT-8：export.zip 已存在时不覆盖节点全量同步产物。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id), with_zip=True)
    context = SkillContext(args={"build_zip": True}, session_id=str(chat.id))
    result = await _collect(ListArtifactsSkill(), db_session, context)
    assert "⚠️" not in result.summary
    built = artifact_service.get_cached_zip_path(str(task.id))
    assert built is not None and built.read_bytes() == b"PK-zip-original"


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


async def _read(db_session: AsyncSession, context: SkillContext, path: str):
    return await _collect(ReadFileSkill(), db_session, SkillContext({"path": path} | context.args, session_id=context.session_id, output_dir=context.output_dir))


async def test_read_artifact_text(db_session: AsyncSession, cache_dir: Path) -> None:
    """UT-9：产物内文本文件返回全文并标注来源。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id))
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _read(db_session, context, "README.md")
    assert result.success is True
    assert "# demo 文档" in result.summary
    assert "产物目录" in result.summary


async def test_read_binary_rejected(db_session: AsyncSession, cache_dir: Path) -> None:
    """UT-10：NUL 字节判定二进制并拒绝。"""
    task, chat = await _make_task_with_session(db_session)
    task_dir = _seed_artifacts(cache_dir, str(task.id))
    (task_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _read(db_session, context, "logo.png")
    assert result.success is False
    assert "二进制" in result.summary


async def test_read_oversized_truncated(
    db_session: AsyncSession, cache_dir: Path
) -> None:
    """UT-11：超 2MB 文本文件截断并提示。"""
    from app.skills.artifact_access import READ_MAX_BYTES

    task, chat = await _make_task_with_session(db_session)
    task_dir = _seed_artifacts(cache_dir, str(task.id), with_zip=False, files={})
    (task_dir / "big.log").write_bytes(b"a" * (READ_MAX_BYTES + 100))
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _read(db_session, context, "big.log")
    assert result.success is True
    assert "已截断" in result.summary


async def test_read_escape_path_rejected(
    db_session: AsyncSession, cache_dir: Path
) -> None:
    """UT-12：``..`` 越界路径拒绝。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id))
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _read(db_session, context, "../other/secret.txt")
    assert result.success is False
    assert "可读范围" in result.summary


async def test_read_outside_scope_rejected(db_session: AsyncSession) -> None:
    """UT-13：中枢内非白名单路径拒绝并列出可读范围。"""
    _, chat = await _make_task_with_session(db_session)
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _read(db_session, context, "app/core/config.py")
    assert result.success is False
    assert "可读范围" in result.summary


async def test_read_session_attachment(
    db_session: AsyncSession, attachment_dir: Path
) -> None:
    """UT-14：按文件名读本会话上传的附件。"""
    _, chat = await _make_task_with_session(db_session)
    rel = f"{chat.id}/doc.md"
    target = attachment_dir / rel
    target.parent.mkdir(parents=True)
    target.write_text("# 用户需求文档", encoding="utf-8")
    db_session.add(
        SessionAttachment(
            session_id=chat.id,
            file_name="doc.md",
            mime="text/markdown",
            size=10,
            kind="archive",
            storage_path=rel,
        )
    )
    await db_session.commit()
    context = SkillContext(args={}, session_id=str(chat.id))
    result = await _read(db_session, context, "doc.md")
    assert result.success is True
    assert "# 用户需求文档" in result.summary
    assert "会话附件" in result.summary


async def test_read_output_dir_file(db_session: AsyncSession, tmp_path: Path) -> None:
    """UT-15：会话绑定工作目录内的路径可读。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("工作区笔记", encoding="utf-8")
    context = SkillContext(args={}, output_dir=str(workspace))
    result = await _read(db_session, context, "notes.txt")
    assert result.success is True
    assert "工作区笔记" in result.summary
    assert "工作目录" in result.summary


async def test_read_prefers_artifact_over_output(
    db_session: AsyncSession, cache_dir: Path, tmp_path: Path
) -> None:
    """产物命中优先于工作目录（同路径名时不串源）。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id))
    context = SkillContext(args={}, session_id=str(chat.id), output_dir=str(tmp_path))
    result = await _read(db_session, context, "README.md")
    assert "产物目录" in result.summary


# ---------------------------------------------------------------------------
# Upload 文件源扩展（bt_call_guard）
# ---------------------------------------------------------------------------


def _upload_first_result() -> dict:
    """构造宝塔 Upload 两段式首次返回。"""
    return {
        "status": True,
        "msg": "上传会话已创建",
        "file_id": "file_art",
        "ready": False,
        "next_action": {
            "type": "run_local_command",
            "windows": 'curl.exe --upload-file "<LOCAL_FILE>" https://panel/mcp/upload/file_art',
        },
    }


class _Proc:
    returncode = 0

    async def communicate(self):
        return (
            json.dumps(
                {
                    "status": True,
                    "ready": True,
                    "file_id": "file_art",
                    "path": "/www/server/probe.zip",
                }
            ).encode(),
            None,
        )


async def _fake_shell(*_args, **_kwargs):
    """替身 create_subprocess_shell（被 await，返回 _Proc）。"""
    return _Proc()


async def test_upload_resolves_session_artifact_zip(
    db_session: AsyncSession,
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-16：file_name=export.zip 命中会话任务产物 → 中枢代执行上传。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id), with_zip=True)
    monkeypatch.setattr(
        "app.skills.bt_call_guard.asyncio.create_subprocess_shell",
        _fake_shell,
    )
    client = AsyncMock()
    client.call_tool = AsyncMock(
        return_value={"status": True, "ready": True, "file_id": "file_art", "path": "/www/server/probe.zip"}
    )
    out = await maybe_run_local_command(
        db_session,
        client=client,
        tool="Upload",
        result=_upload_first_result(),
        arguments={"file_name": "export.zip"},
        session_id=str(chat.id),
    )
    assert out["ready"] is True
    assert "中枢已代执行" in out["hub_note"]


async def test_upload_resolves_artifact_tree_file(
    db_session: AsyncSession,
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-17：file_name 为文件树相对路径（src/main.py）→ 命中产物缓存。"""
    task, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(task.id), with_zip=True)
    monkeypatch.setattr(
        "app.skills.bt_call_guard.asyncio.create_subprocess_shell",
        _fake_shell,
    )
    client = AsyncMock()
    client.call_tool = AsyncMock(
        return_value={"status": True, "ready": True, "file_id": "file_art"}
    )
    out = await maybe_run_local_command(
        db_session,
        client=client,
        tool="Upload",
        result=_upload_first_result(),
        arguments={"file_name": "src/main.py"},
        session_id=str(chat.id),
    )
    assert out["ready"] is True
    assert "中枢已代执行" in out["hub_note"]


async def test_upload_miss_hints_list_artifacts(
    db_session: AsyncSession, cache_dir: Path
) -> None:
    """UT-19：附件与产物均未命中时 hub_note 指引 list_artifacts。"""
    _, chat = await _make_task_with_session(db_session)
    _seed_artifacts(cache_dir, str(chat.task_id), with_zip=False)
    out = await maybe_run_local_command(
        db_session,
        client=AsyncMock(),
        tool="Upload",
        result=_upload_first_result(),
        arguments={"file_name": "不存在.zip"},
        session_id=str(chat.id),
    )
    assert out["ready"] is False
    assert "list_artifacts" in out["hub_note"]


# ---------------------------------------------------------------------------
# 唤醒消息产物指引
# ---------------------------------------------------------------------------


def test_wake_message_contains_artifact_hints() -> None:
    """UT-22：唤醒消息含 list_artifacts / read_file / Upload 产物指引。"""
    msg = build_wake_message(title="t", terminal_status="completed", detail="完成")
    assert "list_artifacts" in msg
    assert "read_file" in msg
    assert "export.zip" in msg
