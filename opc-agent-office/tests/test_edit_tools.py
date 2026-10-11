"""表格写出与编辑另存工具单元测试（ADR-065 M2：UT-M2-1 / UT-M2-2 / UT-M2-3）。

编辑类工具的核心红线是「只另存、不动原件」，因此每个用例都对**原文件 SHA256**
做前后比对，确保素材零改动。
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from app.tools.base import ToolError
from app.tools.docx_tool import EditDocxTool
from app.tools.read_tool import ReadDocxTool, ReadXlsxTool
from app.tools.xlsx_tool import EditXlsxTool, WriteXlsxTool


def sha256(path: Path) -> str:
    """计算文件 SHA256（用于验证原素材未被改动）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_docx(path: Path) -> None:
    """构造含标题与段落的 docx 素材。"""
    from docx import Document

    document = Document()
    document.add_heading("原报告", level=1)
    document.add_paragraph("原正文：预算为 100 万元。")
    document.save(str(path))


def make_xlsx(path: Path) -> None:
    """构造双 sheet 的 xlsx 素材（用于追加行 / 改单元格）。"""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "数据"
    sheet.append(["名称", "数量"])
    sheet.append(["甲", 1])
    workbook.create_sheet("说明")
    workbook["说明"].append(["备注"])
    workbook.save(str(path))


def read_rows(path: Path, sheet_name: str) -> list[list[Any]]:
    """用 openpyxl 重开文件并读取指定工作表的所有行。"""
    from openpyxl import load_workbook

    workbook = load_workbook(str(path))
    try:
        return [list(row) for row in workbook[sheet_name].iter_rows(values_only=True)]
    finally:
        workbook.close()


class TestEditDocx:
    """UT-M2-1：edit_docx 追加一节 → 新文件可用，原文件 SHA256 不变。"""

    def test_append_heading_saves_new_file(self, tmp_path: Path) -> None:
        source = tmp_path / "报告.docx"
        make_docx(source)
        before = sha256(source)

        result = EditDocxTool(tmp_path).execute({
            "filename": "报告.docx",
            "ops": [
                {"op": "append_heading", "text": "新增章节", "level": 2},
                {"op": "append_paragraph", "text": "这是追加的正文。"},
            ],
        })

        target = tmp_path / "报告-edited.docx"
        assert target.is_file()
        assert "报告-edited.docx" in result
        assert target.name != source.name
        # 新内容可在新文件中读回
        reopened = ReadDocxTool(tmp_path).execute({"filename": "报告-edited.docx"})
        assert "## 新增章节" in reopened
        assert "这是追加的正文。" in reopened
        assert "原报告" in reopened
        # 原文件零改动
        assert sha256(source) == before

    def test_replace_text_and_append_table_keep_original(self, tmp_path: Path) -> None:
        from docx import Document

        source = tmp_path / "报告.docx"
        make_docx(source)
        before = sha256(source)

        EditDocxTool(tmp_path).execute({
            "filename": "报告.docx",
            "output_filename": "报告-v2.docx",
            "ops": [
                {"op": "replace_text", "old": "100 万元", "new": "120 万元"},
                {"op": "append_table", "rows": [["指标", "值"], ["预算", "120"]]},
            ],
        })

        target = tmp_path / "报告-v2.docx"
        document = Document(str(target))
        texts = [paragraph.text for paragraph in document.paragraphs]
        assert "原正文：预算为 120 万元。" in texts
        assert len(document.tables) == 1
        assert document.tables[0].cell(0, 0).text == "指标"
        assert document.tables[0].cell(1, 1).text == "120"
        # 原文件内容与字节均未变
        assert sha256(source) == before
        assert "100 万元" in [p.text for p in Document(str(source)).paragraphs][1]

    def test_auto_derived_name_keeps_directory(self, tmp_path: Path) -> None:
        (tmp_path / "素材").mkdir()
        source = tmp_path / "素材" / "需求.docx"
        make_docx(source)
        result = EditDocxTool(tmp_path).execute({
            "filename": "素材/需求.docx",
            "ops": [{"op": "append_paragraph", "text": "补充说明"}],
        })
        assert (tmp_path / "素材" / "需求-edited.docx").is_file()
        assert "需求-edited.docx" in result


class TestXlsxWrite:
    """UT-M2-2（写）：write_xlsx 多 sheet + 表头加粗。"""

    def test_multi_sheet_roundtrip(self, tmp_path: Path) -> None:
        from openpyxl import load_workbook

        result = WriteXlsxTool(tmp_path).execute({
            "filename": "汇总.xlsx",
            "sheets": [
                {"name": "销售", "rows": [["区域", "金额"], ["华东", 120], ["华南", 80]]},
                {"name": "库存", "rows": [["物料", "数量"], ["螺丝", 500]]},
            ],
        })
        target = tmp_path / "汇总.xlsx"
        assert target.is_file()
        assert "2 个工作表" in result
        workbook = load_workbook(str(target))
        try:
            assert workbook.sheetnames == ["销售", "库存"]
            assert [list(row) for row in workbook["销售"].iter_rows(values_only=True)] == [
                ["区域", "金额"],
                ["华东", 120],
                ["华南", 80],
            ]
            assert workbook["销售"]["A1"].font.bold is True
            assert workbook["库存"]["A2"].value == "螺丝"
        finally:
            workbook.close()

    def test_headers_rows_shorthand(self, tmp_path: Path) -> None:
        WriteXlsxTool(tmp_path).execute({
            "filename": "单表.xlsx",
            "headers": ["名称", "数量"],
            "rows": [["甲", 1]],
        })
        rows = read_rows(tmp_path / "单表.xlsx", "Sheet1")
        assert rows == [["名称", "数量"], ["甲", 1]]

    def test_single_sheet_by_rows_only(self, tmp_path: Path) -> None:
        WriteXlsxTool(tmp_path).execute({
            "filename": "裸表.xlsx",
            "rows": [["a", "b"], ["1", "2"]],
            "bold_header": False,
        })
        from openpyxl import load_workbook

        workbook = load_workbook(str(tmp_path / "裸表.xlsx"))
        try:
            assert workbook["Sheet1"]["A1"].value == "a"
            assert not workbook["Sheet1"]["A1"].font.bold
        finally:
            workbook.close()


class TestEditXlsx:
    """UT-M2-2（改）：append_rows / set_cell / add_sheet，原文件不动。"""

    def test_append_and_set_cell_save_new_file(self, tmp_path: Path) -> None:
        source = tmp_path / "销量.xlsx"
        make_xlsx(source)
        before = sha256(source)

        result = EditXlsxTool(tmp_path).execute({
            "filename": "销量.xlsx",
            "ops": [
                {"op": "append_rows", "sheet": "数据", "rows": [["乙", 2]]},
                {"op": "set_cell", "sheet": "数据", "cell": "B2", "value": 99},
                {"op": "add_sheet", "name": "汇总", "rows": [["合计", 99]]},
            ],
        })

        target = tmp_path / "销量-edited.xlsx"
        assert target.is_file()
        assert "销量-edited.xlsx" in result

        data_rows = read_rows(target, "数据")
        assert ["乙", 2] in data_rows
        # set_cell 改写既有单元格
        assert [row for row in data_rows if row[0] == "甲"][0][1] == 99
        assert read_rows(target, "汇总") == [["合计", 99]]
        # 原文件的 sheet 与内容均未变，SHA256 一致
        assert read_rows(source, "数据") == [["名称", "数量"], ["甲", 1]]
        assert sha256(source) == before

    def test_edit_survives_read_xlsx_roundtrip(self, tmp_path: Path) -> None:
        source = tmp_path / "销量.xlsx"
        make_xlsx(source)
        EditXlsxTool(tmp_path).execute({
            "filename": "销量.xlsx",
            "ops": [{"op": "append_rows", "sheet": "说明", "rows": [["已复核"]]}],
        })
        reopened = ReadXlsxTool(tmp_path).execute({"filename": "销量-edited.xlsx"})
        assert "[[SHEET 说明]]" in reopened
        assert "已复核" in reopened

    def test_edit_nested_path_output(self, tmp_path: Path) -> None:
        (tmp_path / "数据").mkdir()
        source = tmp_path / "数据" / "表.xlsx"
        make_xlsx(source)
        result = EditXlsxTool(tmp_path).execute({
            "filename": "数据/表.xlsx",
            "ops": [{"op": "append_rows", "sheet": "数据", "rows": [["丙", 3]]}],
        })
        assert (tmp_path / "数据" / "表-edited.xlsx").is_file()
        assert "数据/表-edited.xlsx" in result or "表-edited.xlsx" in result


class TestEditSafety:
    """UT-M2-3：同名输出 / 素材缺失 / 越界 / 格式不符 → 可读 ToolError。"""

    def test_same_output_name_rejected(self, tmp_path: Path) -> None:
        make_docx(tmp_path / "报告.docx")
        make_xlsx(tmp_path / "销量.xlsx")
        before = sha256(tmp_path / "报告.docx")

        with pytest.raises(ToolError) as excinfo:
            EditDocxTool(tmp_path).execute({
                "filename": "报告.docx",
                "output_filename": "报告.docx",
                "ops": [{"op": "append_paragraph", "text": "x"}],
            })
        assert "输出文件名与输入文件相同" in str(excinfo.value)

        with pytest.raises(ToolError) as excinfo:
            EditXlsxTool(tmp_path).execute({
                "filename": "销量.xlsx",
                "output_filename": "销量.xlsx",
                "ops": [{"op": "append_rows", "sheet": "数据", "rows": [["乙", 2]]}],
            })
        assert "输出文件名与输入文件相同" in str(excinfo.value)
        # 拒绝后原件未被覆盖
        assert sha256(tmp_path / "报告.docx") == before

    def test_same_name_with_backslash_separator_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "数据").mkdir()
        make_xlsx(tmp_path / "数据" / "表.xlsx")
        with pytest.raises(ToolError) as excinfo:
            EditXlsxTool(tmp_path).execute({
                "filename": "数据/表.xlsx",
                "output_filename": "数据\\表.xlsx",
                "ops": [{"op": "append_rows", "sheet": "数据", "rows": [["乙", 2]]}],
            })
        assert "输出文件名与输入文件相同" in str(excinfo.value)

    def test_rejects_escape_and_absolute_output(self, tmp_path: Path) -> None:
        make_xlsx(tmp_path / "销量.xlsx")
        # 越界相对路径与工作目录外的绝对路径都必须被拒绝（写侧越界红线）
        for bad_output in ("../越界.xlsx", str(tmp_path.parent / "越界.xlsx")):
            with pytest.raises(ToolError) as excinfo:
                EditXlsxTool(tmp_path).execute({
                    "filename": "销量.xlsx",
                    "output_filename": bad_output,
                    "ops": [{"op": "append_rows", "sheet": "数据", "rows": [["乙", 2]]}],
                })
            assert "非法文件名" in str(excinfo.value) or "越出工作目录" in str(excinfo.value)
        assert not (tmp_path.parent / "越界.xlsx").exists()

    def test_missing_input_readable_error(self, tmp_path: Path) -> None:
        with pytest.raises(ToolError) as excinfo:
            EditDocxTool(tmp_path).execute({
                "filename": "不存在.docx",
                "ops": [{"op": "append_paragraph", "text": "x"}],
            })
        assert "不存在" in str(excinfo.value)
        with pytest.raises(ToolError) as excinfo:
            EditXlsxTool(tmp_path).execute({
                "filename": "不存在.xlsx",
                "ops": [{"op": "append_rows", "sheet": "数据", "rows": [["乙", 2]]}],
            })
        assert "不存在" in str(excinfo.value)

    def test_rejects_path_escape(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "secret.xlsx"
        outside.write_bytes(b"x")
        with pytest.raises(ToolError) as excinfo:
            EditXlsxTool(tmp_path).execute({
                "filename": "../secret.xlsx",
                "ops": [{"op": "append_rows", "sheet": "数据", "rows": [["乙", 2]]}],
            })
        assert "越出工作目录" in str(excinfo.value)

    def test_wrong_format_readable_error(self, tmp_path: Path) -> None:
        (tmp_path / "文本.txt").write_text("x", encoding="utf-8")
        with pytest.raises(ToolError) as excinfo:
            EditDocxTool(tmp_path).execute({
                "filename": "文本.txt",
                "ops": [{"op": "append_paragraph", "text": "x"}],
            })
        assert "edit_docx 仅支持" in str(excinfo.value)
        with pytest.raises(ToolError) as excinfo:
            EditXlsxTool(tmp_path).execute({
                "filename": "文本.txt",
                "ops": [{"op": "append_rows", "sheet": "数据", "rows": [["乙", 2]]}],
            })
        assert "edit_xlsx 仅支持" in str(excinfo.value)

    def test_corrupt_xlsx_readable_error(self, tmp_path: Path) -> None:
        (tmp_path / "坏.xlsx").write_bytes(b"not a real xlsx")
        with pytest.raises(ToolError) as excinfo:
            EditXlsxTool(tmp_path).execute({
                "filename": "坏.xlsx",
                "ops": [{"op": "append_rows", "sheet": "数据", "rows": [["乙", 2]]}],
            })
        assert "edit_xlsx 读取失败" in str(excinfo.value)

    def test_invalid_ops_readable_error(self, tmp_path: Path) -> None:
        make_xlsx(tmp_path / "销量.xlsx")
        tool = EditXlsxTool(tmp_path)
        cases = [
            {"filename": "销量.xlsx", "ops": []},
            {"filename": "销量.xlsx", "ops": [{"op": "删除表"}]},
            {"filename": "销量.xlsx", "ops": [{"op": "append_rows", "sheet": "不存在", "rows": [["乙", 2]]}]},
            {"filename": "销量.xlsx", "ops": [{"op": "append_rows", "sheet": "数据", "rows": []}]},
            {"filename": "销量.xlsx", "ops": [{"op": "set_cell", "sheet": "数据", "cell": "2B", "value": 1}]},
            {"filename": "销量.xlsx", "ops": [{"op": "set_cell", "sheet": "数据", "cell": "B2"}]},
            {"filename": "销量.xlsx", "ops": [{"op": "add_sheet", "name": "数据", "rows": [["x"]]}]},
        ]
        for args in cases:
            with pytest.raises(ToolError) as excinfo:
                tool.execute(args)
            assert str(excinfo.value).strip()
            assert "Traceback" not in str(excinfo.value)

    def test_invalid_docx_ops_readable_error(self, tmp_path: Path) -> None:
        make_docx(tmp_path / "报告.docx")
        tool = EditDocxTool(tmp_path)
        cases = [
            {"filename": "报告.docx", "ops": []},
            {"filename": "报告.docx", "ops": [{"op": "delete_all"}]},
            {"filename": "报告.docx", "ops": [{"op": "append_paragraph", "text": "  "}]},
            {"filename": "报告.docx", "ops": [{"op": "replace_text", "old": "", "new": "x"}]},
            {"filename": "报告.docx", "ops": [{"op": "append_table", "rows": []}]},
            {"filename": "报告.docx", "ops": [{"op": "append_heading", "text": "x", "level": "2"}]},
        ]
        for args in cases:
            with pytest.raises(ToolError) as excinfo:
                tool.execute(args)
            assert str(excinfo.value).strip()

    def test_write_xlsx_invalid_input(self, tmp_path: Path) -> None:
        tool = WriteXlsxTool(tmp_path)
        with pytest.raises(ToolError) as excinfo:
            tool.execute({"filename": "空.xlsx"})
        assert "sheets" in str(excinfo.value)
        with pytest.raises(ToolError) as excinfo:
            tool.execute({"filename": "空.xlsx", "sheets": [{"name": "a"}]})
        assert "rows" in str(excinfo.value)
        with pytest.raises(ToolError) as excinfo:
            tool.execute({"filename": "  ", "rows": [["a"]]})
        assert "filename" in str(excinfo.value)
        with pytest.raises(ToolError) as excinfo:
            tool.execute({"filename": "../越界.xlsx", "rows": [["a"]]})
        assert "越出工作目录" in str(excinfo.value) or "非法文件名" in str(excinfo.value)
