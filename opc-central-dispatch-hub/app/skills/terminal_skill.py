"""终端命令执行技能（ADR-053）。

AI 在中枢本机执行终端命令的双轨入口：
- 结构化执行：subprocess 非交互执行，stdout/stderr/exit_code 回传 AI；
- 终端可见：命令与输出经 terminal_service.ai_echo 回显到该会话终端流。

权限三档（terminal_permission_mode，system_settings 热读取）：
- manual：每条命令发 command_approval_request 事件（前端确认卡），
  技能内 asyncio.Event 等待用户批准（超时按拒绝）；
- ai_auto：模型自评 risk_level，low 直接执行，high/unknown 转审批；
- full：直接执行（终端面板切换处有红色警示）。
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system_settings_service import SystemSettingsService
from app.services.terminal_approval import terminal_approval_registry
from app.services.terminal_service import terminal_service
from app.skills.base import RESULT_EVENT, Skill, SkillContext, SkillResult

logger = logging.getLogger(__name__)

#: 单命令默认/最大超时（秒）与输出截断（字符）
DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
MAX_OUTPUT_CHARS = 4000

_VALID_MODES = ("manual", "ai_auto", "full")
_settings_service = SystemSettingsService()


def _result(success: bool, summary: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event": RESULT_EVENT,
        "data": SkillResult(success=success, summary=summary, data=data or {}),
    }


def _truncate(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n…[输出截断，共 {len(text)} 字符]"
    return text


class RunCommandSkill(Skill):
    """在中枢本机执行终端命令（受三档权限控制）。"""

    name = "run_command"
    display_name = "执行终端命令"
    description = (
        "在中枢本机（Windows PowerShell 语义）执行终端命令并返回"
        " stdout/stderr/exit_code。适用：构建、依赖安装、git、文件操作、"
        "本地服务管理等。必须自评 risk_level：low=只读或明显无害"
        "（dir/ls/type/cat/git status/npm run 构建等）；high=删除/移动覆盖/"
        "格式化/下载并执行/改系统配置/涉及凭据与端口暴露等；不确定=unknown。"
        "权限由中枢配置决定：manual=每条命令需用户在对话中批准；ai_auto=low "
        "直接执行、high/unknown 自动转用户批准；full=直接执行。"
        "命令在当前会话工作目录执行；输出超长会截断；不要执行交互式全屏程序"
        "（vim/top 等），需要确认的命令优先加 -y/--yes 参数。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令行（单条）"},
            "risk_level": {
                "type": "string",
                "enum": ["low", "high", "unknown"],
                "description": "命令风险自评：low/ high/ unknown",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": f"超时秒数（5-{MAX_TIMEOUT_SECONDS}，默认 {DEFAULT_TIMEOUT_SECONDS}）",
            },
        },
        "required": ["command", "risk_level"],
    }
    returns = (
        "{success, summary(含命令/退出码/stdout/stderr 截断), "
        "data:{exit_code, timed_out, mode, approved_by, command}}"
    )

    async def _read_mode(self, session: AsyncSession) -> str:
        """读取权限模式（非法值回退 manual）。"""
        try:
            settings = await _settings_service.get_network_settings(session)
            mode = settings.terminal_permission_mode
            return mode if mode in _VALID_MODES else "manual"
        except Exception:  # noqa: BLE001 - 读取失败回退最安全档
            logger.warning(
                "terminal_mode_read_failed_fallback_manual",
                extra={"event": "terminal_mode_read_failed_fallback_manual"},
                exc_info=True,
            )
            return "manual"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """按三档权限执行命令并回显终端。"""
        command = str(context.args.get("command") or "").strip()
        risk_level = str(context.args.get("risk_level") or "unknown").strip().lower()
        if risk_level not in ("low", "high", "unknown"):
            risk_level = "unknown"
        try:
            timeout_s = int(context.args.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            timeout_s = DEFAULT_TIMEOUT_SECONDS
        timeout_s = min(max(timeout_s, 5), MAX_TIMEOUT_SECONDS)

        if not command:
            yield _result(False, "参数不完整：必须提供 command。")
            return

        mode = await self._read_mode(session)
        needs_approval = mode == "manual" or (mode == "ai_auto" and risk_level != "low")
        approved_by = "ai_auto" if (mode == "ai_auto" and not needs_approval) else mode

        if needs_approval:
            approval = terminal_approval_registry.create(
                context.session_id or "", command, mode, risk_level
            )
            yield {
                "event": "command_approval_request",
                "data": {
                    "approval_id": approval.approval_id,
                    "command": command,
                    "mode": mode,
                    "risk_level": risk_level,
                },
            }
            approved = await terminal_approval_registry.wait(approval)
            if not approved:
                yield _result(
                    False,
                    f"命令未执行：用户拒绝或审批超时（{command}）。",
                    data={"command": command, "mode": mode, "approved_by": "denied"},
                )
                return
            approved_by = "user"

        cwd = context.output_dir or terminal_service.default_cwd()
        timed_out = False
        exit_code: int | None = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd if cwd and str(cwd).strip() else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s
                )
                exit_code = proc.returncode
            except TimeoutError:
                timed_out = True
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=5)
                stdout_b, stderr_b = b"", "[命令超时被终止]".encode()
        except Exception as exc:  # noqa: BLE001 - 创建进程失败（cwd 不存在等）
            yield _result(
                False,
                f"命令执行失败：{exc}（command: {command}，cwd: {cwd}）",
                data={"command": command, "mode": mode, "approved_by": approved_by},
            )
            return

        stdout = (stdout_b or b"").decode("utf-8", errors="replace").strip()
        stderr = (stderr_b or b"").decode("utf-8", errors="replace").strip()
        combined = "\n".join(part for part in (stdout, stderr) if part)

        # 回显到会话终端流（终端面板 AI 执行样式块）
        session_key = context.session_id or ""
        if session_key:
            terminal_service.ai_echo(session_key, command, combined, exit_code)

        status_text = "命令超时被终止" if timed_out else f"退出码 {exit_code}"
        summary = (
            f"命令执行完成（{status_text}）。\n"
            f"$ {command}\n"
            f"stdout/stderr:\n{_truncate(combined) if combined else '（无输出）'}"
        )
        logger.info(
            "terminal_command_executed",
            extra={
                "event": "terminal_command_executed",
                "session_id": session_key,
                "mode": mode,
                "approved_by": approved_by,
                "risk_level": risk_level,
                "exit_code": exit_code,
                "timed_out": timed_out,
            },
        )
        yield _result(
            True,
            summary,
            data={
                "command": command,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "mode": mode,
                "approved_by": approved_by,
            },
        )
