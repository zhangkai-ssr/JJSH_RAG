"""从受控 XLSX BOM 和 PDF 中提取可审计的格式原生知识块。"""

from io import BytesIO
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader

from .models import Chunk, DocumentMeta


_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _shared_strings(archive: ZipFile) -> list[str]:
    """读取 OOXML 共享字符串；没有共享字符串表时返回空列表。"""
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.findall(f".//{{{_SHEET_NS}}}t"))
        for item in root.findall(f"{{{_SHEET_NS}}}si")
    ]


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str:
    """把共享字符串、内联字符串和数值单元格统一为可检索文本。"""
    cell_type = cell.get("t", "")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.findall(f".//{{{_SHEET_NS}}}t")
        ).strip()
    value = cell.find(f"{{{_SHEET_NS}}}v")
    raw = value.text.strip() if value is not None and value.text else ""
    if cell_type == "s" and raw:
        return shared[int(raw)].strip()
    if cell_type == "b":
        return "true" if raw == "1" else "false"
    return raw


def _column_number(reference: str) -> int:
    """把 A、K、AA 等单元格列名转换为一基列号。"""
    match = re.match(r"([A-Z]+)", reference.upper())
    if not match:
        raise ValueError(f"无效 XLSX 单元格引用: {reference}")
    result = 0
    for character in match.group(1):
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _column_name(number: int) -> str:
    """把一基列号转换为 Excel 列名。"""
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _sheet_locator(sheet_name: str, cell_range: str) -> str:
    """按 Excel 语法在需要时引用工作表名。"""
    if re.fullmatch(r"[A-Za-z0-9_.]+", sheet_name):
        return f"{sheet_name}!{cell_range}"
    return f"'{sheet_name.replace(chr(39), chr(39) * 2)}'!{cell_range}"


def parse_xlsx_bom(document: DocumentMeta, raw: bytes) -> list[Chunk]:
    """把 BOM 工作簿的每个非空数据行转换为带单元格范围的知识块。

    Args:
        document: 已完成 Git 身份和文档类型校验的 BOM 元数据。
        raw: 原始 XLSX bytes。

    Returns:
        以表头字段展开、可回到工作表单元格范围的 BOM 行。

    Raises:
        ValueError: 工作簿结构损坏、缺少工作表或共享字符串索引无效。
    """
    try:
        with ZipFile(BytesIO(raw)) as archive:
            shared = _shared_strings(archive)
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {
                item.get("Id", ""): item.get("Target", "")
                for item in relations.findall(f"{{{_REL_NS}}}Relationship")
            }
            chunks: list[Chunk] = []
            for sheet in workbook.findall(f".//{{{_SHEET_NS}}}sheet"):
                sheet_name = sheet.get("name", "Sheet")
                relation_id = sheet.get(f"{{{_OFFICE_REL_NS}}}id", "")
                target = targets.get(relation_id, "")
                if not target:
                    raise ValueError(f"工作表 {sheet_name} 缺少 OOXML relationship")
                sheet_path = (
                    target.lstrip("/")
                    if target.startswith("/xl/")
                    else (PurePosixPath("xl") / target).as_posix()
                )
                root = ElementTree.fromstring(archive.read(sheet_path))
                rows = root.findall(f".//{{{_SHEET_NS}}}row")
                if not rows:
                    continue
                parsed_rows: list[tuple[int, dict[int, str]]] = []
                for row in rows:
                    row_number = int(row.get("r", "0") or 0)
                    values = {
                        _column_number(cell.get("r", "")): _cell_value(cell, shared)
                        for cell in row.findall(f"{{{_SHEET_NS}}}c")
                    }
                    if any(values.values()):
                        parsed_rows.append((row_number, values))
                if len(parsed_rows) < 2:
                    continue
                _, header_values = parsed_rows[0]
                last_column = max(header_values)
                headers = {
                    column: header_values.get(column, "").strip() or _column_name(column)
                    for column in range(1, last_column + 1)
                }
                for row_number, values in parsed_rows[1:]:
                    fields = [
                        f"{headers[column]}: {values[column]}"
                        for column in range(1, last_column + 1)
                        if values.get(column, "").strip()
                    ]
                    if not fields:
                        continue
                    designator = next(
                        (
                            values[column]
                            for column, header in headers.items()
                            if header.casefold() == "designator" and values.get(column)
                        ),
                        str(row_number),
                    )
                    cell_range = f"A{row_number}:{_column_name(last_column)}{row_number}"
                    chunks.append(
                        Chunk.create_located(
                            document,
                            f"BOM {designator}",
                            _sheet_locator(sheet_name, cell_range),
                            "\n".join(fields),
                        )
                    )
            if not chunks:
                raise ValueError("BOM 工作簿没有可索引的数据行")
            return chunks
    except (BadZipFile, ElementTree.ParseError, KeyError, IndexError, OSError) as exc:
        raise ValueError(f"BOM 工作簿无法可靠解析: {exc}") from exc


def parse_pdf(document: DocumentMeta, raw: bytes) -> list[Chunk]:
    """按页提取 PDF 文本并保留页码，不推断图形连通性。

    Args:
        document: 已完成 Git 身份和文档类型校验的 PDF 元数据。
        raw: 原始 PDF bytes。

    Returns:
        每个包含可提取文字的 PDF 页对应一个知识块。

    Raises:
        ValueError: PDF 无法解析或没有任何可提取文字。
    """
    try:
        reader = PdfReader(BytesIO(raw), strict=False)
        chunks = []
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append(
                    Chunk.create_located(
                        document,
                        f"page {number}",
                        f"page {number}",
                        text,
                    )
                )
        if not chunks:
            raise ValueError("PDF 没有可提取文字")
        return chunks
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"PDF 无法可靠解析: {exc}") from exc
