# JSSH V1.6 R6 RAG

本仓库提供面向 JSSH 具身手环 V1.6 R6 的本地、只读研发知识检索工具。
首期只索引 `C:\work1\JSZN\ESP32_S3` 中 Git 跟踪、可归属到提交的
`1.6_R6` 文本资料，并在回答中保留版本、源提交、文件哈希、证据等级和行号引用。

当前协作规则见 [AGENTS.md](AGENTS.md)，实施范围、验收门和工作记录见
[plan.md](plan.md)。

## 当前能力与边界

- 只服务 `1.6_R6`，不自动回退到 V1.6 或其他硬件版本。
- 使用 SQLite FTS5 全文检索、标识符覆盖、复合问题分句和可选私有语义检索。
- 通过受控元数据优先级区分正式兼容性/驱动/协议入口与历史变更记录。
- 回答固定包含结论、版本、证据等级、引用和不确定性。
- 没有目标版本证据时明确拒答；设计提案不会被表述为已实现或已验证。
- 设计修改、预期改善、源码/构建验证、真机验证和算法接受相互独立。
- 不访问串口，不烧录、不修改设备配置、不控制硬件。
- 默认完全离线；未配置服务地址时，不会向模型服务发送文本。

## 目录入口

| 路径 | 用途 |
| --- | --- |
| `config/sources/v1_6_r6.json` | R6 语料边界、纳入规则和排除规则 |
| `config/metadata_overrides/v1_6_r6.json` | 状态、证据等级和优先级人工覆盖 |
| `src/jssh_rag/` | 索引、存储、检索、回答和评估实现 |
| `tests/` | 自动化测试 |
| `evals/` | 黄金问题集和评估结果 |
| `evals/v1_6_r6_trials.jsonl` | M6 两轮研发问题试用反馈记录 |
| `data/` | 本地索引数据库，默认不进入 Git |
| [AGENTS.md](AGENTS.md) | 本仓库协作、证据和提交规则 |
| [plan.md](plan.md) | 当前范围、验收门和工作记录 |

## 安装与预检

需要 Python 3.11 或更高版本；运行时不依赖第三方 Python 包。

```powershell
python --version
python -m pip install -e . --no-build-isolation
python -m unittest tests.test_preflight -v
```

## 建立索引

```powershell
jssh-rag index --version 1.6_R6
```

索引器通过 Git 读取文件清单和源提交，只纳入已跟踪文件；发现已跟踪但未提交的
源文件时会拒绝索引。本地 SQLite 数据库默认写入 `data/jssh_rag.sqlite3`，不会修改
ESP32_S3 源仓库。SQLite 索引采用单写者模型，请勿并发执行多个重建任务。

## 检索与回答

```powershell
jssh-rag search --version 1.6_R6 --query "ADS1298 DRDY如何连接"
jssh-rag ask --version 1.6_R6 --query "当前R6是否已经完成真机验收"
```

`search` 返回排序后的证据片段；`ask` 在证据之上生成受约束结论。版本参数为必填项，
引用由程序根据实际命中生成。证据不足、证据冲突或只有非当前证据时，输出会保留
不确定性，不会补造结论。

## 可选私有模型服务

只有在已批准的私有网络环境中需要语义检索或生成式回答时，才设置：

```powershell
$env:JSSH_RAG_EMBEDDING_URL = 'http://approved-private-host/v1/embeddings'
$env:JSSH_RAG_LLM_URL = 'http://approved-private-host/v1/chat/completions'
```

服务地址不写入仓库。切换嵌入模型、维度或归一化规则后，应使用新的数据库文件重建
索引，避免复用不兼容的向量缓存。

## 验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src tests
jssh-rag evaluate --version 1.6_R6
git diff --check
```

当前测试、索引和评估快照记录在 [plan.md](plan.md)，README 只保留稳定入口和操作方式。

## 后续扩展门禁

先完成当前 R6 文本 MVP 的合并与干净检出复验，再分别评估 BOM/网表、PDF/表格和
其他硬件版本。设备控制代理不属于本仓库当前范围；如未来立项，应与只读知识库分仓、
分权限和分验收链路实施。
