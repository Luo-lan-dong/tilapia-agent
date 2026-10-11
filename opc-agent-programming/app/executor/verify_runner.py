"""节点侧分层验收执行器（ADR-061 M4）。

**职责**：在任务完成前，按分层判据（L0→L4）逐条执行检查，产出结构化报告，供
**完成闸**判定「是否允许上报 completed」。

**判据来源优先级（用户已定）**：`verify.json`（中枢下发，落盘在 `specs/<doc_dir>/`）
> 项目自带配置（package.json scripts / pyproject.toml / Makefile）> 按文件类型推导；
**都没有 → `unverifiable`（「无法验证」），不得据此判成功**（用户已定：不许假成功）。

**必过层规则（用户已定，与中枢 `app/services/verify_plan.py` 同步）**：L0/L1 必过；
L2 存在即必过；L3/L4 仅当判据显式标注 `must_pass: true`。

**每层超时（用户已定）**：L0 30s / L1 60s / L2 5min / L3 10min / L4 20min；单条 check 可覆盖。

**失败语义**：非 0 退出码 → `failed`；超时 → `timeout`（**按未通过计**）；执行器自身异常
（如工作目录消失）→ `error`（同样按未通过计）。必过检查有任何非 `passed` → 完成闸拒绝。
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 分层封闭枚举
VERIFY_LAYERS = ("L0", "L1", "L2", "L3", "L4")
#: 各层默认超时（秒）
LAYER_TIMEOUTS: dict[str, int] = {"L0": 30, "L1": 60, "L2": 300, "L3": 600, "L4": 1200}
#: 单条 check 超时上限
MAX_CHECK_TIMEOUT_S = 3600
#: 判据条数上限
MAX_CHECKS = 30
#: 命令/名称长度上限
MAX_CMD_CHARS = 2000
MAX_NAME_CHARS = 200
#: 默认必过层
DEFAULT_MUST_PASS = {"L0": True, "L1": True, "L2": True, "L3": False, "L4": False}
#: 命令输出尾部保留字符数（回传 LLM 与落痕用）
OUTPUT_TAIL_CHARS = 2000
#: 单条检查最长等待（防止看门狗把任务判死）
_TERMINATE_GRACE_SECONDS = 5


def default_must_pass(layer: str) -> bool:
    """某层默认是否必过（L3/L4 默认否）。"""
    return DEFAULT_MUST_PASS.get(layer, False)


def normalize_verify_plan(raw: Any) -> dict[str, Any] | None:
    """归一化判据（与中枢同名函数规则一致）；无有效检查时返回 None。"""
    if not isinstance(raw, dict):
        return None
    entries = raw.get("checks")
    if not isinstance(entries, list):
        return None
    checks: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        layer = str(item.get("layer") or "").strip().upper()
        if layer not in VERIFY_LAYERS:
            continue
        cmd = str(item.get("cmd") or "").strip()
        if not cmd or len(cmd) > MAX_CMD_CHARS:
            continue
        name = str(item.get("name") or "").strip()[:MAX_NAME_CHARS] or f"{layer} 检查"
        cwd = item.get("cwd")
        cwd = str(cwd).strip() if isinstance(cwd, str) and cwd.strip() else "."
        must_pass = item.get("must_pass")
        must_pass = bool(must_pass) if isinstance(must_pass, bool) else default_must_pass(layer)
        timeout_raw = item.get("timeout_s")
        timeout_s = LAYER_TIMEOUTS[layer]
        if isinstance(timeout_raw, (int, float)) and timeout_raw > 0:
            timeout_s = min(int(timeout_raw), MAX_CHECK_TIMEOUT_S)
        checks.append(
            {
                "layer": layer,
                "name": name,
                "cmd": cmd,
                "cwd": cwd,
                "timeout_s": timeout_s,
                "must_pass": must_pass,
            }
        )
        if len(checks) >= MAX_CHECKS:
            break
    if not checks:
        return None
    checks.sort(key=lambda item: VERIFY_LAYERS.index(item["layer"]))
    return {"checks": checks}


def load_plan(workspace_dir: Path, doc_dir: str | None) -> tuple[dict[str, Any] | None, str]:
    """按优先级加载判据。

    Returns:
        ``(plan, source)``；source 取值 ``verify_json`` / ``project_config`` /
        ``derived`` / ``none``。
    """
    if doc_dir:
        path = workspace_dir / doc_dir / "verify.json"
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning(
                    "verify_json_unreadable",
                    extra={"event": "verify_json_unreadable", "path": str(path)},
                )
            else:
                plan = normalize_verify_plan(raw)
                if plan is not None:
                    return plan, "verify_json"
    plan = from_project_config(workspace_dir)
    if plan is not None:
        return plan, "project_config"
    plan = derive_checks(workspace_dir)
    if plan is not None:
        return plan, "derived"
    return None, "none"


def from_project_config(workspace_dir: Path) -> dict[str, Any] | None:
    """从项目自带配置推导判据（package.json scripts / pyproject / Makefile）。"""
    checks: list[dict[str, Any]] = []

    package_json = workspace_dir / "package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        for script, layer, name in (
            ("typecheck", "L0", "TypeScript 类型检查"),
            ("build", "L0", "前端构建"),
            ("lint", "L1", "代码规范检查"),
            ("test", "L1", "前端单元测试"),
        ):
            if isinstance(scripts, dict) and scripts.get(script):
                checks.append(
                    {
                        "layer": layer,
                        "name": f"{name}（package.json:{script}）",
                        "cmd": f"npm run {script}",
                        "cwd": ".",
                        "timeout_s": LAYER_TIMEOUTS[layer],
                        "must_pass": default_must_pass(layer),
                    }
                )

    if (workspace_dir / "pyproject.toml").is_file() or (workspace_dir / "pytest.ini").is_file():
        checks.append(
            {
                "layer": "L1",
                "name": "Python 单元测试（pyproject/pytest.ini）",
                "cmd": "python -m pytest -q",
                "cwd": ".",
                "timeout_s": LAYER_TIMEOUTS["L1"],
                "must_pass": True,
            }
        )

    makefile = workspace_dir / "Makefile"
    if makefile.is_file():
        try:
            content = makefile.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        if "\ntest:" in f"\n{content}" or content.startswith("test:"):
            checks.append(
                {
                    "layer": "L1",
                    "name": "Makefile test 目标",
                    "cmd": "make test",
                    "cwd": ".",
                    "timeout_s": LAYER_TIMEOUTS["L1"],
                    "must_pass": True,
                }
            )

    return normalize_verify_plan({"checks": checks})


def derive_checks(workspace_dir: Path) -> dict[str, Any] | None:
    """按文件类型推导最小检查（兜底一层；仍无 → None = 无法验证）。"""
    checks: list[dict[str, Any]] = []
    try:
        has_python = any(
            path.suffix == ".py"
            for path in workspace_dir.rglob("*.py")
            if ".tilacode" not in path.parts
        )
    except OSError:
        has_python = False
    if has_python:
        checks.append(
            {
                "layer": "L0",
                "name": "Python 语法编译检查（推导）",
                "cmd": "python -m compileall -q .",
                "cwd": ".",
                "timeout_s": LAYER_TIMEOUTS["L0"],
                "must_pass": True,
            }
        )
    return normalize_verify_plan({"checks": checks})


@dataclass
class CheckResult:
    """单条检查结果。"""

    layer: str
    name: str
    cmd: str
    cwd: str
    must_pass: bool
    status: str  # passed | failed | timeout | error
    exit_code: int | None = None
    duration_ms: int = 0
    output_tail: str = ""

    @property
    def passed(self) -> bool:
        """是否通过（仅 ``passed`` 算通过；超时/异常一律按未通过计）。"""
        return self.status == "passed"


@dataclass
class VerifyReport:
    """分层验收报告（完成闸的输入）。"""

    status: str  # passed | failed | unverifiable
    source: str
    checks: list[CheckResult] = field(default_factory=list)
    """附加说明（判据被篡改等），追加进 unverifiable 摘要供上报与回灌。"""
    note: str = ""

    @property
    def must_pass_failures(self) -> list[CheckResult]:
        """未通过的必过检查（完成闸据此拒绝）。"""
        return [item for item in self.checks if item.must_pass and not item.passed]

    def summary(self) -> str:
        """人类可读摘要（回灌自修提示与上报中枢）。"""
        if self.status == "unverifiable":
            base = "未找到任何可执行的验收判据（无法验证）"
            return f"{base}\n{self.note}" if self.note else base
        lines = []
        for item in self.checks:
            mark = "✅" if item.passed else "❌"
            flag = "必过" if item.must_pass else "参考"
            lines.append(f"{mark} [{item.layer}/{flag}] {item.name}：`{item.cmd}` → {item.status}")
        return "\n".join(lines)

    def to_payload(self) -> dict[str, Any]:
        """转为上报/落库用的 JSON 结构。"""
        return {
            "status": self.status,
            "source": self.source,
            "must_pass_failures": [
                {"layer": item.layer, "name": item.name, "cmd": item.cmd, "status": item.status}
                for item in self.must_pass_failures
            ],
            "checks": [
                {
                    "layer": item.layer,
                    "name": item.name,
                    "cmd": item.cmd,
                    "must_pass": item.must_pass,
                    "status": item.status,
                    "exit_code": item.exit_code,
                    "duration_ms": item.duration_ms,
                    "output_tail": item.output_tail[-OUTPUT_TAIL_CHARS:],
                }
                for item in self.checks
            ],
        }


async def run_plan(workspace_dir: Path, plan: dict[str, Any] | None, source: str) -> VerifyReport:
    """执行判据计划，返回报告（无判据 → ``unverifiable``）。"""
    if plan is None:
        return VerifyReport(status="unverifiable", source=source)
    results: list[CheckResult] = []
    for item in plan.get("checks") or []:
        results.append(await _run_check(workspace_dir, item))
    failures = [item for item in results if item.must_pass and not item.passed]
    return VerifyReport(
        status="failed" if failures else "passed",
        source=source,
        checks=results,
    )


async def _run_check(workspace_dir: Path, item: dict[str, Any]) -> CheckResult:
    """执行单条检查（超时按未通过计，输出尾部截断保留）。"""
    cwd = workspace_dir / str(item.get("cwd") or ".")
    if not cwd.is_dir():
        cwd = workspace_dir
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            str(item["cmd"]),
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        logger.warning(
            "verify_check_spawn_failed",
            extra={"event": "verify_check_spawn_failed", "cmd": item["cmd"], "error": str(exc)},
        )
        return CheckResult(
            layer=item["layer"],
            name=item["name"],
            cmd=item["cmd"],
            cwd=str(cwd),
            must_pass=bool(item["must_pass"]),
            status="error",
            output_tail=f"无法启动命令：{exc}",
        )

    timeout_s = int(item.get("timeout_s") or LAYER_TIMEOUTS[item["layer"]])
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        status = "passed" if proc.returncode == 0 else "failed"
        exit_code = proc.returncode
    except asyncio.TimeoutError:
        with contextlib.suppress(OSError, ProcessLookupError):
            proc.kill()
        stdout = b""
        status = "timeout"
        exit_code = None
    duration_ms = int((time.monotonic() - started) * 1000)
    tail = (stdout or b"").decode("utf-8", errors="replace")[-OUTPUT_TAIL_CHARS:]
    logger.info(
        "verify_check_done",
        extra={
            "event": "verify_check_done",
            "layer": item["layer"],
            "cmd": item["cmd"],
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        },
    )
    return CheckResult(
        layer=item["layer"],
        name=item["name"],
        cmd=item["cmd"],
        cwd=str(cwd),
        must_pass=bool(item["must_pass"]),
        status=status,
        exit_code=exit_code,
        duration_ms=duration_ms,
        output_tail=tail,
    )
