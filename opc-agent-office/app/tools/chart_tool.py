"""render_chart 工具：matplotlib 渲染静态图表 PNG（ADR-065 M3）。

产出 PNG 供 Word/PPT 嵌入（LLM 也可用 ``edit_docx``/``write_pptx`` 之外的
场景：报告配图、网页素材等）；产物落在工作目录内。

两条硬约束：

1. **无 GUI 后端**：模块一加载即 ``matplotlib.use("Agg")``（早于任何绘图 API），
   否则无 DISPLAY 的 Linux 节点（opc-agent-office 部署形态）会报后端错误；
2. **中文字体兜底**：逐一探测常见中文字体，命中则显式写入 rcParams；
   全部缺失时回退默认字体并告警（中文会显示为方框，但不抛异常、不影响出图）。
"""

import logging
from pathlib import Path
from typing import Any

import matplotlib  # 顺序即语义：必须先 use("Agg") 再导入任何绘图 API

from app.tools.base import OfficeTool, ToolError, clean_llm_text, require_str

matplotlib.use("Agg")  # 无 GUI 后端：无 DISPLAY 的 Linux 节点同样可出图

logger = logging.getLogger(__name__)

#: 支持的图表类型（LLM 可见）
_CHART_TYPES = ("line", "bar", "pie")
#: 默认画布尺寸（英寸）与分辨率
_DEFAULT_WIDTH = 8.0
_DEFAULT_HEIGHT = 4.5
_DEFAULT_DPI = 150
_WIDTH_RANGE = (1.0, 40.0)
_HEIGHT_RANGE = (1.0, 40.0)
#: 中文字体候选（Windows / macOS / Linux 常见发行版字体名）
_CJK_FONT_CANDIDATES = (
    "Microsoft YaHei",  # Windows 微软雅黑
    "SimHei",  # Windows 黑体
    "PingFang SC",  # macOS
    "Noto Sans CJK SC",  # Linux（Noto CJK）
    "Noto Sans SC",
    "Source Han Sans SC",  # 思源黑体
    "WenQuanYi Zen Hei",  # Linux 文泉驿
    "WenQuanYi Micro Hei",
    "DejaVu Sans",  # 兜底：无中文字形（会告警）
)

_font_state: dict[str, Any] = {"resolved": None}


def _resolve_cjk_font() -> str | None:
    """探测本机可用的中文字体，命中即写入 rcParams；全部缺失回退并告警。

    结果缓存于 ``_font_state``（字体探测需读字体缓存，避免每张图重复扫描）。
    """
    if _font_state["resolved"] is not None:
        return _font_state["resolved"]
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    chosen = next((name for name in _CJK_FONT_CANDIDATES if name in available), None)
    if chosen is None:
        logger.warning(
            "chart_cjk_font_missing",
            extra={"event": "chart_cjk_font_missing", "candidates": list(_CJK_FONT_CANDIDATES)},
        )
    else:
        matplotlib.rcParams["font.sans-serif"] = [chosen, *_CJK_FONT_CANDIDATES]
    # 中文字体缺 U+2212 字形，负号会显示为方框 → 用 ASCII 连字符
    matplotlib.rcParams["axes.unicode_minus"] = False
    _font_state["resolved"] = chosen or ""
    return chosen


def _chart_type_of(kind: str) -> str:
    """校验图表类型（line/bar/pie）。"""
    if kind not in _CHART_TYPES:
        raise ToolError(f"type 不支持：{kind or '(空)'}（可选：{'/'.join(_CHART_TYPES)}）")
    return kind


def _categories_of(raw: Any) -> list[str]:
    """校验分类轴标签（非空字符串/数字数组）。"""
    if not isinstance(raw, list) or not raw:
        raise ToolError("categories 必须为非空数组")
    categories: list[str] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise ToolError("categories 元素必须为字符串或数字")
        categories.append(clean_llm_text(str(item)))
    return categories


def _series_of(raw: Any, point_count: int) -> list[tuple[str, list[float]]]:
    """校验系列（名称 + 数值数组，数值个数须与 categories 一致）。"""
    if not isinstance(raw, list) or not raw:
        raise ToolError("series 必须为非空数组")
    series: list[tuple[str, list[float]]] = []
    for index, item in enumerate(raw):
        label = f"series 第 {index + 1} 项"
        if not isinstance(item, dict):
            raise ToolError(f"{label} 必须为对象")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolError(f"{label} name 必须为非空字符串")
        values = item.get("values")
        if not isinstance(values, list) or not values:
            raise ToolError(f"{label} values 必须为非空数组")
        numbers: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ToolError(f"{label} values 元素必须为数字")
            numbers.append(float(value))
        if len(numbers) != point_count:
            raise ToolError(
                f"{label} values 个数（{len(numbers)}）与 categories 个数（{point_count}）不一致"
            )
        series.append((clean_llm_text(name.strip())[:100], numbers))
    return series


def _optional_number(
    args: dict[str, Any], key: str, default: float, bounds: tuple[float, float]
) -> float:
    """读取可选数值参数（缺省默认值，超范围 → 可读 ``ToolError``）。"""
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"参数 {key} 必须为数字")
    number = float(value)
    if not bounds[0] <= number <= bounds[1]:
        raise ToolError(f"参数 {key} 超出允许范围（{bounds[0]}-{bounds[1]}）：{number}")
    return number


def _optional_text(args: dict[str, Any], key: str, max_len: int) -> str:
    """读取可选字符串参数（缺省空串）；显式传入非字符串视为非法输入。"""
    value = args.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolError(f"参数 {key} 必须为字符串")
    return clean_llm_text(value.strip())[:max_len]


class RenderChartTool(OfficeTool):
    """用 matplotlib 渲染静态图表（PNG）。"""

    name = "render_chart"
    description = (
        "把数据渲染为图表图片（PNG），保存在工作目录内，可用于报告配图或嵌入文档。"
        "type 支持 line（折线）/ bar（柱状）/ pie（饼图，取第一个系列）；"
        "categories 为分类标签，series 为 [{name, values}]（values 个数须与 categories 一致）；"
        "可选 title/xlabel/ylabel/width/height（英寸，默认 8×4.5，dpi 150）。"
        "返回文本包含实际路径与像素尺寸。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "输出 PNG 相对路径，如：图表/销量.png"},
            "type": {"type": "string", "enum": list(_CHART_TYPES), "description": "图表类型"},
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "分类轴标签",
            },
            "series": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "系列名"},
                        "values": {"type": "array", "items": {"type": "number"}},
                    },
                    "required": ["name", "values"],
                },
                "description": "数据系列列表",
            },
            "title": {"type": "string", "description": "图表标题（可空）"},
            "xlabel": {"type": "string", "description": "X 轴标签（可空）"},
            "ylabel": {"type": "string", "description": "Y 轴标签（可空）"},
            "width": {"type": "number", "description": "画布宽度（英寸），默认 8"},
            "height": {"type": "number", "description": "画布高度（英寸），默认 4.5"},
        },
        "required": ["filename", "type", "categories", "series"],
    }

    def execute(self, args: dict[str, Any]) -> str:
        """渲染图表并落盘；失败抛 ``ToolError``（不抛未捕获异常）。"""
        # 先做全部入参校验，再解析输出路径（避免非法输入也创建空目录）
        kind = _chart_type_of(str(args.get("type") or "").strip().lower())
        categories = _categories_of(args.get("categories"))
        series = _series_of(args.get("series"), len(categories))
        title = _optional_text(args, "title", 200)
        xlabel = _optional_text(args, "xlabel", 100)
        ylabel = _optional_text(args, "ylabel", 100)
        width = _optional_number(args, "width", _DEFAULT_WIDTH, _WIDTH_RANGE)
        height = _optional_number(args, "height", _DEFAULT_HEIGHT, _HEIGHT_RANGE)

        filename = require_str(args, "filename", max_len=300)
        if not filename.lower().endswith(".png"):
            filename = f"{filename}.png"
        target = self._resolve_output(filename)

        font = _resolve_cjk_font()
        try:
            pixels = self._render(
                target,
                kind=kind,
                categories=categories,
                series=series,
                title=title,
                xlabel=xlabel,
                ylabel=ylabel,
                width=width,
                height=height,
            )
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 — 渲染异常转可读错误，不终止工具循环
            logger.warning(
                "tool_chart_render_failed",
                extra={"event": "tool_chart_render_failed", "file": str(target), "error": str(exc)},
            )
            raise ToolError(f"{self.name} 渲染失败：{type(exc).__name__}: {exc}") from exc

        logger.info(
            "tool_chart_rendered",
            extra={"event": "tool_chart_rendered", "file": str(target), "type": kind, "series": len(series)},
        )
        warning = "" if font else "（未找到中文字体，中文可能显示为方框，建议安装 Noto Sans CJK）"
        return (
            f"已生成图表 {target.name}（{kind}，{len(series)} 个系列 × {len(categories)} 个数据点，"
            f"{width:g}×{height:g} 英寸 @ {_DEFAULT_DPI}dpi = {pixels[0]}×{pixels[1]} 像素，"
            f"{target.stat().st_size} 字节）{warning}"
        )

    @staticmethod
    def _render(
        target: Path,
        *,
        kind: str,
        categories: list[str],
        series: list[tuple[str, list[float]]],
        title: str,
        xlabel: str,
        ylabel: str,
        width: float,
        height: float,
    ) -> tuple[int, int]:
        """执行 matplotlib 绘制并保存；返回像素尺寸 (宽, 高)。"""
        from matplotlib.figure import Figure

        figure = Figure(figsize=(width, height), dpi=_DEFAULT_DPI)
        axes = figure.add_subplot(1, 1, 1)
        try:
            if kind == "pie":
                # 饼图只表达一个系列：取第一个（多系列时其余无几何意义）
                name, values = series[0]
                axes.pie(
                    values,
                    labels=categories,
                    autopct="%1.1f%%",
                    startangle=90,
                    counterclock=False,
                )
                axes.axis("equal")
                if len(series) > 1:
                    logger.warning(
                        "chart_pie_extra_series_ignored",
                        extra={"event": "chart_pie_extra_series_ignored", "series": len(series)},
                    )
                if not title:
                    title = name
            elif kind == "bar":
                positions = range(len(categories))
                count = len(series)
                span = 0.8 / count
                for offset, (name, values) in enumerate(series):
                    shifted = [pos - 0.4 + span * (offset + 0.5) for pos in positions]
                    axes.bar(shifted, values, width=span, label=name)
                axes.set_xticks(list(positions))
                axes.set_xticklabels(categories)
                if count > 1:
                    axes.legend()
                axes.grid(axis="y", linestyle="--", alpha=0.4)
            else:  # line
                for name, values in series:
                    axes.plot(values, marker="o", linewidth=1.8, markersize=5, label=name)
                axes.set_xticks(range(len(categories)))
                axes.set_xticklabels(categories)
                if len(series) > 1:
                    axes.legend()
                axes.grid(axis="y", linestyle="--", alpha=0.4)
            if title:
                axes.set_title(title)
            if xlabel and kind != "pie":
                axes.set_xlabel(xlabel)
            if ylabel and kind != "pie":
                axes.set_ylabel(ylabel)
            figure.tight_layout()
            figure.savefig(str(target), dpi=_DEFAULT_DPI, facecolor="white")
        finally:
            figure.clear()
        return int(width * _DEFAULT_DPI), int(height * _DEFAULT_DPI)
