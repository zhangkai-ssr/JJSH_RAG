"""验证 BOM、Protel 网表和 PDF 的确定性结构化切分。"""

from io import BytesIO
import hashlib
import unittest
from zipfile import ZipFile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from jssh_rag.indexer import parse_document
from jssh_rag.models import DocumentMeta, EvidenceLevel
from jssh_rag.structured import parse_pdf, parse_xlsx_bom


def meta(document_type: str) -> DocumentMeta:
    """建立结构化解析测试使用的完整来源身份。"""
    suffix = {"bom_xlsx": "xlsx", "protel_netlist": "tel", "pdf": "pdf"}[document_type]
    return DocumentMeta(
        product="JSSH",
        hardware_version="1.6_R6",
        relative_path=f"1.6_R6/hardware/test/source.{suffix}",
        git_commit="a" * 40,
        source_sha256=hashlib.sha256(document_type.encode("utf-8")).hexdigest(),
        document_type=document_type,
        module="hardware",
        status="current",
        evidence_level=EvidenceLevel.SOURCE_REVIEWED,
    )


def minimal_xlsx_bytes() -> bytes:
    """生成包含共享字符串和数值单元格的最小 OOXML 工作簿。"""
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="BOM" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    shared_strings = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <si><t>No.</t></si><si><t>Quantity</t></si><si><t>Designator</t></si><si><t>Manufacturer Part</t></si>
 <si><t>1</t></si><si><t>U8</t></si><si><t>LIS2MDL</t></si>
</sst>"""
    sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData>
  <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>
  <row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2"><v>1</v></c><c r="C2" t="s"><v>5</v></c><c r="D2" t="s"><v>6</v></c></row>
 </sheetData>
</worksheet>"""
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/sharedStrings.xml", shared_strings)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def minimal_pdf_bytes(text: str) -> bytes:
    """生成一页带标准字体文本的可提取 PDF。"""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class StructuredParserTest(unittest.TestCase):
    """验证格式原生定位、字段展开和续行处理。"""

    def test_bom_row_keeps_sheet_and_cell_range(self):
        chunks = parse_xlsx_bom(meta("bom_xlsx"), minimal_xlsx_bytes())

        self.assertEqual(1, len(chunks))
        self.assertEqual("BOM!A2:D2", chunks[0].source_locator)
        self.assertIn("Designator: U8", chunks[0].content)
        self.assertIn("Manufacturer Part: LIS2MDL", chunks[0].content)

    def test_protel_net_keeps_source_lines_and_continuation(self):
        text = """$PACKAGES
C0201 ! C0201 ! 100nF ; C1 C2
$NETS
'LIS2_DRDY' ; CN1.11 U8.5 ,
        U17.9
$SCHEDULE
$END
"""

        chunks = parse_document(meta("protel_netlist"), text)

        net = next(item for item in chunks if item.heading_or_symbol == "net LIS2_DRDY")
        self.assertEqual((4, 5), (net.start_line, net.end_line))
        self.assertIn("U17.9", net.content)

    def test_pdf_page_keeps_page_locator(self):
        chunks = parse_pdf(meta("pdf"), minimal_pdf_bytes("R67 GPIO48"))

        self.assertEqual(1, len(chunks))
        self.assertEqual("page 1", chunks[0].source_locator)
        self.assertIn("R67 GPIO48", chunks[0].content)


if __name__ == "__main__":
    unittest.main()
