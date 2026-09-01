# M7 BOM、网表与 PDF 结构化来源 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改 ESP32_S3 源仓库的前提下，把 R6 正式 BOM、Protel 网表和 PDF 按可审计定位符纳入现有本地索引、检索、回答与黄金评估。

**Architecture:** 保留现有 `DocumentMeta -> Chunk -> SQLite -> Retriever -> Answerer` 主链路。文本继续使用行号；XLSX 使用 `工作表!单元格范围`，PDF 使用 `page N`，并把定位符写入 chunk、检索结果和引用。XLSX 由 Python 标准库读取 OOXML，网表由确定性文本解析器读取，PDF 仅增加 `pypdf` 这一项运行依赖并按页提取文本。

**Tech Stack:** Python 3.11+、标准库 `zipfile`/`xml.etree.ElementTree`、SQLite FTS5、`pypdf>=6,<7`、`unittest`

## Global Constraints

- 只支持 `1.6_R6`，源仓库固定为 `C:\work1\JSZN\ESP32_S3`，只读且只索引 Git 跟踪、与 HEAD 一致的正式文件。
- 仅准入 `hardware/*/source/BOM_*.xlsx`、`hardware/*/schematic/Netlist_*.tel`、`hardware/*/schematic/*.pdf` 和 `hardware/*/manufacturing/*.pdf`。
- 不纳入 PickAndPlace、ZIP、STEP、DXF、Gerber、图片、未跟踪文件或其他硬件版本。
- XLSX/PDF 不伪造源码行号；引用必须带原文件、commit、SHA-256 和格式原生定位符。
- PDF 文本提取只代表可检索的源文件内容，不代表原理图/PCB 连通性已自动验证。
- 不访问串口、不构建或烧录固件、不修改源仓库、不启用外部模型服务。

---

### Task 1: 受控来源策略与文档类型

**Files:**
- Modify: `config/sources/v1_6_r6.json`
- Modify: `src/jssh_rag/indexer.py`
- Modify: `src/jssh_rag/models.py`
- Test: `tests/test_source_policy.py`
- Test: `tests/test_metadata.py`

**Interfaces:**
- Consumes: `SourcePolicy.accepts(relative_path, tracked_files)`、`infer_document_fields(relative_path, overrides)`
- Produces: `SourcePolicy.path_patterns: dict[str, tuple[str, ...]]`；文档类型 `bom_xlsx`、`protel_netlist`、`pdf`

- [ ] **Step 1: Write the failing source-policy tests**

```python
def test_only_controlled_structured_files_are_accepted(self):
    self.assertTrue(self.policy.accepts("1.6_R6/hardware/mainboard-top/source/BOM_board.xlsx"))
    self.assertTrue(self.policy.accepts("1.6_R6/hardware/mainboard-top/schematic/Netlist_board.tel"))
    self.assertTrue(self.policy.accepts("1.6_R6/hardware/mainboard-top/schematic/SCH_board.pdf"))
    self.assertFalse(self.policy.accepts("1.6_R6/hardware/mainboard-top/manufacturing/PickAndPlace_board.xlsx"))
```

- [ ] **Step 2: Run the focused tests and verify the expected rejection**

Run: `python -m unittest tests.test_source_policy tests.test_metadata -v`

Expected: FAIL because `.xlsx`、`.tel`、`.pdf` 尚不在策略与类型映射中。

- [ ] **Step 3: Add extension-specific path patterns and document types**

```python
path_patterns = {
    suffix: tuple(patterns)
    for suffix, patterns in data.get("path_patterns", {}).items()
}
if path.suffix.lower() in self.path_patterns:
    return any(path.match(pattern) for pattern in self.path_patterns[path.suffix.lower()])
```

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_source_policy tests.test_metadata -v`

Expected: PASS。

- [ ] **Step 5: Commit the source gate**

```powershell
git add config/sources/v1_6_r6.json src/jssh_rag/indexer.py src/jssh_rag/models.py tests/test_source_policy.py tests/test_metadata.py
git commit -m "增加M7结构化来源准入门禁"
```

### Task 2: 格式原生引用定位符与 SQLite 迁移

**Files:**
- Modify: `src/jssh_rag/models.py`
- Modify: `src/jssh_rag/store.py`
- Modify: `src/jssh_rag/answering.py`
- Modify: `src/jssh_rag/evaluator.py`
- Test: `tests/test_store.py`
- Test: `tests/test_answering.py`

**Interfaces:**
- Consumes: `Chunk.create(...)` 的既有文本行号调用
- Produces: `Chunk.create_located(document, heading_or_symbol, source_locator, content)`；所有 `Chunk`、`RetrievedChunk`、`Citation` 暴露 `source_locator`

- [ ] **Step 1: Write failing locator tests**

```python
chunk = Chunk.create_located(document, "BOM U8", "BOM!A12:K12", "Designator: U8")
self.assertEqual("BOM!A12:K12", chunk.source_locator)
self.assertEqual((0, 0), (chunk.start_line, chunk.end_line))
```

- [ ] **Step 2: Run tests and verify `create_located` is missing**

Run: `python -m unittest tests.test_store tests.test_answering -v`

Expected: FAIL with `AttributeError: type object 'Chunk' has no attribute 'create_located'`。

- [ ] **Step 3: Add locator fields and an additive SQLite migration**

```python
@classmethod
def create_located(cls, document, heading_or_symbol, source_locator, content):
    return cls._create(document, heading_or_symbol, 0, 0, source_locator, content)

if "source_locator" not in chunk_columns:
    connection.execute("ALTER TABLE chunks ADD COLUMN source_locator TEXT NOT NULL DEFAULT ''")
```

- [ ] **Step 4: Preserve exact locator through retrieval, answering and evaluation**

```python
Citation(
    relative_path=item.relative_path,
    heading_or_symbol=item.heading_or_symbol,
    start_line=item.start_line,
    end_line=item.end_line,
    source_locator=item.source_locator,
    git_commit=item.git_commit,
    source_sha256=item.source_sha256,
)
```

- [ ] **Step 5: Run focused tests and commit**

Run: `python -m unittest tests.test_store tests.test_answering tests.test_evaluator -v`

Expected: PASS。

```powershell
git add src/jssh_rag/models.py src/jssh_rag/store.py src/jssh_rag/answering.py src/jssh_rag/evaluator.py tests/test_store.py tests/test_answering.py
git commit -m "支持结构化来源原生定位符"
```

### Task 3: BOM、Protel 网表和 PDF 解析器

**Files:**
- Create: `src/jssh_rag/structured.py`
- Modify: `src/jssh_rag/indexer.py`
- Modify: `pyproject.toml`
- Test: `tests/test_structured.py`
- Test: `tests/test_indexer.py`

**Interfaces:**
- Consumes: `DocumentMeta` 与源文件原始 bytes
- Produces: `parse_xlsx_bom(document, raw) -> list[Chunk]`、`parse_pdf(document, raw) -> list[Chunk]`、`parse_document(...)` 中的 `protel_netlist` 分支

- [ ] **Step 1: Write failing real-format tests**

```python
def test_bom_row_keeps_sheet_and_cell_range(self):
    chunks = parse_xlsx_bom(meta("bom_xlsx"), minimal_xlsx_bytes())
    self.assertEqual("BOM!A2:K2", chunks[0].source_locator)
    self.assertIn("Designator: U8", chunks[0].content)

def test_pdf_page_keeps_page_locator(self):
    chunks = parse_pdf(meta("pdf"), minimal_pdf_bytes("R67 GPIO48"))
    self.assertEqual("page 1", chunks[0].source_locator)
    self.assertIn("R67 GPIO48", chunks[0].content)
```

- [ ] **Step 2: Run the new tests and verify imports fail**

Run: `python -m unittest tests.test_structured -v`

Expected: FAIL because `jssh_rag.structured` does not exist。

- [ ] **Step 3: Implement the smallest deterministic parsers**

```python
def parse_pdf(document: DocumentMeta, raw: bytes, warnings: list[str] | None = None) -> list[Chunk]:
    reader = PdfReader(BytesIO(raw), strict=False)
    return [
        Chunk.create_located(document, f"page {number}", f"page {number}", text)
        for number, page in enumerate(reader.pages, 1)
        if (text := (page.extract_text() or "").strip())
    ]
```

XLSX 只读取 workbook relationship、shared strings 和 worksheet cell，不计算公式；BOM 每个非空数据行生成一个 chunk。网表按 `$PACKAGES`、`$NETS` 和续行边界生成可定位 chunk。PDF 宽松恢复产生的 parser warning 必须写入索引报告，不得用 `0 errors` 隐去来源损坏。

- [ ] **Step 4: Dispatch binary/text reads from the repository indexer**

```python
if fields.document_type == "bom_xlsx":
    chunks = parse_xlsx_bom(document, raw)
elif fields.document_type == "pdf":
    chunks = parse_pdf(document, raw)
else:
    chunks = parse_document(document, raw.decode("utf-8-sig"))
```

- [ ] **Step 5: Run parser/indexer tests and commit**

Run: `python -m unittest tests.test_structured tests.test_indexer -v`

Expected: PASS。

```powershell
git add pyproject.toml src/jssh_rag/structured.py src/jssh_rag/indexer.py tests/test_structured.py tests/test_indexer.py
git commit -m "解析R6 BOM网表与PDF来源"
```

### Task 4: M7 黄金问题、CLI 与文档边界

**Files:**
- Modify: `evals/v1_6_r6.jsonl`
- Modify: `tests/test_cli.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `plan.md`

**Interfaces:**
- Consumes: 扩展后的 `search`、`ask` 和 `evaluate`
- Produces: 至少 6 条 BOM/网表/PDF 黄金问题；CLI JSON 中的 `source_locator`

- [ ] **Step 1: Add a failing CLI locator assertion and M7 cases**

```python
self.assertEqual("BOM!A2:K2", payload["results"][0]["source_locator"])
```

黄金问题覆盖器件型号/位号、网络—引脚、PCB 生产参数、原理图符号和 PDF 证据边界。

- [ ] **Step 2: Run CLI tests and an actual index**

Run: `python -m unittest tests.test_cli -v`

Expected: locator fixture test initially fails, then passes after fixture uses `Chunk.create_located`。

Run: `jssh-rag index --version 1.6_R6`

Expected: 0 errors，文档数包含 4 BOM、4 网表和 9 PDF；宽松恢复的 PDF 诊断进入 `warnings`。

- [ ] **Step 3: Update stable commands, evidence rules and work record**

`README.md` 说明三种定位方式与 PDF 提取边界；`AGENTS.md` 增加结构化来源最少验证；`plan.md` 把 M7 记为“进行中/待合并”，不得提前写成 main 已完成。

- [ ] **Step 4: Run M7 smoke queries and golden evaluation**

Run: `jssh-rag search --version 1.6_R6 --query "LIS2MDL U8"`

Run: `jssh-rag search --version 1.6_R6 --query "LIS2_DRDY CN1.11 CN2.11"`

Run: `jssh-rag search --version 1.6_R6 --query "JLC04121H-7628 板厚 1.2mm"`

Run: `jssh-rag evaluate --version 1.6_R6`

Expected: 版本污染 0、引用覆盖 100%、引用定位准确率不低于 95%、Top 5 来源不低于 90%、边界准确率不低于 95%。

- [ ] **Step 5: Commit docs and acceptance cases**

```powershell
git add evals/v1_6_r6.jsonl tests/test_cli.py README.md AGENTS.md plan.md docs/superpowers/plans/2026-09-01-m7-structured-sources.md
git commit -m "补充M7评估与证据边界"
```

### Task 5: 完整验证与交付前复核

**Files:**
- Verify only: all files changed by Tasks 1-4

**Interfaces:**
- Consumes: M7 branch complete diff
- Produces: fresh verification evidence and a local-only branch ready for independent review

- [ ] **Step 1: Reinstall declared dependencies**

Run: `python -m pip install -e . --no-build-isolation`

Expected: `jssh-rag` 与 `pypdf>=6,<7` 安装成功。

- [ ] **Step 2: Run full regression**

Run: `python -m unittest discover -s tests -v`

Expected: all tests PASS。

Run: `python -m compileall -q src tests`

Expected: exit code 0。

- [ ] **Step 3: Rebuild and evaluate the actual R6 corpus**

Run: `jssh-rag index --version 1.6_R6`

Expected: 0 errors and source commit `f89e2f847998ee3992c432ccfd2b6df8aa4eca63` unless the read-only source has legitimately advanced and remains clean；任何 PDF 容错恢复均以 `warnings` 报告。

Run: `jssh-rag evaluate --version 1.6_R6`

Expected: all documented M7 thresholds pass。

- [ ] **Step 4: Verify diff hygiene and branch scope**

Run: `git diff --check origin/main...HEAD`

Run: `git status --short --branch`

Run: `git diff --stat origin/main...HEAD`

Expected: no whitespace errors；只有 M7 计划内文件；没有数据库、缓存或源仓库文件。

- [ ] **Step 5: Stop before external publication**

报告本地提交、验证结果和精确远程 `https://github.com/zhangkai-ssr/JJSH_RAG.git`。没有新的用户授权时，不推送、不创建 PR、不合并。
