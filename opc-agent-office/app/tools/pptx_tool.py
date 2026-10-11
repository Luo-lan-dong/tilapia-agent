"""write_pptx 工具：slides 结构渲染为 PowerPoint（ADR-062）。

LLM 以 JSON slides 描述演示文稿（封面 + 内容页），本工具负责
python-pptx 渲染；产物落在工作目录内。

产出增强（ADR-065 M3）：``charts`` 用 python-pptx 原生图表部件插入**真实图表**
（可在 PowerPoint 中双击编辑数据）；``notes`` 按页写入演讲者备注。
两者均为可选参数，**不给参数时产物结构与 M2 完全一致**。
"""

import logging
from typing import Any

from app.tools.base import (
    CJK_FONT_NAME,
    OfficeTool,
    ToolError,
    clean_llm_text,
    count_unrenderable_chars,
    require_str,
)

logger = logging.getLogger(__name__)

#: render 图表类型枚举（LLM 可见）→ python-pptx 图表常量名
_CHART_TYPES = ("bar", "line", "pie")
#: 图表区域（英寸）：避开页标题，居中偏下
_CHART_LEFT_INCHES = 0.8
_CHART_TOP_INCHES = 1.7
_CHART_WIDTH_INCHES = 8.4
_CHART_HEIGHT_INCHES = 4.4


def _chart_type_of(kind: str) -> Any:
    """图表类型字符串 → ``XL_CHART_TYPE`` 常量（延迟导入 python-pptx）。"""
    from pptx.enum.chart import XL_CHART_TYPE

    mapping = {
        # bar 取柱状图（演示场景最常见的「条形对比」；python-pptx 无统一 bar 常量）
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "pie": XL_CHART_TYPE.PIE,
    }
    return mapping[kind]


def _set_cjk_font(run: Any) -> None:
    """为文本 run 设置中文字体（python-pptx 无 eastAsia API，操作 XML）。"""
    try:
        run.font.name = CJK_FONT_NAME
        rpr = run._r.get_or_add_rPr()  # noqa: SLF001
        ea = rpr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
        if ea is None:
            ea = rpr.makeelement(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}ea", {}
            )
            rpr.append(ea)
        ea.set("typeface", CJK_FONT_NAME)
    except Exception as exc:  # noqa: BLE001 — 字体设置失败不中断生成
        logger.warning("pptx_run_font_failed", extra={"error": str(exc)})


def _optional_list(args: dict[str, Any], key: str) -> list[Any]:
    """读取可选数组参数（缺省空数组）；显式传入非数组视为非法输入。"""
    value = args.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ToolError(f"参数 {key} 必须为数组")
    return value


class PptxTool(OfficeTool):
    """生成 .pptx 演示文稿。"""

    name = "write_pptx"
    description = (
        "生成 PowerPoint 演示文稿（.pptx）。slides 数组每个元素为一页"
        "（title + bullets 要点列表 + notes 备注）；首组数据自动渲染封面与目录由内容页承担，"
        "建议最后一页放总结要点。文件名以 .pptx 结尾，保存在工作目录内。"
        "可选增强（ADR-065 M3）：charts 按 slide 序号（1=封面）插入原生图表"
        "（type bar/line/pie + categories + series）；notes 按 slide 序号写入演讲者备注。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名，如：项目汇报.pptx"},
            "title": {"type": "string", "description": "封面主标题"},
            "subtitle": {"type": "string", "description": "封面副标题（可空）"},
            "slides": {
                "type": "array",
                "description": "内容页列表（不含封面）",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "页标题"},
                        "bullets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "要点列表",
                        },
                        "notes": {"type": "string", "description": "演讲者备注（可空）"},
                    },
                    "required": ["title", "bullets"],
                },
            },
            "charts": {
                "type": "array",
                "description": "可选：原生图表列表（不给则不插入任何图表）",
                "items": {
                    "type": "object",
                    "properties": {
                        "slide": {"type": "integer", "description": "页码（1=封面，2=第一页内容）"},
                        "type": {"type": "string", "enum": list(_CHART_TYPES)},
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
                                    "values": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "description": "数值（个数须与 categories 一致）",
                                    },
                                },
                                "required": ["name", "values"],
                            },
                        },
                        "title": {"type": "string", "description": "图表标题（可空）"},
                    },
                    "required": ["slide", "type", "categories", "series"],
                },
            },
            "notes": {
                "type": "array",
                "description": "可选：按页码写演讲者备注（与 slides[].notes 等价）",
                "items": {
                    "type": "object",
                    "properties": {
                        "slide": {"type": "integer", "description": "页码（1=封面）"},
                        "text": {"type": "string", "description": "备注文本"},
                    },
                    "required": ["slide", "text"],
                },
            },
        },
        "required": ["filename", "title", "slides"],
    }

    def execute(self, args: dict[str, Any]) -> str:
        """渲染 slides 为 pptx 并落盘。"""
        try:
            from pptx import Presentation  # 延迟导入：仅执行时需要

            presentation = Presentation()
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            raise ToolError(f"python-pptx 未安装: {exc}") from exc

        filename = require_str(args, "filename", max_len=200)
        title = clean_llm_text(require_str(args, "title", max_len=500))
        slides = args.get("slides")
        if not isinstance(slides, list) or not slides:
            raise ToolError("slides 必须为非空数组")
        subtitle = args.get("subtitle")
        subtitle = clean_llm_text(subtitle.strip()) if isinstance(subtitle, str) else ""
        # 产出增强参数：缺省为空 → 与 M2 产物结构完全一致
        chart_specs = _optional_list(args, "charts")
        note_specs = _optional_list(args, "notes")

        # 封面页
        cover = presentation.slides.add_slide(presentation.slide_layouts[0])
        cover.shapes.title.text = title
        for paragraph in cover.shapes.title.text_frame.paragraphs:
            for run in paragraph.runs:
                _set_cjk_font(run)
        if subtitle:
            cover.placeholders[1].text = subtitle
            for paragraph in cover.placeholders[1].text_frame.paragraphs:
                for run in paragraph.runs:
                    _set_cjk_font(run)

        rendered = 0
        for slide_args in slides:
            if not isinstance(slide_args, dict):
                continue
            self._render_slide(presentation, slide_args)
            rendered += 1

        # 图表与备注在内容页渲染完成后应用，页码语义覆盖封面（1=封面）
        charts_added = self._apply_charts(presentation, chart_specs)
        notes_added = self._apply_notes(presentation, note_specs)

        target = self._resolve_output(filename if filename.endswith(".pptx") else f"{filename}.pptx")
        presentation.save(str(target))
        logger.info(
            "tool_pptx_written",
            extra={
                "event": "tool_pptx_written",
                "file": str(target),
                "slides": rendered + 1,
                "charts": charts_added,
                "notes": notes_added,
            },
        )
        # PUA 字符检测（方框字根因，同 docx_tool）
        all_text = title + "".join(
            str(slide.get("title") or "") + "".join(map(str, slide.get("bullets") or []))
            if isinstance(slide, dict)
            else ""
            for slide in slides
        )
        pua_count = count_unrenderable_chars(clean_llm_text(all_text))
        if pua_count:
            return (
                f"已生成 {target.name}，但检测到 {pua_count} 个无法显示的异常字符"
                "（模型输出的编码缺陷，会显示为方框）。请基于同样内容改用标准简体汉字"
                "重新调用 write_pptx 覆盖生成，确保不含任何异常字符。"
            )
        summary = f"已生成 {target.name}（封面+{rendered} 页内容，{target.stat().st_size} 字节）"
        extras = []
        if chart_specs:
            extras.append(f"{charts_added} 张原生图表")
        if note_specs:
            extras.append(f"{notes_added} 页演讲者备注")
        return f"{summary}；已附加 {'、'.join(extras)}" if extras else summary

    def _apply_charts(self, presentation: Any, specs: list[Any]) -> int:
        """按页码插入 python-pptx 原生图表；非法规格抛 ``ToolError``。"""
        if not specs:
            return 0
        from pptx.chart.data import CategoryChartData
        from pptx.util import Inches

        added = 0
        for index, spec in enumerate(specs):
            label = f"charts 第 {index + 1} 项"
            if not isinstance(spec, dict):
                raise ToolError(f"{label} 必须为对象")
            slide = self._resolve_slide(presentation, spec.get("slide"), label)
            kind = str(spec.get("type") or "").strip().lower()
            if kind not in _CHART_TYPES:
                raise ToolError(
                    f"{label} type 不支持：{kind or '(空)'}（可选：{'/'.join(_CHART_TYPES)}）"
                )
            categories = self._chart_categories(spec.get("categories"), label)
            series = self._chart_series(spec.get("series"), len(categories), label)
            chart_data = CategoryChartData()
            chart_data.categories = categories
            for name, values in series:
                chart_data.add_series(name, values)
            chart = slide.shapes.add_chart(
                _chart_type_of(kind),
                Inches(_CHART_LEFT_INCHES),
                Inches(_CHART_TOP_INCHES),
                Inches(_CHART_WIDTH_INCHES),
                Inches(_CHART_HEIGHT_INCHES),
                chart_data,
            ).chart
            chart_title = spec.get("title")
            if isinstance(chart_title, str) and chart_title.strip():
                chart.has_title = True
                chart.chart_title.text_frame.text = clean_llm_text(chart_title.strip())[:200]
            else:
                chart.has_title = False
            if kind == "pie":  # 饼图显示百分比更可读
                chart.plots[0].has_data_labels = True
                chart.plots[0].data_labels.show_percentage = True
            added += 1
        return added

    def _apply_notes(self, presentation: Any, specs: list[Any]) -> int:
        """按页码写演讲者备注；非法规格抛 ``ToolError``。"""
        if not specs:
            return 0
        added = 0
        for index, spec in enumerate(specs):
            label = f"notes 第 {index + 1} 项"
            if not isinstance(spec, dict):
                raise ToolError(f"{label} 必须为对象")
            slide = self._resolve_slide(presentation, spec.get("slide"), label)
            text = spec.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ToolError(f"{label} text 必须为非空字符串")
            # 覆盖式写入正文文本；已存在备注时追加段落（同页 slides[].notes 不被覆盖）
            frame = slide.notes_slide.notes_text_frame
            cleaned = clean_llm_text(text.strip())[:2000]
            if frame.text.strip():
                frame.add_paragraph().text = cleaned
            else:
                frame.text = cleaned
            added += 1
        return added

    @staticmethod
    def _resolve_slide(presentation: Any, raw_index: Any, label: str) -> Any:
        """解析 1 起算的页码 → slide 对象（越界/非整数 → 可读 ``ToolError``）。"""
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raise ToolError(f"{label} slide 必须为整数页码（1 起算）")
        total = len(presentation.slides)
        if raw_index < 1 or raw_index > total:
            raise ToolError(f"{label} slide 超出范围：{raw_index}（本演示文稿共 {total} 页）")
        return presentation.slides[raw_index - 1]

    @staticmethod
    def _chart_categories(raw: Any, label: str) -> list[str]:
        """校验分类轴标签（非空字符串数组）。"""
        if not isinstance(raw, list) or not raw:
            raise ToolError(f"{label} categories 必须为非空数组")
        categories: list[str] = []
        for item in raw:
            if not isinstance(item, (str, int, float)) or isinstance(item, bool):
                raise ToolError(f"{label} categories 元素必须为字符串或数字")
            categories.append(clean_llm_text(str(item)))
        return categories

    @staticmethod
    def _chart_series(raw: Any, point_count: int, label: str) -> list[tuple[str, list[float]]]:
        """校验系列（名称 + 数值数组，数值个数须与 categories 一致）。"""
        if not isinstance(raw, list) or not raw:
            raise ToolError(f"{label} series 必须为非空数组")
        series: list[tuple[str, list[float]]] = []
        for position, item in enumerate(raw):
            item_label = f"{label} series 第 {position + 1} 项"
            if not isinstance(item, dict):
                raise ToolError(f"{item_label} 必须为对象")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ToolError(f"{item_label} name 必须为非空字符串")
            values = item.get("values")
            if not isinstance(values, list) or not values:
                raise ToolError(f"{item_label} values 必须为非空数组")
            numbers: list[float] = []
            for value in values:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ToolError(f"{item_label} values 元素必须为数字")
                numbers.append(float(value))
            if len(numbers) != point_count:
                raise ToolError(
                    f"{item_label} values 个数（{len(numbers)}）与 categories 个数"
                    f"（{point_count}）不一致"
                )
            series.append((clean_llm_text(name.strip())[:100], numbers))
        return series

    def _render_slide(self, presentation: Any, slide_args: dict[str, Any]) -> None:
        """渲染单个内容页；非法页跳过并记录。"""
        page_title = slide_args.get("title")
        bullets = slide_args.get("bullets")
        if not isinstance(page_title, str) or not page_title.strip():
            return
        if not isinstance(bullets, list):
            bullets = []
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = clean_llm_text(page_title.strip())
        for paragraph in slide.shapes.title.text_frame.paragraphs:
            for run in paragraph.runs:
                _set_cjk_font(run)
        body = slide.placeholders[1]
        text_frame = body.text_frame
        first = True
        for bullet in bullets[:12]:
            text = clean_llm_text(str(bullet).strip())
            if not text:
                continue
            paragraph = text_frame.paragraphs[0] if first else text_frame.add_paragraph()
            paragraph.text = text[:500]
            for run in paragraph.runs:
                _set_cjk_font(run)
            first = False
        notes_text = slide_args.get("notes")
        if isinstance(notes_text, str) and notes_text.strip():
            slide.notes_slide.notes_text_frame.text = clean_llm_text(notes_text.strip())[:2000]
