# JSSH RAG 工作记录

本文档是 JSSH RAG 跨工作区的人机协作记录。详细 diff、命令输出和提交内容以 Git 与 PR 为准；
这里只保留当前范围、验收门、验证摘要和提交/合并标识。

## 当前范围

1. 当前唯一开放知识域是 JSSH 具身手环 `1.6_R6`。
2. 源仓库是 `C:\work1\JSZN\ESP32_S3`，RAG 仅以只读方式处理 Git 跟踪的 `1.6_R6/` 正式文本和受控结构化资料。
3. 当前交付是本地 SQLite FTS5、BOM/网表/PDF 结构化来源、可选私有 Embedding/LLM、CLI 问答和黄金评估。
4. 当前不包含 PickAndPlace/ Gerber/STEP/DXF 解析、Web UI、其他硬件版本、生产查询或设备 Agent。
5. 当前不访问串口、不烧录、不 OTA、不修改配置、不实时采集、不控制真实设备。

## 证据记录规则

1. “设计已提出”“源码已审查”“仿真/Host/QEMU/构建通过”“已烧录”“真机部分通过”
   “真机通过”和“量产接受”分别记录，不互相替代。
2. 索引和评估通过只证明 RAG 对当前语料的处理结果，不代表 R6 固件或硬件通过真机验收。
3. PR 未合并的工作写“待合并”；只有合并到 `main` 并复验后才写“已完成”。
4. 每条记录保留范围、验证摘要和提交/PR 标识；易变的运行日志和本地数据库不进入 Git。

## MVP 门禁

| 门禁 | 当前状态 | 验收依据 |
| --- | --- | --- |
| M0 项目与安全边界 | 已满足 | 独立仓库；源仓库只读；设备操作禁止 |
| M1 R6 可追溯语料 | 已满足 | Git 跟踪过滤；版本、commit、SHA-256、状态、证据等级齐全 |
| M2 确定性解析与本地索引 | 已满足 | Markdown/源码/脚本/JSON/TXT 切分；SQLite FTS5；行号回溯 |
| M3 强制版本与混合检索 | 已满足 | 第一阶段只允许 `1.6_R6`；标识符、全文和可选语义融合 |
| M4 带引用和证据边界的回答 | 已满足 | 无证据拒答；设计提案、非 current 和冲突资料不越级 |
| M5 黄金评估 | 已满足 | 40 条 R6 问题达到当前 MVP 指标 |
| M6 小范围研发试用 | 已满足 | 两轮共12条研发问题；版本污染0、引用错误0、证据越级0；已合并并在 `main` 复验 |
| M7 BOM、网表和 PDF | 进行中 | PR #6 已创建；PR 前复审无 Critical/Important，PR 后首轮复核问题已修正，待重新复审、合并及 `main` 复验后才能写“已满足” |
| M8 其他版本与设备工具 | 未开始 | 每个版本单独准入；设备能力需要独立权限设计和用户授权 |

## 工作记录

| 日期 | 方式 | 工作区 / 分支 | 工作范围 | 验证摘要 | 提交 / PR | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-31 | worktree / PR | `feature/v1.6-r6-rag-mvp` / `.WORKTREE/v1.6-r6-rag-mvp/` | 建立 V1.6 R6 文本 RAG MVP：来源策略、元数据、解析、SQLite FTS5、混合检索、证据约束回答、CLI 和 40 条黄金评估。 | 源提交 `f89e2f847998ee3992c432ccfd2b6df8aa4eca63`；实际索引 334 文档、4047 chunks、0 errors；合并后在 `main@ebc256f` 复验 40 项测试通过；40 条评估的版本污染为 0，引用覆盖、引用位置、证据判断、拒答、Top 5 来源和边界判断均为 100%；未访问或修改真实设备。 | `76166c0` → `ebc256f` / PR #1 | 已完成 |
| 2026-08-31 | worktree / PR 增量 | `feature/v1.6-r6-rag-mvp` / `.WORKTREE/v1.6-r6-rag-mvp/` | 将 ESP32_S3 的入口文档职责适配为本仓库根级 `AGENTS.md`、`plan.md` 和 `README.md`，明确只读源边界、证据治理、验证矩阵、工作树和 PR 流程。 | Markdown 相对链接、稳定命令、完整测试、黄金评估和 `git diff --check` 均通过；固件专用构建、烧录、硬件引脚和 external 镜像条款未复制；合并后已删除该临时 worktree 及本地、远程 feature 分支。 | `873ccc9` → `ebc256f` / PR #1 | 已完成 |
| 2026-08-31 | worktree / M6 两轮试用 | `feature/rag-mvp-progress-audit` / `.WORKTREE/rag-mvp-progress-audit/` | 执行12条R6研发问题，修正旧拓扑/同步方案元数据、受控来源优先级、复合问题分句检索、离线多子句摘要和整机验收边界。 | 实际索引334文档/4047 chunks/0 errors；57项测试、40条黄金评估通过，版本污染0，引用、证据、拒答和边界100%，Top 5来源97.22%；合并后在 `main@50c4851` 重建索引并完成相同回归；两轮试用污染0/引用错误0/越级0；未访问设备；当前会话未执行独立 reviewer。 | `23c8d18` → `50c4851` / PR #2 | 已完成 |
| 2026-09-01 | worktree / 规则同步 | `feature/worktree-process-sync` / `.WORKTREE/worktree-process-sync/` | 对齐并补全 Worktree/PR 全流程：本地与远端 `main` 零差异门禁、从最新 `origin/main` 创建、受控未发布分支 rebase、发布后不改写历史、双阶段 review、分别授权、合并后复验、完成记录交付、精确清理和终检；Git 提交 subject 优先中文。 | PR 前及 PR 后独立 review 问题均已修正，最终 Critical/Important/Minor 均为 0；仓库未配置自动检查；PR #4 合并后从本地 `main` 的 `src` 明确加载，57 项测试、Python 编译、334 文档/4047 chunks 索引、40 条黄金评估及 `git diff --check` 通过；本地 `main` 与 `origin/main` 为 `0 0` 且无树差异；未修改源仓库或设备。 | `0345ae6` → `0759425` / PR #4 → `a5c370a` | 已完成 |
| 2026-09-01 | worktree / M7 | `feature/m7-structured-sources` / `.WORKTREE/m7-structured-sources/` | 受控准入4份BOM XLSX、4份Protel网表和9份原理图/PCB PDF；增加工作表单元格、网表行号和PDF页码定位；保持commit、SHA-256、状态和证据等级；补充6条结构化黄金问题；review 后修正结构化来源 fail-open、文本 chunk ID 兼容、格式边界实检、结构化来源确定性回答、PDF 恢复审计，以及词法/融合两层 priority 越位。 | 源提交`f89e2f847998ee3992c432ccfd2b6df8aa4eca63`；实际索引351文档/4548 chunks/0 errors；1份源PDF触发2条可审计恢复warning；84项测试通过；46条评估版本污染0，引用、定位、证据、拒答和边界100%，Top 5来源97.62%；未修改源仓库或访问设备。 | PR #6（待复审 / 待合并） | 进行中 |

## 后续准入顺序

1. M7 已进入 PR #6；下一门禁是 PR 后重新复审、合并，以及合并后的 `main` 重建和复验。
2. 依次修正元数据、语料状态、切分和排序；最后才调整提示词。
3. M7 当前仅覆盖受控 BOM、网表和可提取 PDF 文字；PickAndPlace、Gerber、STEP、DXF 与图形连通性解析需另行准入。
4. 其他版本按 `1.6_R6 → 1.6 → 1.65 → 1.2 → 2.0 → ARCHIVE` 单独准入。
5. 只读设备查询和受控设备 Agent 必须另立权限、身份复核和真实设备验收流程。
