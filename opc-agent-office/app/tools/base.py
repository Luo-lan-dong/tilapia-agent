"""办公工具基类与注册表（ADR-062）。

工具统一以 OpenAI function calling 的 JSON Schema 描述，供 LLM 工具循环
组装 tools 列表；``execute`` 为同步阻塞实现，调用方用 ``asyncio.to_thread``
调度。工具入参来自 LLM 生成的 JSON，必须视为不可信输入做校验。
"""

import logging
import unicodedata
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: LLM 输出中文字体的标准名（docx/pptx 渲染统一使用；
#: 字体名随文件存储，打开方按本机字体解析——微软雅黑 Windows/WPS 通用）
CJK_FONT_NAME = "微软雅黑"


def clean_llm_text(text: str) -> str:
    """清洗 LLM 输出文本：NFKC 规范化消除兼容区/异体字符。

    兼容区字符（U+F900–FAFF）归一为基本区汉字；常规中文不受影响。
    """
    if not text:
        return text
    return unicodedata.normalize("NFKC", text)


def count_unrenderable_chars(text: str) -> int:
    """统计无法渲染的字符数（私用区 U+E000–F8FF 等）。

    LLM（DeepSeek 等）偶发把常用汉字输出为私用区码位（模型词表缺陷），
    任何标准字体均无字形，Word/WPS 渲染为「□」。此类字符无法程序化映射，
    只能检测后反馈 LLM 重新生成。
    """
    return sum(1 for ch in (text or "") if 0xE000 <= ord(ch) <= 0xF8FF)


class ToolError(Exception):
    """工具执行失败（消息将回传给 LLM 感知并自行调整）。"""


class OfficeTool(ABC):
    """办公工具基类。"""

    #: OpenAI function calling 工具名（LLM 可见）
    name: str = ""
    #: 功能描述（LLM 可见）
    description: str = ""
    #: 参数 JSON Schema（LLM 可见）
    parameters: dict[str, Any] = {}

    def __init__(self, work_dir: Path) -> None:
        """绑定任务工作目录；产物与中间文件均落在此目录内。"""
        self.work_dir = work_dir

    def to_openai_schema(self) -> dict[str, Any]:
        """转为 OpenAI chat.completions tools 条目。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    def execute(self, args: dict[str, Any]) -> str:
        """执行工具并返回给 LLM 的文本结果；失败抛 ``ToolError``。"""

    def _resolve_output(self, filename: str) -> Path:
        """解析产物输出路径：仅允许工作目录内的相对路径（防越界写入）。"""
        if not filename or not filename.strip():
            raise ToolError("filename 不能为空")
        clean = filename.strip().lstrip("/")
        if any(part in ("", ".", "..") for part in Path(clean).parts):
            raise ToolError(f"非法文件名: {filename}")
        target = self.work_dir / clean
        if not target.resolve().is_relative_to(self.work_dir.resolve()):
            raise ToolError(f"文件名越出工作目录: {filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def derive_edit_output_name(self, input_name: str | Path, suffix: str) -> str:
        """按输入路径派生另存名：``数据/表.xlsx`` → ``数据/表-edited.xlsx``。

        编辑类工具（edit_docx / edit_xlsx）默认另存策略：绝不原地覆盖原素材。
        """
        if isinstance(input_name, Path):
            try:
                clean = input_name.resolve().relative_to(self.work_dir.resolve()).as_posix()
            except ValueError:  # pragma: no cover - 越界输入已由读取侧拦截
                clean = input_name.name
        else:
            clean = (input_name or "").strip().replace("\\", "/").lstrip("/")
        parent, _, base = clean.rpartition("/")
        stem = base[: -len(suffix)] if base.lower().endswith(suffix.lower()) else Path(base).stem
        derived = f"{stem}-edited{suffix}"
        return f"{parent}/{derived}" if parent else derived

    def resolve_edit_output(
        self,
        *,
        input_path: Path,
        output_filename: Any,
        suffix: str,
    ) -> Path:
        """解析「读入 → 改 → 另存」的输出路径，并拒绝覆盖输入原件。

        - ``output_filename`` 缺省时按 ``derive_edit_output_name`` 自动派生；
        - 扩展名不符时补全 ``suffix``；越界由 ``_resolve_output`` 拦截；
        - 输出与输入解析后为同一文件 → ``ToolError``（保护原件，ADR-065 M2）。
        """
        if output_filename is None:
            raw = self.derive_edit_output_name(input_path, suffix)
        elif not isinstance(output_filename, str) or not output_filename.strip():
            raise ToolError("参数 output_filename 必须为非空字符串")
        else:
            raw = output_filename.strip()
        if not raw.lower().endswith(suffix.lower()):
            raw = f"{raw}{suffix}"
        target = self._resolve_output(raw)
        if target.resolve() == input_path.resolve():
            raise ToolError(
                f"输出文件名与输入文件相同（{target.name}），已拒绝以防覆盖原素材；"
                f"请改用其他文件名，如：{self.derive_edit_output_name(input_path, suffix)}"
            )
        return target


class ToolRegistry:
    """工具注册表：按名取用与 schema 列表导出。"""

    def __init__(self) -> None:
        self._tools: dict[str, OfficeTool] = {}

    def register(self, tool: OfficeTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> OfficeTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"未知工具: {name}")
        return tool

    def openai_schemas(self) -> list[dict[str, Any]]:
        """导出全部工具的 OpenAI tools 条目。"""
        return [tool.to_openai_schema() for tool in self._tools.values()]


def require_str(args: dict[str, Any], key: str, *, max_len: int = 8000) -> str:
    """从 LLM 工具入参中取必填字符串（不可信输入边界校验）。"""
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"参数 {key} 必须为非空字符串")
    if len(value) > max_len:
        raise ToolError(f"参数 {key} 超长（>{max_len} 字符）")
    return value.strip()
