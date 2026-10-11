"""write_docx 工具：结构化 blocks 渲染为 Word 文档（ADR-062）。

LLM 以 JSON blocks 描述文档结构（标题层级/段落/列表/表格），本工具负责
python-docx 渲染；产物落在工作目录内。

本模块另含：

- ``edit_docx``（ADR-065 M2）：在既有 .docx 素材上追加段落/标题/替换文本/
  追加表格，并**另存为新文件**——原素材保持不变；
- ``write_docx`` 产出增强（ADR-065 M3）：目录域（TOC）、页脚页码域（PAGE）、
  页眉/页脚文本。全部为可选参数，**不给参数时产物结构与 M2 完全一致**。

⚠️ Word 的域（TOC/PAGE）由 Word/WPS 打开时计算：本工具写入的是域代码与
域占位文本，未在打开时更新前不会显示真实目录/页码，因此返回文本必须说明
这一点，避免把「域未更新」误判为生成失败。
"""

import logging
from typing import Any

from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.tools.base import (
    CJK_FONT_NAME,
    OfficeTool,
    ToolError,
    clean_llm_text,
    count_unrenderable_chars,
    require_str,
)
from app.tools.read_tool import resolve_read_path

logger = logging.getLogger(__name__)

_SUPPORTED_BLOCK_TYPES = ("heading", "paragraph", "bullet", "table")

#: 需要设置中文字体（w:eastAsia）的样式；docx 默认主题字体对部分 CJK
#: 兼容字符缺字形，Word 渲染为「□」（2026-09-28 实测）
_CJK_STYLES = ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "List Bullet")


def _apply_cjk_fonts(document: Any) -> None:
    """为文档核心样式显式设置中文字体（w:eastAsia）。"""
    from docx.oxml.ns import qn

    for style_name in _CJK_STYLES:
        try:
            style = document.styles[style_name]
        except KeyError:  # pragma: no cover - 语言版本模板差异
            continue
        try:
            rpr = style.element.get_or_add_rPr()
            rfonts = rpr.get_or_add_rFonts()
            rfonts.set(qn("w:eastAsia"), CJK_FONT_NAME)
        except Exception as exc:  # noqa: BLE001 — 单样式设置失败不中断
            logger.warning(
                "docx_style_font_failed",
                extra={"style": style_name, "error": str(exc)},
            )


#: 目录域代码（Word 域指令）：1-3 级标题、超链接、隐藏非打印字符、识别大纲级别
_TOC_FIELD_CODE = r'TOC \o "1-3" \h \z \u'
#: 页码域代码
_PAGE_FIELD_CODE = "PAGE"
#: 域未更新时的占位文本（打开文档后由 Word 计算真实值）
_TOC_PLACEHOLDER = "（目录：请在 Word/WPS 中按 Ctrl+A 后按 F9 更新域）"
_PAGE_PLACEHOLDER = "1"
#: 返回值中必须包含的「域需更新」提示（避免 LLM/用户误判为生成失败）
FIELD_UPDATE_NOTICE = (
    "⚠️ 目录/页码为 Word 域（field），需在 Word/WPS 中打开后更新域才会显示真实内容"
    "（全选 Ctrl+A 后按 F9，或打印预览；本工具已置 updateFields 标记，"
    "部分版本打开时会提示“更新目录”）。当前为域占位文本，属正常现象、非生成失败。"
)


def _new_paragraph_element() -> Any:
    """构造空 ``w:p`` 元素（用于在文档开头插入段落）。"""
    from docx.oxml import OxmlElement

    return OxmlElement("w:p")


def _append_field(paragraph: Any, field_code: str, placeholder: str) -> None:
    """在段落末尾追加一个复杂域（``begin`` → ``instrText`` → ``separate`` → 结果 → ``end``）。

    复杂域结构是 Word 的稳定格式；只写 ``instrText`` 而无 ``fldChar`` 时
    Word 不识别为域，因此必须补齐 begin/separate/end 三段。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def _run() -> Any:
        return paragraph.add_run()

    begin_run = _run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    begin_run._r.append(begin)  # noqa: SLF001 — python-docx 未封装域 API

    instr_run = _run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {field_code} "
    instr_run._r.append(instr)  # noqa: SLF001

    separate_run = _run()
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    separate_run._r.append(separate)  # noqa: SLF001

    result_run = _run()
    result_run.text = placeholder

    end_run = _run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    end_run._r.append(end)  # noqa: SLF001


def _insert_toc(document: Any) -> None:
    """在文档开头（主标题之后）插入目录域（TOC）。

    标题使用 ``Heading 1`` 样式，可被 ``TOC \\o "1-3"`` 正确收录；
    域占位文本附带操作提示，避免用户误判为空白失败。

    实现要点：目录域段落必须紧跟目录标题移动（按标题所在位置插入），
    否则首块为表格时目录会残留在文档末尾。
    """
    heading = document.add_paragraph("目录", style="Heading 1")
    toc_paragraph = document.add_paragraph()
    _append_field(toc_paragraph, _TOC_FIELD_CODE, _TOC_PLACEHOLDER)
    body = document.element.body
    try:
        position = body.index(heading._p)  # noqa: SLF001 — 需按文档顺序移动元素
    except ValueError:  # pragma: no cover - 极端模板差异
        return
    body.remove(heading._p)  # noqa: SLF001
    body.remove(toc_paragraph._p)  # noqa: SLF001
    # 目录标题插到文档主标题之后，目录域段落紧随目录标题
    body.insert(1, heading._p)  # noqa: SLF001 — 位置 0 为文档主标题（Title）段落
    body.insert(2, toc_paragraph._p)  # noqa: SLF001
    logger.debug("docx_toc_inserted", extra={"from_index": position})


def _enable_update_fields(document: Any) -> None:
    """置 ``w:updateFields``：提示 Word/WPS 打开文档时更新域（目录/页码）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    try:
        settings = document.settings.element
    except Exception as exc:  # noqa: BLE001 — 设置不可用时仅告警（域仍在，可手动 F9）
        logger.warning("docx_settings_unavailable", extra={"error": str(exc)})
        return
    update = settings.find(qn("w:updateFields"))
    if update is None:
        update = OxmlElement("w:updateFields")
        settings.append(update)
    update.set(qn("w:val"), "true")


def _optional_bool(args: dict[str, Any], key: str) -> bool:
    """读取可选布尔开关（缺省 false）；显式传入非布尔值视为非法输入。"""
    value = args.get(key)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ToolError(f"参数 {key} 必须为布尔值（true/false）")


def _optional_text(args: dict[str, Any], key: str, *, max_len: int) -> str:
    """读取可选字符串参数（缺省空串）；显式传入非字符串视为非法输入。"""
    value = args.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolError(f"参数 {key} 必须为字符串")
    return clean_llm_text(value.strip())[:max_len]


def _set_part_text(part: Any, text: str) -> None:
    """写页眉/页脚纯文本（复用首个段落，避免多余空行）。"""
    paragraph = part.paragraphs[0] if part.paragraphs else part.add_paragraph()
    paragraph.text = text


def _apply_footer_text(footer: Any, *, footer_text: str, page_numbers: bool) -> None:
    """写页脚：文本与页码域共存时页码另起一行（避免页码覆盖文本）。"""
    target: Any = None
    if footer_text:
        target = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        target.text = footer_text
    if not page_numbers:
        return
    if footer_text:
        target = footer.add_paragraph()  # 页码另起一行
    elif footer.paragraphs:
        target = footer.paragraphs[-1]
    else:  # pragma: no cover - 模板差异
        target = footer.add_paragraph()
    target.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _append_field(target, _PAGE_FIELD_CODE, _PAGE_PLACEHOLDER)


class DocxTool(OfficeTool):
    """生成 .docx 文档。"""

    name = "write_docx"
    description = (
        "生成 Word 文档（.docx）。用 blocks 描述文档结构："
        "heading（含 level 1-3）、paragraph、bullet、table（rows 为二维字符串数组）。"
        "文件名以 .docx 结尾，保存在工作目录内。"
        "可选增强（ADR-065 M3）：toc=true 在开头插入目录域；page_numbers=true 在页脚居中插入页码域；"
        "header/footer 写入页眉/页脚文本。目录与页码为 Word 域，需在 Word/WPS 中更新域才显示内容。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名，如：调研报告.docx"},
            "title": {"type": "string", "description": "文档主标题"},
            "blocks": {
                "type": "array",
                "description": "文档内容块列表，按顺序渲染",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": list(_SUPPORTED_BLOCK_TYPES),
                        },
                        "level": {"type": "integer", "description": "heading 级别 1-3"},
                        "text": {"type": "string", "description": "文本内容"},
                        "rows": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "string"}},
                            "description": "table 二维单元格（首行为表头）",
                        },
                    },
                    "required": ["type"],
                },
            },
            "toc": {
                "type": "boolean",
                "description": "可选：在文档开头插入目录（TOC 域，收录 1-3 级标题），默认 false",
            },
            "page_numbers": {
                "type": "boolean",
                "description": "可选：页脚居中插入页码（PAGE 域），默认 false",
            },
            "header": {"type": "string", "description": "可选：页眉文本"},
            "footer": {"type": "string", "description": "可选：页脚文本（与页码共存时页码另起一行）"},
        },
        "required": ["filename", "title", "blocks"],
    }

    def execute(self, args: dict[str, Any]) -> str:
        """渲染 blocks 为 docx 并落盘。"""
        try:
            from docx import Document  # 延迟导入：仅执行时需要

            document = Document()
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            raise ToolError(f"python-docx 未安装: {exc}") from exc

        filename = require_str(args, "filename", max_len=200)
        title = require_str(args, "title", max_len=500)
        blocks = args.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            raise ToolError("blocks 必须为非空数组")
        # 产出增强参数（默认全关 → 产物结构与 M2 完全一致）
        toc = _optional_bool(args, "toc")
        page_numbers = _optional_bool(args, "page_numbers")
        header_text = _optional_text(args, "header", max_len=500)
        footer_text = _optional_text(args, "footer", max_len=500)

        document.add_heading(clean_llm_text(title), level=0)
        _apply_cjk_fonts(document)
        rendered = 0
        for index, block in enumerate(blocks):
            self._render_block(document, block, index)
            rendered += 1
        # 目录/页眉页脚必须在正文渲染后处理，保证目录插在标题之后、正文之前
        self._apply_page_setup(
            document,
            toc=toc,
            page_numbers=page_numbers,
            header_text=header_text,
            footer_text=footer_text,
        )

        target = self._resolve_output(filename if filename.endswith(".docx") else f"{filename}.docx")
        document.save(str(target))
        logger.info(
            "tool_docx_written",
            extra={
                "event": "tool_docx_written",
                "file": str(target),
                "blocks": rendered,
                "toc": toc,
                "page_numbers": page_numbers,
            },
        )
        # PUA 字符检测（方框字根因）：LLM 偶发把常用汉字输出为私用区码位，
        # 任何字体均无字形；检测到时反馈 LLM 重新生成覆盖
        all_text = title + "".join(
            str(block.get("text") or "") if isinstance(block, dict) else "" for block in blocks
        )
        pua_count = count_unrenderable_chars(clean_llm_text(all_text))
        if pua_count:
            return (
                f"已生成 {target.name}，但检测到 {pua_count} 个无法显示的异常字符"
                "（模型输出的编码缺陷，会显示为方框）。请基于同样内容改用标准简体汉字"
                "重新调用 write_docx 覆盖生成，确保不含任何异常字符。"
            )
        summary = f"已生成 {target.name}（{rendered} 个内容块，{target.stat().st_size} 字节）"
        applied: list[str] = []
        if toc:
            applied.append("目录域")
        if page_numbers:
            applied.append("页码域")
        if header_text:
            applied.append("页眉")
        if footer_text:
            applied.append("页脚")
        if not applied:
            return summary
        return f"{summary}；已附加 {'、'.join(applied)}。{FIELD_UPDATE_NOTICE}"

    def _apply_page_setup(
        self,
        document: Any,
        *,
        toc: bool,
        page_numbers: bool,
        header_text: str,
        footer_text: str,
    ) -> None:
        """应用目录/页码/页眉/页脚（全部关闭时不触碰文档，保持默认结构）。"""
        if not (toc or page_numbers or header_text or footer_text):
            return
        if toc:
            _insert_toc(document)
        if toc or page_numbers:
            # 置 updateFields，提示打开时更新域（目录/页码均需更新才显示内容）
            _enable_update_fields(document)
        for section in document.sections:
            if header_text:
                _set_part_text(section.header, header_text)
            _apply_footer_text(section.footer, footer_text=footer_text, page_numbers=page_numbers)

    def _render_block(self, document: Any, block: Any, index: int) -> None:
        """渲染单个内容块；非法块跳过并记录（不让单块失败毁掉整个文档）。"""
        if not isinstance(block, dict):
            return
        block_type = block.get("type")
        text = clean_llm_text(str(block.get("text") or "").strip())
        try:
            if block_type == "heading" and text:
                level = block.get("level") if isinstance(block.get("level"), int) else 1
                document.add_heading(text, level=max(1, min(3, level)))
            elif block_type == "paragraph" and text:
                document.add_paragraph(text)
            elif block_type == "bullet" and text:
                document.add_paragraph(text, style="List Bullet")
            elif block_type == "table":
                rows = block.get("rows")
                if isinstance(rows, list) and rows:
                    self._render_table(document, rows)
        except Exception as exc:  # noqa: BLE001 — 单块渲染失败不中断
            logger.warning(
                "tool_docx_block_skipped",
                extra={"event": "tool_docx_block_skipped", "index": index, "error": str(exc)},
            )

    def _render_table(self, document: Any, rows: list[Any]) -> None:
        """渲染表格（首行表头加粗；行内列数取最大值，缺格补空）。"""
        cleaned = [
            [clean_llm_text(str(cell)) for cell in row] if isinstance(row, list) else [clean_llm_text(str(row))]
            for row in rows
        ]
        width = max(len(row) for row in cleaned)
        table = document.add_table(rows=len(cleaned), cols=width)
        table.style = "Table Grid"
        for row_index, row in enumerate(cleaned):
            for col_index in range(width):
                cell = table.cell(row_index, col_index)
                value = row[col_index] if col_index < len(row) else ""
                cell.text = value
                if row_index == 0:
                    for run in cell.paragraphs[0].runs:
                        run.bold = True


#: edit_docx 支持的编辑操作
_EDIT_OPS = ("append_paragraph", "append_heading", "replace_text", "append_table")


def _replace_in_paragraph(paragraph: Any, old: str, new: str) -> int:
    """段落内全文替换 ``old`` → ``new``，返回替换次数。

    优先逐 run 替换（保留其余 run 的格式）；run 边界切割导致跨 run 匹配时，
    退化为整段合并后重写（格式取首个 run）。
    """
    count = 0
    for run in paragraph.runs:
        if old in run.text:
            run.text = run.text.replace(old, new)
            count += 1
    if count:
        return count
    full = "".join(run.text for run in paragraph.runs)
    if old not in full:
        return 0
    count = full.count(old)
    runs = paragraph.runs
    runs[0].text = full.replace(old, new)
    for run in runs[1:]:
        run.text = ""
    return count


class EditDocxTool(OfficeTool):
    """在既有 .docx 上追加段落/标题、替换文本、追加表格，并另存为新文件。"""

    name = "edit_docx"
    description = (
        "编辑工作目录内已有的 Word 文档（.docx）并**另存为新文件**（原文件保持不变）。"
        "ops 支持：append_paragraph（text，可选 style）、append_heading（text + level 1-3）、"
        "replace_text（old + new，全文替换）、append_table（rows 二维数组）。"
        "output_filename 缺省时自动派生（如 报告.docx → 报告-edited.docx），返回文本会说明实际输出名。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "已存在的相对路径，如：素材/报告.docx"},
            "ops": {
                "type": "array",
                "description": "编辑操作列表（按顺序应用）",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": list(_EDIT_OPS),
                            "description": "操作类型",
                        },
                        "text": {"type": "string", "description": "append_paragraph / append_heading 的文本"},
                        "style": {"type": "string", "description": "可选：段落样式名，如 List Bullet"},
                        "level": {"type": "integer", "description": "append_heading 的标题级别 1-3"},
                        "old": {"type": "string", "description": "replace_text 被替换的原文"},
                        "new": {"type": "string", "description": "replace_text 替换后的新文本"},
                        "rows": {
                            "type": "array",
                            "items": {"type": "array"},
                            "description": "append_table 的二维单元格（首行为表头）",
                        },
                    },
                    "required": ["op"],
                },
            },
            "output_filename": {
                "type": "string",
                "description": "可选：另存文件名（必须与 filename 不同，缺省自动加 -edited）",
            },
        },
        "required": ["filename", "ops"],
    }

    def execute(self, args: dict[str, Any]) -> str:
        """读入 → 改 → 另存；失败抛 ``ToolError``（不抛未捕获异常）。"""
        try:
            from docx import Document  # 延迟导入：仅执行时需要
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            raise ToolError(f"python-docx 未安装: {exc}") from exc

        filename = require_str(args, "filename", max_len=500)
        source = resolve_read_path(self.work_dir, filename)
        if source.suffix.lower() not in (".docx", ".docm"):
            raise ToolError(
                f"{self.name} 仅支持 .docx 文件，收到：{source.name}"
                "（请确认素材格式；只读素材请改用 read_docx）"
            )
        ops = args.get("ops")
        if not isinstance(ops, list) or not ops:
            raise ToolError("ops 必须为非空数组")
        target = self.resolve_edit_output(
            input_path=source,
            output_filename=args.get("output_filename"),
            suffix=".docx",
        )

        try:
            document = Document(str(source))
        except Exception as exc:  # noqa: BLE001 — 解析异常转可读错误，不终止工具循环
            logger.warning(
                "tool_edit_load_failed",
                extra={"event": "tool_edit_load_failed", "tool": self.name, "error": str(exc)},
            )
            raise ToolError(
                f"{self.name} 读取失败（{source.name}）：{type(exc).__name__}: {exc}"
            ) from exc

        _apply_cjk_fonts(document)
        applied = [self._apply_op(document, op, index) for index, op in enumerate(ops)]
        try:
            document.save(str(target))
        except Exception as exc:  # noqa: BLE001 — 保存异常转可读错误
            raise ToolError(f"{self.name} 保存失败（{target.name}）：{type(exc).__name__}: {exc}") from exc
        logger.info(
            "tool_docx_edited",
            extra={
                "event": "tool_docx_edited",
                "tool": self.name,
                "source": str(source),
                "output": str(target),
                "ops": len(applied),
            },
        )
        return (
            f"已另存为新文件 {target.name}（源文件 {source.name} 保持不变），"
            f"共应用 {len(applied)} 个操作：\n" + "\n".join(f"- {item}" for item in applied)
        )

    def _apply_op(self, document: Any, op: Any, index: int) -> str:
        """应用单个操作并返回可读描述；非法操作抛 ``ToolError``（不静默跳过）。"""
        if not isinstance(op, dict):
            raise ToolError(f"ops 第 {index + 1} 项必须为对象")
        kind = str(op.get("op") or "").strip()
        if kind not in _EDIT_OPS:
            raise ToolError(
                f"ops 第 {index + 1} 项 op 不支持：{kind or '(空)'}（可选：{'/'.join(_EDIT_OPS)}）"
            )
        if kind == "append_heading":
            text = clean_llm_text(self._op_text(op, index))
            level = op.get("level")
            if level is not None and (isinstance(level, bool) or not isinstance(level, int)):
                raise ToolError(f"ops 第 {index + 1} 项 level 必须为整数（1-3）")
            level = 1 if level is None else max(1, min(3, level))
            document.add_heading(text, level=level)
            return f"追加 {level} 级标题：{text[:30]}"
        if kind == "append_paragraph":
            text = clean_llm_text(self._op_text(op, index))
            style = op.get("style")
            if style is not None and not isinstance(style, str):
                raise ToolError(f"ops 第 {index + 1} 项 style 必须为字符串（样式名）")
            if isinstance(style, str) and style.strip():
                name = style.strip()
                if name not in [item.name for item in document.styles]:
                    raise ToolError(f"ops 第 {index + 1} 项样式不存在：{name}")
                document.add_paragraph(text, style=name)
                return f"追加段落（样式 {name}）：{text[:30]}"
            document.add_paragraph(text)
            return f"追加段落：{text[:30]}"
        if kind == "replace_text":
            old = op.get("old")
            if not isinstance(old, str) or not old:
                raise ToolError(f"ops 第 {index + 1} 项 old 必须为非空字符串")
            new = op.get("new")
            if new is None:
                new = ""
            elif not isinstance(new, str):
                raise ToolError(f"ops 第 {index + 1} 项 new 必须为字符串")
            count = self._replace_document_text(document, clean_llm_text(old), clean_llm_text(new))
            return f"全文替换 {old[:20]} → {new[:20]}（命中 {count} 段）"
        rows = op.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ToolError(f"ops 第 {index + 1} 项 rows 必须为非空二维数组")
        table = self._append_table(document, rows)
        return f"追加表格 {len(rows)} 行 × {len(table.columns)} 列"

    @staticmethod
    def _op_text(op: dict[str, Any], index: int) -> str:
        """取操作文本参数（必填非空，超长截断防单块塞入整篇正文）。"""
        value = op.get("text")
        if not isinstance(value, str) or not value.strip():
            raise ToolError(f"ops 第 {index + 1} 项 text 必须为非空字符串")
        return value.strip()[:4_000]

    @staticmethod
    def _replace_document_text(document: Any, old: str, new: str) -> int:
        """对正文段落、表格单元格、页眉页脚做全文替换，返回命中的段落数。"""
        paragraphs: list[Any] = list(document.paragraphs)
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    paragraphs.extend(cell.paragraphs)
        for section in document.sections:
            for part in (section.header, section.footer):
                paragraphs.extend(part.paragraphs)
                for table in part.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            paragraphs.extend(cell.paragraphs)
        return sum(1 for paragraph in paragraphs if _replace_in_paragraph(paragraph, old, new))

    @staticmethod
    def _append_table(document: Any, rows: list[Any]) -> Any:
        """在文档末尾追加表格（首行表头加粗，列数取最大值，缺格补空）。"""
        cleaned = [
            [clean_llm_text(str(cell)) for cell in row] if isinstance(row, list) else [clean_llm_text(str(row))]
            for row in rows
        ]
        width = max(len(row) for row in cleaned)
        table = document.add_table(rows=len(cleaned), cols=width)
        table.style = "Table Grid"
        for row_index, row in enumerate(cleaned):
            for col_index in range(width):
                cell = table.cell(row_index, col_index)
                cell.text = row[col_index] if col_index < len(row) else ""
                if row_index == 0:
                    for run in cell.paragraphs[0].runs:
                        run.bold = True
        return table
