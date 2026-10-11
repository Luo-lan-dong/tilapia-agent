"""任务工作目录管理。

按 task_id 隔离工作目录，负责创建目录结构、写入 tilacode 配置与
任务规格文档三件套（spec/tasks/checklist），供 tilacode 会话消费。

安全与并发约束（ADR-055）：
- ``prepare`` 对显式产物路径与兜底 ``task_id`` 做**工作区包含性校验**，
  拒绝 ``/etc`` 这类绝对路径与 ``../..`` 越界相对路径（P0-4）；
- 所有 tilacode 配置写入走**临时文件 + 原子替换**，读取时 ``deepcopy``
  模板，避免 check-then-write 竞态与全局常量污染（P2-12）；
- 执行模型凭据（apiKey）写入**用户级**配置目录（``~/.config/tilacode``），
  不落任务工作目录；工作目录内配置只保留权限/默认 agent/instructions（P0-4）。
"""

import copy
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

from app.executor import permission_policy

logger = logging.getLogger(__name__)

# tilacode 配置模板：不写 model——实际执行模型由任务桥接在 session 创建时
# 显式指定（ADR-029，优先级高于本文件），写死默认值会误导排查（用户看到
# 免费模型却实际用中枢配置执行）。
# instructions（ADR-048）：每次会话自动把元规则全文注入上下文——这是"每次
# 开发前必加载 .tilacode/rules/"的硬机制（AGENTS.md 指引作为兜底层）。
TILACODE_CONFIG_TEMPLATE: dict = {
    "$schema": "https://opencode.ai/config.json",
    # 权限策略（ADR-061 M2a，2026-09-28）：
    # ⚠️ 这里**不得**再写 "permission": {"*": "allow"}。该规则经 V1→V2 迁移后会**追加到
    # tilacode 内置 agent 权限数组的末尾**，而求值是 findLast（最后匹配者胜），会把内置
    # 全部护栏覆盖成 allow——工作区外访问、.env 读取、tool-output/tmp/plans 白名单全部失效。
    # 无人值守（没有人能应答询问）下改用 permission_mode="unattended"：tilacode 会把
    # "本该询问人"的请求一律按拒绝处理，工作区内不受影响，且不会挂起等待。
    "permission_mode": "unattended",
    "default_agent": "build",
    "instructions": [
        ".tilacode/rules/coding-standards.md",
        ".tilacode/rules/decision-documents.md",
        ".tilacode/rules/environment-check.md",
        # 前端验证（ADR-061 M7）：节点本地生成，告诉 Agent 何时用浏览器探针
        ".tilacode/rules/frontend-verification.md",
    ],
}

#: 前端验证规则相对路径（节点本地生成；与模板 instructions 保持一致）
_FRONTEND_RULE_REL = ".tilacode/rules/frontend-verification.md"
#: 探针脚本在工作目录内的相对路径（随任务落盘，供子 Agent 直接用 node 运行）
_PROBE_REL = ".tilacode/tools/frontend_probe.mjs"

#: 前端验证规则正文（ADR-061 M7）
#:
#: 与 hub 下发的元规则同格式：说明「什么时候必须用探针、怎么读输出、失败怎么算」。
#: 文本优先（默认只看结构化 JSON），截图仅在需要视觉记录时加 `--shots`。
FRONTEND_VERIFICATION_RULE = """# 前端验证规则（自动生成，ADR-061 M7）

涉及**任何可见界面**的任务（网页、前端页面、桌面壳内嵌页），改完必须做一次浏览器实测，
不能只看 `build` 是否通过——构建成功不等于页面能打开、能点、控制台没报错。

## 怎么测

先把服务跑起来（`npm run dev` / `npm run preview` / 后端自带的静态服务均可），然后：

```bash
# 文本反馈（默认，最快）：结构化 JSON —— 标题/结构快照/可见文本摘要/控制台错误/失败请求
node .tilacode/tools/frontend_probe.mjs --url http://localhost:5173

# 需要视觉记录（交付物里要放截图）时：
node .tilacode/tools/frontend_probe.mjs --url http://localhost:5173 --shots out/shots --full-page
```

## 怎么读结果与怎么算通过

- **退出码即判据**：`0` 通过；`1` 有控制台错误或失败请求（按未通过处理）；
  `2` 导航失败（服务没起来或地址不对）；`3` 浏览器依赖缺失（请联系节点管理员）。
- `console_errors` / `page_errors` / `failed_requests` 非空 → **必须修**；修完再跑一次，直到为空。
- `structure`（标题层级与可交互元素可访问名）用来确认页面**结构完整**：
  关键按钮/输入框存在且**有可读名称**；缺名称也要修（无障碍要求）。
- `text_excerpt` 用来确认文案正确、没有占位符残留（如 `TODO`、`undefined`、`{{...}}`）。
- 非前端任务可忽略本规则。
"""


#: 规格文档三件套：TaskSpecification 属性名 -> 版本目录内相对文件名
#: （ADR-037 修订：版本目录位于工作目录根部 specs/，与中枢下发模块三件套
#: 的目录约定一致，docs/ 保留给元规则驱动的决策文档体系）
_SPEC_DOC_PATHS: dict[str, str] = {
    "spec": "spec.md",
    "tasks": "tasks.md",
    "checklist": "checklist.md",
    # 验收判据（ADR-061 M4）：内容为 dict，落盘时序列化为 JSON
    "verify": "verify.json",
}

#: 版本目录标题片段最大字符数（ADR-037，与中枢 task_service 同规则）
_DIR_TITLE_MAX_CHARS = 20
#: 文件系统/跨平台非法目录字符（ADR-037，与中枢同规则）
_ILLEGAL_DIR_CHARS = set('/\\:*?"<>|')

#: 用户级配置文件名（tilacode 会依次尝试 .json / .jsonc）
_USER_CONFIG_FILENAME = "tilacode.jsonc"
#: hub 托管的 provider 命名空间前缀（避免与其他用途 provider 冲突）
_PROVIDER_NAMESPACE_PREFIX = "hub"
#: 历史版本写入的托管 provider 记录键（tilacode 严格 schema 下为**毒键**：
#: 顶层出现未知键会导致整个配置文件不参与 merge）。仅用于写入时自愈剔除。
_MANAGED_KEY = "_hub_managed_providers"
#: provider/模型名净化：仅保留字母数字与 _ - .（tilacode provider id 约定）
_NAME_SANITIZE_RE = re.compile(r"[^0-9A-Za-z_.-]")

#: tilacode 配置允许的顶层键（V1 ∪ V2 的并集，见源码）：
#: - V2：packages/core/src/config.ts 的 ``Info``
#: - V1（会被迁移）：packages/core/src/v1/config/config.ts 与 migrate.ts 的 ``keys``
#: 顶层出现此集合之外的键时 tilacode 判定整个配置非法（Unrecognized keys），
#: 因此启动自检按本集合清理（事故见 ``sanitize_user_config`` docstring）。
_KNOWN_CONFIG_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        # V2（Config.Info）
        "$schema",
        "shell",
        "model",
        "default_agent",
        "autoupdate",
        "share",
        "enterprise",
        "username",
        "permissions",
        "agents",
        "snapshots",
        "watcher",
        "formatter",
        "lsp",
        "attachments",
        "tool_output",
        "mcp",
        "compaction",
        "skills",
        "commands",
        "instructions",
        "references",
        "plugins",
        "experimental",
        "providers",
        # V1（ConfigV1.Info / migrate.keys）
        "logLevel",
        "server",
        "command",
        "reference",
        "snapshot",
        "plugin",
        "autoshare",
        "disabled_providers",
        "enabled_providers",
        "small_model",
        "mode",
        "agent",
        "provider",
        "permission",
        "tools",
        "attachment",
        "layout",
    }
)

#: V1 专属键（tilacode ``ConfigMigrateV1.keys``）：出现任一即按 V1 解析整个文件。
#: 用于判定「复数 ``providers`` 在本文件中是否属非法键」——V1 只有单数 ``provider``。
_V1_ONLY_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "logLevel",
        "server",
        "command",
        "reference",
        "snapshot",
        "plugin",
        "autoshare",
        "disabled_providers",
        "enabled_providers",
        "small_model",
        "mode",
        "agent",
        "provider",
        "permission",
        "tools",
        "attachment",
        "layout",
    }
)


def _looks_like_v1_config(data: dict) -> bool:
    """按 tilacode ``isV1`` 同规则判断配置是否按 V1 解析（含任一 V1 专属键）。"""
    return any(key in data for key in _V1_ONLY_TOP_LEVEL_KEYS)


class WorkspacePathError(Exception):
    """工作目录路径非法（越界、为空或含非法片段）时抛出。"""

    def __init__(self, reason: str, *, path: str | None = None) -> None:
        """初始化异常。

        Args:
            reason: 人类可读的拒绝原因。
            path: 触发拒绝的原始路径（可为 None）。
        """
        self.path = path
        super().__init__(f"工作目录路径非法：{reason}" + (f"（{path}）" if path else ""))


def sanitize_provider_name(name: str) -> str:
    """净化 provider/模型标识为 tilacode 可接受的安全片段。

    Args:
        name: 原始名称（来自中枢下发或 provider 配置）。

    Returns:
        仅含 ``[0-9A-Za-z_.-]`` 的名称；全部非法时回退 ``provider``。
    """
    cleaned = _NAME_SANITIZE_RE.sub("-", name).strip("-.")
    return cleaned or "provider"


def provider_namespace(provider: str, task_id: str) -> str:
    """构造 hub 托管的 provider 命名空间（ADR-055 D3）。

    形如 ``hub-deepseek-1a2b3c4d``：同节点不同任务使用不同 provider 定义时
    互不覆盖；前缀便于 TTL 清理时识别归属。

    Args:
        provider: 中枢下发的 provider 名（如 ``aliyun``）。
        task_id: 触发该凭据写入的任务 ID。

    Returns:
        净化后的命名空间名。
    """
    short_id = _NAME_SANITIZE_RE.sub("", task_id)[:8] or "task"
    return f"{_PROVIDER_NAMESPACE_PREFIX}-{sanitize_provider_name(provider)}-{short_id}"


def _resolve_probe_source() -> Path | None:
    """定位前端探针源文件；找不到返回 None（=本次构建未随包分发浏览器能力）。

    优先读环境变量 ``TILAPIA_TOOLS_DIR``：Nuitka ``--onefile`` 产物的 ``__file__``
    指向临时解包目录，**不能**用来推断安装目录；systemd 里已注入该变量
    （``/opt/opc-agent-code/tools``）。回退到仓库相对路径，便于源码模式与单测。
    """
    candidates: list[Path] = []
    tools_dir = os.environ.get("TILAPIA_TOOLS_DIR")
    if tools_dir:
        candidates.append(Path(tools_dir) / "frontend_probe.mjs")
    candidates.append(Path(__file__).resolve().parents[2] / "tools" / "frontend_probe.mjs")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _atomic_write_text(path: Path, content: str) -> None:
    """以临时文件 + 原子替换写入文本（ADR-055 P2-12）。

    同目录临时文件 + ``os.replace`` 保证读者永远看不到半截内容；写入失败
    清理临时文件后原样抛出。

    Args:
        path: 目标文件路径。
        content: 待写入文本。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fp:
            fp.write(content)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _read_json_object(path: Path, *, fallback: dict | None = None) -> dict:
    """读取 JSON/JSONC 风格配置文件为字典（容错）。

    Args:
        path: 配置文件路径。
        fallback: 读取或解析失败时的返回值（浅结构，调用方负责 deepcopy）。

    Returns:
        解析后的字典；文件不存在或非法时返回 fallback（缺省空字典）。
    """
    data, _ok = _read_json_object_checked(path, fallback=fallback)
    return data


def _read_json_object_checked(path: Path, *, fallback: dict | None = None) -> tuple[dict, bool]:
    """读取配置文件并告知内容是否可用（供调用方决定是否写回修复）。

    Args:
        path: 配置文件路径。
        fallback: 读取或解析失败时的返回值（浅结构，调用方负责 deepcopy）。

    Returns:
        ``(配置字典, 文件内容是否可用)``。文件不存在或解析失败时第二项为
        False——调用方据此把修复后的内容**写回**，避免坏文件长期留存导致
        配置一直不生效。
    """
    empty = dict(fallback) if fallback else {}
    if not path.exists():
        return empty, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("tilacode_config_parse_failed_rebuild", extra={"path": str(path)})
        return empty, False
    if not isinstance(data, dict):
        return empty, False
    return data, True


def _heal_workspace_config(config: dict) -> bool:
    """修复工作目录配置中的历史污染键（ADR-055 D3 修订，2026-09-27）。

    tilacode 对配置做**严格 schema 校验**：顶层出现任何 ``ConfigV1.Info``
    之外的键（如历史版本写入的 ``_hub_managed_providers``）会导致整个文件
    不参与 merge，表现就是「文件内容看着正确但 provider 加载不到」。此处
    主动剔除这些键，实现自愈。

    同时把复数 ``providers`` 归并为单数 ``provider``（真实 schema 是单数，
    复数形式会被严格校验拒绝或静默忽略）。

    Args:
        config: 配置字典（就地修改）。

    Returns:
        True 表示确实做了修复。
    """
    changed = False
    if config.pop(_MANAGED_KEY, None) is not None:
        changed = True
    # 权限策略迁移（ADR-061 M2a，2026-09-28）：历史模板写过 "permission": {"*": "allow"}。
    # 该规则经 V1→V2 迁移后会被**追加到 agent 权限数组的末尾**，而求值是 findLast（最后
    # 匹配者胜）→ tilacode 内置的全部权限护栏（工作区外访问、.env 读取、tool-output/tmp/plans
    # 白名单）都被覆盖成 allow。仅在**恰好等于旧模板默认值**时迁移，避免误动用户自定义规则；
    # 已有 permission_mode 时不覆盖（尊重显式配置）。
    if config.get("permission") == {"*": "allow"}:
        config.pop("permission", None)
        config.setdefault("permission_mode", "unattended")
        changed = True
    plural = config.pop("providers", None)
    if isinstance(plural, dict):
        merged = config.get("provider")
        merged = dict(merged) if isinstance(merged, dict) else {}
        for name, entry in plural.items():
            merged.setdefault(name, entry)
        config["provider"] = merged
        changed = True
    # 必载指令补全（ADR-061 M7）：历史工作区配置缺少新增的规则文件条目时补上，
    # 否则节点本地生成的 frontend-verification 规则不会被 tilacode 加载。
    # 只**追加缺失项**，不重排、不删除用户已有条目。
    required_instructions = list(TILACODE_CONFIG_TEMPLATE.get("instructions") or [])
    instructions = config.get("instructions")
    if not isinstance(instructions, list):
        if required_instructions:
            config["instructions"] = list(required_instructions)
            changed = True
        return changed
    missing = [item for item in required_instructions if item not in instructions]
    if missing:
        config["instructions"] = [*instructions, *missing]
        changed = True
    return changed


def _sanitize_title_for_dir(title: str) -> str:
    """将标题净化为可作目录名的片段（ADR-037，规则与中枢 task_service 一致）。

    非法字符与空白统一转 ``-``、压缩连续 ``-``、去除首尾 ``-.``、截断
    ``_DIR_TITLE_MAX_CHARS``；空串兜底 ``task``。
    """
    cleaned = "".join(
        "-" if ch.isspace() or ch in _ILLEGAL_DIR_CHARS else ch for ch in title.strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned.strip("-.")[:_DIR_TITLE_MAX_CHARS]) or "task"


def spec_docs_relative_dir(task_id: str, title: str | None) -> str:
    """返回本轮三件套版本目录相对路径（ADR-037 修订：根目录 specs/）。

    形如 ``specs/<净化标题>-<task_id前8>``，任务 ID 前缀锚定追溯。
    """
    short_id = task_id[:8] if task_id else "unknown"
    return f"specs/{_sanitize_title_for_dir(title or '')}-{short_id}"


class TaskSpecification(Protocol):
    """任务规格文档结构协议：三个 markdown 字段 + 结构化判据均可为 None。"""

    spec: str | None
    tasks: str | None
    checklist: str | None
    #: 验收判据（ADR-061 M4）：dict 落盘为 verify.json
    verify: dict | None


class RuleDocEntry(Protocol):
    """规则文档条目协议（ADR-048）：TaskRuleDocInfo 满足本协议。"""

    file_name: str
    apply_when: str | None
    target: str


def build_agents_md(entries: list[tuple[RuleDocEntry, bytes]]) -> str:
    """生成项目级 AGENTS.md（ADR-048 修订）。

    AGENTS.md 是 tilacode 自动注入的兜底层：指向 ``.tilacode/rules/``
    元规则（驱动者，经 tilacode.jsonc instructions 每次会话自动注入全文），
    并列出本次下发的规范正文清单（docs/standards/）。元规则承载完整
    加载流程，AGENTS.md 只做入口指引。
    """
    standards = [(doc, _) for doc, _ in entries if doc.target == "standard"]
    rows = "\n".join(
        f"| docs/standards/{doc.file_name}.md | {doc.apply_when or '（未标注，按需判断）'} |"
        for doc, _ in standards
    )
    standards_section = (
        f"| 文档（docs/standards/） | 何时必须加载 |\n| --- | --- |\n{rows}"
        if standards
        else "（本次未随任务下发规范正文，按 docs/standards/ 目录现有文件按需加载）"
    )
    return f"""# 项目开发规则（中枢派发系统自动生成，禁止手工改写本文件）

## 元规则加载（每次开工必须执行）

本项目采用元规则驱动体系（与中枢项目同一套结构）：

1. **先读元规则** `.tilacode/rules/`（已通过 tilacode instructions 自动注入，未注入时主动读取）：
   - `coding-standards.md`——规则①：写/改代码前先按其索引加载 `docs/standards/` 对应规范；
   - `decision-documents.md`——规则②：改功能前先查 `docs/adr/`、`docs/spec/`、`docs/INDEX.md`；
   - `environment-check.md`（如存在）——项目首次开发时执行环境检查与安装。
2. **规范正文**位于 `docs/standards/`，**动工哪一部分就读取哪一部分，不要全量阅读**。

## 本任务规范清单（docs/standards/）

{standards_section}

## 决策文档体系（规则②驱动）

- `docs/INDEX.md`：决策文档清单与更新记录（唯一权威登记源）；
- `docs/adr/`、`docs/spec/`：决策产物（重大偏离时按元规则②编写并登记）。
"""


class WorkspaceManager:
    """管理任务工作目录的创建、初始化与规格文档写入。"""

    def __init__(self, workspace_root: str, config_dir: str | Path | None = None) -> None:
        """初始化工作区管理器。

        Args:
            workspace_root: 任务工作目录根路径（所有任务目录必须位于其内）。
            config_dir: 用户级 tilacode 配置目录（ADR-055 D3，存放 provider
                凭据）；None 时使用平台缺省位置。
        """
        self._workspace_root = workspace_root
        self._config_dir = Path(config_dir) if config_dir else default_tilacode_config_dir()
        self._logger = logging.getLogger(__name__)

    @property
    def workspace_root(self) -> str:
        """返回工作目录根路径（来自 config.tilacode_workspace_root）。"""
        return self._workspace_root

    @property
    def config_dir(self) -> Path:
        """返回用户级 tilacode 配置目录（凭据落点）。"""
        return self._config_dir

    def sanitize_user_config(self) -> list[str]:
        """启动自检：剔除用户级配置中的未知顶层键（2026-09-27 事故防线）。

        **事故背景**：早期实现把 ``providers``（复数）与记账键
        ``_hub_managed_providers`` 写进了 ``~/.config/tilacode/tilacode.jsonc``。
        tilacode 对配置做严格 schema 校验，顶层出现未知键时**整个文件不参与
        merge**（并打印 ``Unrecognized keys``）；对用户表现为：
        tilacode 启动即退出（重启循环），或服务虽起但任务专属 provider 从未
        加载 → 模型解析失败 → **任务永远停在 10% 且无任何事件**。

        代码早已不再写用户级配置，但**磁盘上的历史残留会继续害人**，因此节点
        每次启动都主动清理一次：只保留 tilacode 已知顶层键，其余未知键剔除并
        记 WARNING（原值不打印，避免凭据进日志）。

        Returns:
            被剔除的键名列表（无残留时为空列表）。
        """
        config_path = self._config_dir / _USER_CONFIG_FILENAME
        if not config_path.exists():
            return []
        data, readable = _read_json_object_checked(config_path)
        if not readable:
            # 文件损坏：直接重置为空对象（tilacode 会按缺省配置启动）
            _atomic_write_text(config_path, "{}")
            self._logger.error(
                "tilacode_user_config_reset_unreadable",
                extra={"path": str(config_path)},
            )
            return ["<unreadable>"]

        unknown = [key for key in data if key not in _KNOWN_CONFIG_TOP_LEVEL_KEYS]
        # V1 判别与 tilacode 的 ``isV1`` 同规则（migrate.ts）：出现任一 V1 专属键
        # 即按 V1 解析。V1 只有单数 ``provider``，此时复数 ``providers`` 属未知键，
        # 会让**整个配置文件失效**（2026-09-27 事故根因），必须一并剔除。
        if _looks_like_v1_config(data) and "providers" in data:
            unknown.append("providers")
        if not unknown:
            return []
        for key in unknown:
            data.pop(key, None)
        _atomic_write_text(config_path, json.dumps(data, ensure_ascii=False, indent=2))
        self._logger.error(
            "tilacode_user_config_unknown_keys_removed",
            extra={"path": str(config_path), "keys": unknown},
        )
        return unknown

    def prepare(self, task_id: str, output_dir: str | None = None) -> Path:
        """创建/复用任务工作目录，并确保 ``.tilacode/`` 子目录存在。

        Args:
            task_id: 任务 ID（默认目录名的兜底键）。
            output_dir: 显式产物保存路径（ADR-028，hub 下发）；为空时使用
                ``{root}/{task_id}/``。相对路径相对工作区根解析，绝对路径
                也**必须位于工作区根之内**（ADR-055 P0-4 硬约束）。

        Returns:
            实际使用的工作目录绝对路径。

        Raises:
            WorkspacePathError: 路径越界、为空或含非法片段。
        """
        root = Path(self._workspace_root).resolve()
        if output_dir:
            candidate = Path(output_dir)
            resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        else:
            safe_task_id = self._safe_task_segment(task_id)
            resolved = (root / safe_task_id).resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise WorkspacePathError(
                "产物保存路径必须位于工作目录根之内", path=output_dir or task_id
            )
        if resolved == root:
            raise WorkspacePathError("产物保存路径不能是工作目录根本身", path=output_dir or task_id)
        resolved.mkdir(parents=True, exist_ok=True)
        (resolved / ".tilacode").mkdir(parents=True, exist_ok=True)
        self._logger.info(
            "workspace_prepared",
            extra={
                "task_id": task_id,
                "workspace_dir": str(resolved),
                "explicit_output_dir": bool(output_dir),
            },
        )
        return resolved

    @staticmethod
    def _safe_task_segment(task_id: str) -> str:
        """净化兜底 ``task_id`` 为单层目录名（拒绝 ``..`` 与分隔符）。"""
        cleaned = _NAME_SANITIZE_RE.sub("-", task_id).strip("-.")
        if not cleaned or cleaned in (".", ".."):
            raise WorkspacePathError("任务 ID 不能作为目录名", path=task_id)
        return cleaned

    def ensure_tilacode_config(
        self,
        workspace_dir: Path,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """确保工作目录 ``.tilacode/tilacode.jsonc`` 存在、未被污染，并应用权限策略。

        **绝不覆盖既有配置**——工作目录配置是任务专属 provider 定义（含
        apiKey）的**唯一有效载体**（tilacode 仅在启动时快照用户级配置，运行期
        不热重载；项目级配置随会话目录动态加载）。因此这里只做三件事：

        1. 文件不存在 → 写入模板（permission/default_agent/instructions）；
        2. 文件存在 → 仅剔除历史污染键（``_hub_managed_providers``）与把复数
           ``providers`` 归并为单数 ``provider``（严格 schema 校验要求），
           **保留 provider 段**；
        3. **权限策略（ADR-061 M2b）**：``policy`` 非 None 时覆盖写入
           ``permission_mode`` 与 allow 白名单（只追加 allow、清除一切形式的
           blanket allow）；``policy=None`` 表示中枢未下发 → 沿用本地已有配置
           （缓存语义，Q2）。本地也没有时即模板最严值（unattended + 空白名单）。

        历史教训（2026-09-27）：本方法曾调用 ``_strip_credentials`` 删除
        provider 段（ADR-055 P0-4 的「凭据不落工作区」策略），但该策略与
        tilacode 实际加载机制冲突——删除后模型解析失败，任务永远不执行；
        执行顺序上 ``ensure_tilacode_config`` 先于 ``_resolve_session_model``，
        因此**复用工作目录的第二轮任务必现失效**。凭据改为留在工作目录，
        靠产物排除（``file_scanner`` 整体排除 ``.tilacode/``）防外泄。

        Returns:
            当前**实际生效**的权限策略（回读自配置），供权限判定与审计使用。
        """
        config_path = workspace_dir / ".tilacode" / _USER_CONFIG_FILENAME
        if config_path.exists():
            existing, readable = _read_json_object_checked(
                config_path, fallback=copy.deepcopy(TILACODE_CONFIG_TEMPLATE)
            )
            healed = _heal_workspace_config(existing)
            if policy is not None:
                applied = permission_policy.apply_to_config(existing, policy)
                self._logger.info(
                    "tilacode_permission_policy_applied",
                    extra={
                        "path": str(config_path),
                        "mode": policy.get("mode"),
                        "allow_count": len(policy.get("allow") or []),
                        "changed": applied,
                    },
                )
                healed = healed or applied
            # 内容不可用（损坏/非对象）时必须写回，否则坏文件长期留存、
            # tilacode 侧配置始终不生效（任务因模型解析失败而不执行）
            if healed or not readable:
                _atomic_write_text(config_path, json.dumps(existing, ensure_ascii=False, indent=2))
                self._logger.warning(
                    "tilacode_config_healed",
                    extra={"path": str(config_path), "unreadable": not readable},
                )
            else:
                self._logger.info(
                    "tilacode_config_exists_skip",
                    extra={"path": str(config_path)},
                )
            return permission_policy.effective_policy_from_config(existing)
        template = copy.deepcopy(TILACODE_CONFIG_TEMPLATE)
        if policy is not None:
            permission_policy.apply_to_config(template, policy)
            self._logger.info(
                "tilacode_permission_policy_applied",
                extra={
                    "path": str(config_path),
                    "mode": policy.get("mode"),
                    "allow_count": len(policy.get("allow") or []),
                    "changed": True,
                },
            )
        _atomic_write_text(config_path, json.dumps(template, ensure_ascii=False, indent=2))
        self._logger.info("tilacode_config_written", extra={"path": str(config_path)})
        return permission_policy.effective_policy_from_config(template)

    def ensure_frontend_tooling(self, workspace_dir: Path) -> list[Path]:
        """落盘前端探针与前端验证规则（ADR-061 M7）。

        - ``.tilacode/tools/frontend_probe.mjs``：节点自带的浏览器探针（**文本优先**、
          截图可选）；与节点模板内容不一致时覆盖，保证升级后生效；
        - ``.tilacode/rules/frontend-verification.md``：告诉子 Agent 何时必须实测、
          怎么读输出、什么算通过（已列入配置 ``instructions``，会被 tilacode 加载）。

        两文件都在 ``.tilacode/`` 下，**产物扫描天然排除**，不会污染交付物。
        """
        written: list[Path] = []
        probe_src = _resolve_probe_source()
        if probe_src is None:
            # 本次构建未随包分发探针（WITHOUT_BROWSER=1 或 tools/ 未上传）：
            # **不写**前端验证规则——否则规则会让子 Agent 去跑一个不存在的脚本，
            # 报错信息还不如 M4 的「无法验证」清晰。
            self._logger.info(
                "frontend_tooling_skipped",
                extra={"reason": "probe_not_shipped"},
            )
            return written
        probe_dst = workspace_dir / _PROBE_REL
        content = probe_src.read_text(encoding="utf-8")
        if not probe_dst.is_file() or probe_dst.read_text(encoding="utf-8") != content:
            _atomic_write_text(probe_dst, content)
        written.append(probe_dst)
        rule_dst = workspace_dir / _FRONTEND_RULE_REL
        if (
            not rule_dst.is_file()
            or rule_dst.read_text(encoding="utf-8") != FRONTEND_VERIFICATION_RULE
        ):
            _atomic_write_text(rule_dst, FRONTEND_VERIFICATION_RULE)
        written.append(rule_dst)
        self._logger.info(
            "frontend_tooling_ready",
            extra={"files": [str(item) for item in written]},
        )
        return written

    def ensure_index_skeleton(self, workspace_dir: Path) -> Path | None:
        """初始化决策文档体系索引 ``docs/INDEX.md`` 骨架（ADR-048 修订）。"""
        return _ensure_index_skeleton(workspace_dir)

    def upsert_provider_config(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        namespace: str | None = None,
        workspace_dir: Path | None = None,
    ) -> str:
        """把自定义 OpenAI 兼容 provider 定义写入工作目录与用户级配置（ADR-055 D3 修订）。

        - **工作目录** ``{workspace_dir}/.tilacode/tilacode.jsonc``：tilacode
          server 仅在**启动时**快照用户级配置、运行期不热重载；而项目级配置
          随会话目录**动态加载**。任务专属 provider 必须写在此处才能被
          runTurn 解析（2026-09-27 排查：ModelUnavailableError 根因——provider
          只写用户级，运行中的 server 永远看不到）。与 materialize 写入的
          permission/instructions 段合并（不覆盖）。
        - **用户级** ``{config_dir}/tilacode.jsonc``：兼容留存（server 重启后
          可见），命名空间按任务隔离，TTL 清理机制不变。

        Args:
            provider: 中枢下发的 provider 名（用于生成缺省命名空间）。
            model: 模型 ID。
            base_url: OpenAI 兼容 base URL。
            api_key: 中枢下发的 API Key。
            namespace: 显式命名空间；缺省按 provider + 时间戳生成。
            workspace_dir: 任务工作目录；提供时同步写项目级配置。

        Returns:
            实际使用的 provider 命名空间（供 session 创建引用）。
        """
        name = namespace or provider_namespace(provider, str(int(time.time())))
        provider_entry = {
            "npm": "@ai-sdk/openai-compatible",
            "name": sanitize_provider_name(provider),
            "options": {"baseURL": base_url, "apiKey": api_key},
            "models": {model: {"name": model}},
        }

        if workspace_dir is not None:
            ws_config_path = workspace_dir / ".tilacode" / _USER_CONFIG_FILENAME
            ws_existing = _read_json_object(
                ws_config_path, fallback=copy.deepcopy(TILACODE_CONFIG_TEMPLATE)
            )
            # 顶层键必须是单数 ``provider``（V1 ConfigProviderV1.Info；V2 的
            # ``providers`` 由 ConfigMigrateV1.migrate 从它迁移生成，V2 catalog
            # 插件（core/config/plugin/provider.ts）只读迁移后的 ``providers``）。
            # ⚠️ 复数 ``providers`` 在**全局与工作区两处都会被严格校验拒绝**
            # （parse.ts topLevelExtraKeys 抛「Unrecognized key: providers」，
            # 整个文件作废）——不是「项目级静默忽略」。2026-09-28 编译 Windows
            # 二进制实测确认，见 ADR-055「tilacode 配置 schema 契约」。
            # 2026-09-27 ModelUnavailableError 的最终根因即此键名错误。
            #
            # ⚠️ 连带红线：``isV1()`` 只要文件里出现**任一 V1 键**就把整个文件按 V1
            # 解析，因此本文件必须**全 V1 键、零 V2 键**。以后往工作区配置加
            # providers/permissions/agents/plugins/commands/references/compaction
            # 等 V2 专有键 → 立刻触发严格校验、文件作废、任务不执行。
            ws_providers = ws_existing.get("provider")
            ws_providers = ws_providers if isinstance(ws_providers, dict) else {}
            ws_providers[name] = provider_entry
            ws_existing["provider"] = ws_providers
            # 顶层只允许 ConfigV1.Info 已知键（parse.ts topLevelExtraKeys：
            # 任一未知键即抛 unrecognized_keys，整个文件不参与 merge）。
            # 历史版本写入的 ``_hub_managed_providers`` 正是这种毒键——
            # 文件内容看着正确但 provider 加载不到；写入时主动剔除自愈。
            ws_existing.pop(_MANAGED_KEY, None)
            _atomic_write_text(
                ws_config_path, json.dumps(ws_existing, ensure_ascii=False, indent=2)
            )
            self._logger.info(
                "tilacode_provider_config_workspace_written",
                extra={"namespace": name, "path": str(ws_config_path)},
            )

        # 用户级配置写入已废弃（2026-09-27）：新版 tilacode 对用户级配置做
        # 严格 schema 校验，顶层 providers/_hub_managed_providers 键会导致
        # server 启动即退出（"Unrecognized keys"）——节点重启循环根因。
        # provider 定义只允许写入任务工作目录项目配置（tilacode 随会话
        # 动态加载且接受该键，探针实测通过）。
        self._logger.info(
            "tilacode_provider_config_upserted",
            extra={
                "provider": provider,
                "namespace": name,
                "model": model,
                "workspace": str(workspace_dir) if workspace_dir else None,
            },
        )
        return name

    def write_specification_docs(
        self,
        workspace_dir: Path,
        specification: TaskSpecification,
        doc_dir: str,
    ) -> list[Path]:
        """将规格文档三件套写入 ``workspace_dir/{doc_dir}/`` 版本目录（ADR-037）。"""
        return _write_specification_docs(workspace_dir, specification, doc_dir)

    def read_plan_documents(self, workspace_dir: Path) -> str | None:
        """读取 ``.tilacode/plans/*.md`` 全文（ADR-012 plan 审批流）。"""
        return _read_plan_documents(workspace_dir)

    def materialize_rule_docs(
        self,
        workspace_dir: Path,
        blobs: list[tuple[RuleDocEntry, bytes]],
    ) -> list[Path]:
        """按 target 落盘规则文档并生成 AGENTS.md（ADR-048）。"""
        config_path = workspace_dir / ".tilacode" / _USER_CONFIG_FILENAME
        return _materialize_rule_docs(workspace_dir, blobs, config_path)

    @staticmethod
    def rule_docs_materialized(
        workspace_dir: Path,
        blobs: list[tuple[RuleDocEntry, bytes]],
    ) -> bool:
        """判断本次下发的规则文档是否已按相同内容落盘（ADR-055 P1-7）。

        链上后续任务复用同一工作目录时，规则文档通常与上一轮完全一致；此时
        重写没有收益，却会改变 tilacode ``instructions`` 基线，让会话上下文
        产生无谓的增量注入。因此逐份比对**目标文件字节**，全部一致才返回 True
        （任一份缺失或内容不同都要重新落盘，保证版本更新能生效）。

        Args:
            workspace_dir: 任务工作目录。
            blobs: (文档信息, 文档字节) 列表。

        Returns:
            True 表示无需重写。
        """
        if not blobs:
            return False
        standards_dir = workspace_dir / "docs" / "standards"
        rules_dir = workspace_dir / ".tilacode" / "rules"
        for doc, content in blobs:
            safe_name = Path(f"{doc.file_name}.md").name
            target_dir = rules_dir if doc.target == "meta-rule" else standards_dir
            doc_path = target_dir / safe_name
            try:
                if not doc_path.is_file() or doc_path.read_bytes() != content:
                    return False
            except OSError:
                return False
        # AGENTS.md 是索引载体：存在即认为索引已生成（其内容由本次 blobs 决定，
        # 文档一致时索引也一致）
        return (workspace_dir / "AGENTS.md").is_file()


def default_tilacode_config_dir() -> Path:
    """返回 tilacode 用户级配置目录（与 tilacode ``Global.Path.config`` 对齐）。

    优先级：``TILACODE_CONFIG_DIR`` 环境变量 → ``XDG_CONFIG_HOME`` →
    ``APPDATA``（Windows）→ ``~/.config``；再拼接 ``tilacode``。

    Returns:
        用户级配置目录路径（不保证已存在，写入方负责创建）。
    """
    override = os.environ.get("TILACODE_CONFIG_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "tilacode"
    appdata = os.environ.get("APPDATA")
    if appdata and os.name == "nt":
        return Path(appdata) / "tilacode"
    return Path.home() / ".config" / "tilacode"


def _write_specification_docs(
    workspace_dir: Path,
    specification: TaskSpecification,
    doc_dir: str,
) -> list[Path]:
    """将规格文档三件套写入 ``workspace_dir/{doc_dir}/`` 版本目录（ADR-037）。

    每轮任务的文档按 ``specs/<净化标题>-<任务ID前8>/`` 独立保存，历史版本
    目录共存不覆盖；同任务重跑写同一目录（幂等覆盖）。值为 None 的字段
    跳过不写；返回实际写入的文件路径列表。
    """
    written: list[Path] = []
    for attr, filename in _SPEC_DOC_PATHS.items():
        content = getattr(specification, attr, None)
        if content is None:
            continue
        # 判据为结构化 dict（ADR-061 M4），落盘为 JSON；其余文档按文本写
        text = (
            json.dumps(content, ensure_ascii=False, indent=2)
            if isinstance(content, dict)
            else content
        )
        doc_path = workspace_dir / doc_dir / filename
        _atomic_write_text(doc_path, text)
        written.append(doc_path)
        logger.info("specification_doc_written", extra={"path": str(doc_path)})
    return written


def _read_plan_documents(workspace_dir: Path) -> str | None:
    """读取 ``workspace_dir/.tilacode/plans/`` 下全部 ``*.md`` 并拼接。

    按文件名升序排序，每个文件以 ``## {文件名}`` 标题 + 正文呈现，多文件
    之间以分隔线相连；目录不存在或无 md 文件时返回 None（plan 模式未产出
    方案时的回传语义见 ADR-012 plan 审批流）。单个文件读取失败仅告警跳过
    （ADR-055：不再让 OSError 逸出为 task_bridge_error）。
    """
    plans_dir = workspace_dir / ".tilacode" / "plans"
    if not plans_dir.is_dir():
        return None
    md_files = sorted(plans_dir.glob("*.md"), key=lambda path: path.name)
    sections: list[str] = []
    for path in md_files:
        try:
            sections.append(f"## {path.name}\n\n{path.read_text(encoding='utf-8')}")
        except OSError as exc:
            logger.warning(
                "plan_document_read_failed",
                extra={"path": str(path), "error": str(exc)},
            )
    if not sections:
        return None
    logger.info(
        "plan_documents_read",
        extra={"workspace_dir": str(workspace_dir), "count": len(sections)},
    )
    return "\n\n---\n\n".join(sections)


def _materialize_rule_docs(
    workspace_dir: Path,
    blobs: list[tuple[RuleDocEntry, bytes]],
    config_path: Path,
) -> list[Path]:
    """把派发的规则文档按 target 分目录落盘并生成 AGENTS.md（ADR-048 修订）。

    落盘位置（target 字段决定）：
    - ``standard`` → ``{workspace}/docs/standards/``：规范正文（元规则①指向此处按需加载）；
    - ``meta-rule`` → ``{workspace}/.tilacode/rules/``：元规则（驱动者），
      .tilacode 已在产物回传排除清单中，不污染交付物。

    同时生成根 ``AGENTS.md``、把实际落盘的元规则同步进 tilacode 配置的
    ``instructions``（每次会话自动注入全文——硬机制），并初始化
    ``docs/INDEX.md`` 骨架。所有写入走原子替换（ADR-055 P2-12）。

    Args:
        workspace_dir: 任务工作目录。
        blobs: (文档信息, 文档字节) 列表（hub_client 下载校验后传入）。
        config_path: 工作目录 tilacode 配置文件路径。

    Returns:
        实际写入的文件路径列表（规则文档 + AGENTS.md）。
    """
    if not blobs:
        return []
    standards_dir = workspace_dir / "docs" / "standards"
    rules_dir = workspace_dir / ".tilacode" / "rules"
    standards_dir.mkdir(parents=True, exist_ok=True)
    rules_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    instruction_files: list[str] = []
    for doc, content in blobs:
        # 双重防越界：取 basename（中枢已净化过文件名）
        safe_name = Path(f"{doc.file_name}.md").name
        if doc.target == "meta-rule":
            doc_path = rules_dir / safe_name
            instruction_files.append(f".tilacode/rules/{safe_name}")
        else:
            doc_path = standards_dir / safe_name
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_bytes(content)
        written.append(doc_path)
        logger.info(
            "rule_doc_materialized",
            extra={"path": str(doc_path), "target": doc.target, "size": len(content)},
        )
    agents_md_path = workspace_dir / "AGENTS.md"
    _atomic_write_text(agents_md_path, build_agents_md(blobs))
    written.append(agents_md_path)
    _upsert_instructions(config_path, instruction_files)
    index_path = _ensure_index_skeleton(workspace_dir)
    if index_path is not None:
        written.append(index_path)
    logger.info(
        "agents_md_written",
        extra={
            "path": str(agents_md_path),
            "doc_count": len(blobs),
            "instructions": instruction_files,
        },
    )
    return written


def _upsert_instructions(config_path: Path, files: list[str]) -> None:
    """把元规则文件同步进工作目录 tilacode 配置的 instructions（ADR-048）。

    合并写入（保留既有 instructions 项）；无元规则时不做任何修改；配置损坏
    时以模板重建（``deepcopy``，不污染全局模板）。写入原子替换（P2-12）。
    """
    if not files:
        return
    existing = _read_json_object(config_path, fallback=copy.deepcopy(TILACODE_CONFIG_TEMPLATE))
    current = existing.get("instructions")
    merged = list(dict.fromkeys([*(current if isinstance(current, list) else []), *files]))
    existing["instructions"] = merged
    _atomic_write_text(config_path, json.dumps(existing, ensure_ascii=False, indent=2))
    logger.info(
        "tilacode_instructions_upserted",
        extra={"path": str(config_path), "instructions": merged},
    )


def _ensure_index_skeleton(workspace_dir: Path) -> Path | None:
    """初始化决策文档体系索引 ``docs/INDEX.md`` 骨架（ADR-048 修订）。

    工程化任务首次落盘元规则时创建（已存在则跳过，AI 按元规则②自行维护）；
    骨架 = 决策文档清单 + 文档更新记录两个空表章节。

    Returns:
        创建时返回文件路径；已存在返回 None。
    """
    index_path = workspace_dir / "docs" / "INDEX.md"
    if index_path.exists():
        return None
    _atomic_write_text(
        index_path,
        """# 项目文档索引（唯一权威登记源）

本文件由决策文档规则（.tilacode/rules/decision-documents.md）驱动维护：
所有决策文档（ADR/Spec/Harness/Changes）在此登记，重大变更在更新记录中留痕。

## 一、决策文档清单

| 模块 | ADR | Spec / Harness / Changes | 日期 |
| --- | --- | --- | --- |
| （按需追加） | | | |

## 二、文档更新记录

| 日期 | 变更内容 | 变更人 |
| --- | --- | --- |
""",
    )
    logger.info("docs_index_skeleton_created", extra={"path": str(index_path)})
    return index_path
