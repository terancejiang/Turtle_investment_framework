# 龟龟投资策略 v1.0 — 协调器（Coordinator）

> 本文件为多阶段分析的调度中枢。协调器自身不执行数据获取或分析计算，仅负责：
> (1) 解析用户输入；(2) 通过 AskUserQuestion 补全关键信息；(3) 按依赖关系调度 Phase 0/1/2/3；(4) 交付最终报告。

---

## v1.0 变更摘要（vs v0.16_alpha）

- **新增 Phase 0**：内置 `/download-report` 命令，自动搜索并下载年报 PDF
- **Phase 1 拆分两步**：Step A = `tushare_collector.py`（Python 脚本采集结构化数据）+ Step B = Agent WebSearch（非结构化信息）
- **Phase 2 拆分两步**：Step A = `pdf_preprocessor.py`（Python 关键词定位 7 章节：P2-P13 + MDA + SUB）+ Step B = Agent 精提取（5+1 项 footnote 数据，SUB 条件触发）
- **Pipeline 重排**：Phase 1A + Phase 2A 并行运行；Phase 1B 在 Phase 1A 完成后立即启动（§10 到达时检查 pdf_sections.json）
- **单位统一**：所有金额单位为 **百万元**（Tushare 原始单位元 ÷ 1e6）
- **新增母公司报表**：§3P/§4P 母公司损益表和资产负债表（Tushare `report_type=4`）
- **yfinance 保留为 fallback**：Tushare 失败时降级使用
- **AskUserQuestion 交互**：结构化收集持股渠道、PDF 处理方式、Tushare Token 等
- **渐进式披露**：Phase 3 精简执行器 + references/ 按需加载

---

## 输入解析

用户输入可能包含以下组合：

| 输入项 | 示例 | 必需？ |
|--------|------|--------|
| 股票代码或名称 | `600887` / `伊利股份` / `0001.HK` / `长和` / `AAPL` / `AAPL.US` | 必需 |
| 持股渠道 | `港股通` / `直接` / `美股券商` | 可选（未指定则触发 AskUserQuestion） |
| PDF 年报文件 | 用户上传的 `.pdf` 文件 | 可选（未提供则触发 Phase 0 自动下载） |

**解析规则**：
1. 从用户消息中提取股票代码/名称和持股渠道
2. 检查是否有 PDF 文件上传（检查 `/sessions/*/mnt/uploads/` 目录中的 `.pdf` 文件）
3. 若用户只给了公司名称没给代码，在 Phase 1 Step A 中由脚本通过 Tushare `stock_basic` 确认代码
4. 股票代码格式化：A 股 → `XXXXXX.SH` 或 `XXXXXX.SZ`；港股 → `XXXXX.HK`；美股 → `AAPL.US`

---

## AskUserQuestion 交互

当用户输入不完整或存在歧义时，**立即使用 AskUserQuestion 工具**收集必要信息，而不是猜测或使用默认值。

### 触发条件与问题模板

**条件1：持股渠道未指定（港股标的）**

```
AskUserQuestion:
  question: "请问您通过什么渠道持有这只港股？"
  header: "持股渠道"
  options:
    - label: "港股通（推荐）"
      description: "通过内地券商的港股通渠道持有，适用20%股息税率"
    - label: "直接持有"
      description: "通过香港券商直接持有，H股适用28%股息税率，红筹/开曼适用20%"
```

**条件2：标的为多地上市公司**

```
AskUserQuestion:
  question: "{公司名}同时在A股和港股上市，您希望分析哪个市场的股票？"
  header: "分析市场"
  options:
    - label: "港股 ({港股代码})"
      description: "分析港股市场的股票，适用港股估值门槛和税率"
    - label: "A股 ({A股代码})"
      description: "分析A股市场的股票，适用A股估值门槛和税率"
```

**条件3：未上传年报PDF且未检测到本地缓存**

```
AskUserQuestion:
  question: "您是否有该公司的最新年报PDF？上传年报可以获得更精确的附注数据分析。"
  header: "年报PDF"
  options:
    - label: "没有，自动下载（推荐）"
      description: "Phase 0 默认获取最近5年年报；若最近一年年报未披露则用本年季报/半年报补齐；本地已有的不重复下载"
    - label: "没有，跳过"
      description: "仅使用 Tushare + WebSearch 数据分析，部分模块将使用降级方案（~85%精度）"
    - label: "稍后上传"
      description: "我会手动上传年报PDF文件"
```

**条件4：模糊的公司名称**

```
AskUserQuestion:
  question: "搜索到多个匹配结果，请确认您要分析的公司："
  header: "确认标的"
  options:
    - label: "{公司1} ({代码1})"
      description: "{行业/简介}"
    - label: "{公司2} ({代码2})"
      description: "{行业/简介}"
```

**条件5：Tushare Token 未配置**

```
AskUserQuestion:
  question: "本策略需要 Tushare Pro API Token 来获取财务数据。请提供您的 Token（可从 tushare.pro 注册获取）："
  header: "Tushare Token"
  options:
    - label: "我有 Token"
      description: "请在下方输入您的 Tushare Pro Token"
    - label: "没有 Token"
      description: "将使用 yfinance 作为备用数据源（数据精度可能降低）"
```

### 不触发 AskUserQuestion 的情况

- 用户提供了完整的股票代码（如 `600887`、`0001.HK`）→ 直接执行
- A股标的且未指定渠道 → 默认"长期持有"
- 美股标的且未指定渠道 → 默认"W-8BEN"
- 用户在消息中明确说了渠道（如"我通过港股通持有长和"）→ 直接使用
- 环境变量 `TUSHARE_TOKEN` 已设置 → 直接使用

---

## 阶段调度

```
┌─────────────────────────────────────────────────┐
│              用户输入解析                          │
│   股票代码 = {code}                               │
│   持股渠道 = {channel | AskUserQuestion}          │
│   PDF年报 = {有 | 无 | 自动下载}                  │
│   Tushare Token = {有 | 无 → yfinance fallback}  │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  Phase 0：PDF 自动获取（仅当需要时）               │
│  /download-report 命令                            │
│                                                   │
│  ⚠️ 触发条件：                                    │
│     用户未上传 PDF + 选择了"自动下载"               │
│  跳过条件：                                       │
│     用户已上传 PDF / 选择了"跳过" / "稍后上传"     │
│                                                   │
│  默认策略：最近 5 年年报；若最近一年年报未披露     │
│          则用本年披露的季报/半年报补齐             │
│  去重：output/{code}_{company}/ 已有的 PDF 不重复下载│
│  输出：output/{code}_{company}/*.pdf（或下载失败 Warning）│
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────── Step A: Python 脚本（并行启动）──────────┐
│                                                    │
│  ┌────────────────────────┐  ┌──────────────────┐  │
│  │  Phase 1A: Tushare采集  │  │  Phase 2A: PDF解析│  │
│  │                         │  │  ⚠️ 仅当有PDF时   │  │
│  │  Bash 运行              │  │                   │  │
│  │  tushare_collector.py   │  │  Bash 运行        │  │
│  │  → data_pack_market.md  │  │  pdf_preprocessor │  │
│  │    (§1-§6, §7部分,      │  │  → pdf_sections   │  │
│  │     §9, §11, §12,      │  │    .json          │  │
│  │     §14, §15, §16,     │  │  (P2-P13+MDA+SUB) │  │
│  │     §3P, §4P,          │  │                   │  │
│  │     审计意见, §13.1)    │  └──────────────────┘  │
│  │  → available_fields.json│                        │
│  └────────────────────────┘                        │
│                                                    │
└───────────┬────────────────────────────────────────┘
            │  Phase 1A 完成后立即启动 Phase 1B
            │  Phase 2A 可与 Phase 1B 并行运行
            ▼
┌─────────── Step B: Agent（Phase 1A 完成后启动）────┐
│                                                    │
│  ┌────────────────────────┐                        │
│  │  Phase 1B: WebSearch   │                        │
│  │  补充 §7, §8, §10, §13│                        │
│  │  ⚠️ §7/§8/§9B 不依赖   │                        │
│  │    pdf_sections.json   │                        │
│  │  ⚠️ §10 到达时检查      │                        │
│  │    pdf_sections.json   │                        │
│  │    是否已生成           │                        │
│  │  → 追加到              │                        │
│  │    data_pack_market.md │                        │
│  └────────┬───────────────┘                        │
│           │                                        │
│  ┌────────────────────────┐                        │
│  │  Phase 2B: PDF精提取    │                        │
│  │  ⚠️ 仅当有PDF时         │                        │
│  │  ⚠️ 等待 Phase 2A 完成  │                        │
│  │  精提取5+1项footnote   │                        │
│  │  (SUB条件触发)          │                        │
│  │  → data_pack_report.md │                        │
│  └────────┬───────────────┘                        │
│           │                                        │
└───────────┼────────────────────────────────────────┘
            │     等待全部完成
            ▼
┌─────────────────────────────────────────────────┐
│           Phase 3: 分析与报告                      │
│           Task Agent                              │
│                                                    │
│  输入：data_pack_market.md                         │
│        data_pack_report.md（若有）                  │
│        phase3_分析与报告.md（精简执行器）            │
│        references/ 目录（按需加载）                 │
│                                                    │
│  渐进式披露：                                      │
│     执行器仅含工作流+报告模板                       │
│     各因子详细规则按需从 references/ 读取            │
│                                                    │
│  输出：{output_dir}/{公司名}_{代码}_分析报告.md     │
│                                                    │
│  ⚠️ 不调用任何外部数据源                            │
│  ⚠️ 内部设置 checkpoint：                          │
│     每完成一个因子 → 将结论追加写入报告文件          │
│     防止 Phase 3 自身 context compact               │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│           协调器交付                               │
│  1. 确认报告文件已生成                              │
│  2. 返回报告文件链接给用户                          │
└─────────────────────────────────────────────────┘
```

---

## Sub-agent 调用指令

### 环境准备（首次运行）

```bash
# 安装 Python 依赖
pip install tushare pandas pdfplumber --break-system-packages
```

### Phase 0：PDF 自动获取

```
# 步骤 1：下载年报（必选，仅当用户选择"自动下载"时执行）
/download-report {stock_code} {year} 年报
# 或手动调用: python3 scripts/download_report.py --url <URL> --stock-code <code> --report-type 年报 --year <year> --save-dir {output_dir}
# 下载目标年报 → {output_dir}/{code}_{year}_年报.pdf

# 检查下载结果
# 成功 → pdf_path = 下载文件路径
# 失败 → pdf_path = None，进入无 PDF 模式

# 步骤 2：下载中报（条件触发）
# ⚠️ 仅当 Phase 1A 输出显示中报已发布时执行（见中报时效性规则）
# 下载目标中报 → {output_dir}/{code}_{year}_中报.pdf
```

### Step A：Python 脚本（Phase 1A + Phase 2A 并行）

```
# === Phase 1A：Tushare 采集（Bash 调用）===
Bash(
  command = "python3 scripts/tushare_collector.py --code {ts_code} --output {output_dir}/data_pack_market.md",
  description = "Phase1A Tushare采集"
)
# 输出：data_pack_market.md（§1-§6, §7部分, §9, §11, §12, §14, §15, §16, §3P, §4P, 审计意见, §13.1）
# 输出：available_fields.json（可用字段清单）

# === Phase 2A.5（可选）：Agent 读取 PDF 前 10 页提取 TOC ===
# ⚠️ 仅当有 PDF 时执行，可与 Phase 1A 并行
Task(
  subagent_type = "general-purpose",
  prompt = """
  读取 PDF 文件 {output_dir}/{code}_{year}_年报.pdf 前 10 页。从目录页提取章节→页码映射，重点定位：
  - "主要控股参股公司" 或 "在子公司中的权益" 章节的起始页
  - "管理层讨论与分析" 章节的起始页
  输出 JSON: {output_dir}/toc_hints.json
  格式: {"SUB": {"page": N, "title": "..."}, "MDA": {"page": N, "title": "..."}}
  若目录页不存在或无法解析，输出空 JSON: {}
  """,
  description = "Phase2A.5 TOC定位"
)

# === Phase 2A：PDF 预处理（Bash 调用，仅当有 PDF 时，等待 Phase 2A.5）===
# 年报 PDF（必选，若有 PDF）
Bash(
  command = "python3 scripts/pdf_preprocessor.py --pdf {output_dir}/{code}_{year}_年报.pdf --output {output_dir}/pdf_sections.json --hints {output_dir}/toc_hints.json",
  description = "Phase2A PDF预处理-年报"
)
# 输出：pdf_sections.json（7 段文本片段：P2/P3/P4/P6/P13/MDA/SUB）

# 中报 PDF（条件触发，若中报 PDF 存在）
Bash(
  command = "python3 scripts/pdf_preprocessor.py --pdf {output_dir}/{code}_{h1_year}_中报.pdf --output {output_dir}/pdf_sections_interim.json",
  description = "Phase2A PDF预处理-中报"
)
```

### Step B：Agent（Phase 1B 在 Phase 1A 完成后立即启动，Phase 2B 等待 Phase 2A）

```
# === Phase 1B：Agent WebSearch 补充（Task 调用）===
# ⚠️ Phase 1A 完成后立即启动，不等待 Phase 2A
Task(
  subagent_type = "general-purpose",
  prompt = """
  请阅读 {prompts_dir}/phase1_数据采集.md 中的完整指令。

  目标股票：{stock_code}（{company_name}）
  持股渠道：{channel}

  data_pack_market.md 已由 tushare_collector.py 生成了 §1-§6, §7(部分:十大股东), §9, §11, §12, §14, §15, §16, §3P, §4P, 审计意见, §13.1 部分。
  你的任务是通过 WebSearch 补充以下章节，追加到 {output_dir}/data_pack_market.md：
  - §7 管理层与治理
  - §8 行业与竞争
  - §9B 上市子公司识别（条件触发：仅控股公司）
  - §10 MD&A 摘要
  - §13 Warnings

  §7/§8/§9B 不依赖 pdf_sections.json，直接通过 WebSearch 获取。
  §10 执行时检查 {output_dir}/pdf_sections.json 是否存在：
    若存在 → 优先使用其中的 MDA 字段
    若不存在 → 使用 WebSearch fallback 获取 MDA 摘要

  注意：data_pack_market.md 中 §8, §10, §13.2 含占位符 `*[§N 待Agent WebSearch补充]*`。
  使用 Edit 工具**替换**这些占位符为实际内容，而非在文件末尾追加。
  §7 已有结构化数据（十大股东表+审计意见），在其后追加定性信息即可。
  """,
  description = "Phase1B WebSearch补充"
)

# === Phase 2B：Agent 精提取（Task 调用，仅当有 PDF 时）===
Task(
  subagent_type = "general-purpose",
  prompt = """
  请阅读 {prompts_dir}/phase2_PDF解析.md 中的完整指令。

  pdf_sections.json 文件路径：{output_dir}/pdf_sections.json
  中报 pdf_sections（若有）：{output_dir}/pdf_sections_interim.json
  公司名称：{company_name}
  将解析结果写入：{output_dir}/data_pack_report.md
  将中报解析结果写入（若有中报）：{output_dir}/data_pack_report_interim.md
  """,
  description = "Phase2B PDF精提取"
)
```

### Phase 3：分析与报告

```
# 等待 Phase 1 + Phase 2 全部完成后启动
Task(
  subagent_type = "general-purpose",
  prompt = """
  请阅读 {prompts_dir}/phase3_分析与报告.md 中的完整指令。

  数据包文件：
    - {output_dir}/data_pack_market.md
    - {output_dir}/data_pack_report.md （年报附注，若存在）
    - {output_dir}/data_pack_report_interim.md （中报附注，若存在）
  因子参考文件目录：{prompts_dir}/references/
  将分析报告写入：{output_dir}/{company}_{code}_分析报告.md

  注意事项：
  - 所有金额单位为百万元（人民币），报告中显示时使用千位逗号分隔
  - 母公司报表数据来自 data_pack_market.md §3P/§4P
  - 若 data_pack_report.md 不存在，使用降级方案
  - ⚠️ 当中报数据包存在时，应优先使用中报中更新的数据（如最新受限资产、
    应收账款账龄等），但年报数据作为完整年度基线仍需参考
  """,
  description = "Phase3 分析报告"
)
```

### 当没有 PDF 年报时（跳过 Phase 2）

```
# Phase 1 完成后直接启动 Phase 3（无 data_pack_report.md）
Task(
  subagent_type = "general-purpose",
  prompt = """
  请阅读 {prompts_dir}/phase3_分析与报告.md 中的完整指令。

  数据包文件：
    - {output_dir}/data_pack_market.md
  注意：本次分析无年报PDF。data_pack_report.md 不存在。
  - P2/P3/P4/P6/P13 附注数据不可用，使用降级方案
  - MDA 不可用，§10 MD&A 基于 WebSearch 获取的摘要信息
  - 母公司单体报表数据已通过 Tushare report_type=4 获取，在 §3P/§4P 中
  因子参考文件目录：{prompts_dir}/references/
  将分析报告写入：{output_dir}/{company}_{code}_分析报告.md
  """,
  description = "Phase3 分析报告（无PDF模式）"
)
```

---

## 报表时效性规则

协调器在启动 Phase 0 前，应确定目标年报年份：

- 若当前日期在 1-3月，最新年报可能尚未发布，使用上一财年年报
- 若当前日期在 4月及以后，最新财年年报通常已发布

Tushare 数据自动覆盖最近 5 个财年，无需手动指定年份。

**支付率等关键指标必须基于同币种数据计算**（股息总额与归母净利润均取报表币种），不依赖 yfinance 的 payoutRatio 等衍生字段。

### 中报时效性规则（双PDF触发）

当 Phase 1A 的输出 data_pack_market.md 中出现 "YYYYH1" 列（如 "2025H1"），
说明该公司已发布比最新年报更新的中报（半年报）。此时：

1. Phase 0 应下载**两份** PDF：最新年报 + 最新中报
2. Phase 2A 应对两份 PDF 分别运行 pdf_preprocessor.py
3. Phase 2B 应分别处理两份 pdf_sections.json
4. Phase 3 应同时参考两份 data_pack_report

判断方法：Phase 1A 完成后，检查 data_pack_market.md 的 §3 损益表表头。
若第一列为 "YYYYH1" 格式 → 触发双 PDF 流程。

示例：
  表头为 ["2025H1", "2024", "2023", ...] → 下载 2024年报 + 2025中报
  表头为 ["2024", "2023", ...]           → 仅下载 2024年报

执行顺序调整：
```
Phase 1A + Phase 0-年报 (并行)
    ↓
检查 Phase 1A 输出是否包含 H1 列
    ↓ (若有)
Phase 0-中报 (补充下载)
    ↓
Phase 2A (处理全部 PDF)
```

---

## 异常处理

| 异常情况 | 处理方式 |
|---------|---------  |
| Tushare Token 无效或未配置 | 全程降级使用 yfinance MCP，标注数据源 |
| Phase 0 PDF 下载失败 | 标注 Warning，跳过 Phase 2，进入无 PDF 模式 |
| Phase 1 Step A 脚本执行失败 | 检查 Python 环境和依赖，提示安装 |
| Phase 1 Tushare 某端点返回空 | 脚本内置 yfinance fallback，标注来源 |
| Phase 1 财报数据不足5年 | 继续执行，在 data_pack 中标注实际覆盖年份 |
| Phase 2 Step A PDF 无法解析 | 跳过 Phase 2，Phase 3 使用降级方案 |
| Phase 2 关键词未命中 | 对应项返回 null，data_pack_report 标注 Warning |
| Phase 3 某因子触发否决 | 按框架规则停止后续因子，输出否决报告 |
| Phase 3 context 接近上限 | 通过 checkpoint 机制已将中间结果持久化到文件 |
| Phase 1 warnings 非空 | Phase 3 读取 warnings 区块，影响分析策略 |

---

## 文件路径约定

每个标的的运行时输出放在独立文件夹中，避免多次分析互相覆盖。

**变量定义**：
- `{workspace}` = 龟龟投资策略_v1.0 根目录
- `{prompts_dir}` = `{workspace}/prompts`
- `{output_dir}` = `{workspace}/output/{代码}_{公司}`（如 `output/600887_伊利股份`、`output/00001_长和`）

```
{workspace}/
├── prompts/                                    ← 策略逻辑（只读，不随标的变化）
│   ├── coordinator.md                          ← 本文件（调度逻辑）
│   ├── phase1_数据采集.md                       ← Phase 1 Step B prompt（WebSearch）
│   ├── phase2_PDF解析.md                        ← Phase 2 Step B prompt（5项精提取）
│   ├── phase3_分析与报告.md                      ← Phase 3 精简执行器
│   └── references/                              ← 因子详细规则（按需加载）
│       ├── factor1_资产质量与商业模式.md
│       ├── factor2_穿透回报率粗算.md
│       ├── factor3_穿透回报率精算.md
│       └── factor4_估值与安全边际.md
├── scripts/                                    ← 预处理脚本（只读，不随标的变化）
│   ├── tushare_collector.py                    ← Phase 1 Step A 数据采集脚本
│   ├── pdf_preprocessor.py                     ← Phase 2 Step A PDF 预处理脚本
│   ├── config.py                               ← Token 管理
│   └── requirements.txt                        ← Python 依赖
└── output/                                     ← 运行时输出（按标的隔离）
    ├── 600887_伊利股份/                          ← 示例：伊利股份
    │   ├── data_pack_market.md                  ← Phase 1 输出
    │   ├── available_fields.json                ← Phase 1 输出（可用字段清单）
    │   ├── 600887_2024_年报.pdf                  ← Phase 0 下载（年报）
    │   ├── 600887_2025_中报.pdf                  ← Phase 0 下载（中报，条件触发）
    │   ├── pdf_sections.json                    ← Phase 2A 输出（年报）
    │   ├── pdf_sections_interim.json            ← Phase 2A 输出（中报，条件触发）
    │   ├── data_pack_report.md                  ← Phase 2B 输出（年报附注）
    │   ├── data_pack_report_interim.md          ← Phase 2B 输出（中报附注，条件触发）
    │   └── 伊利股份_600887_分析报告.md             ← Phase 3 输出（最终报告）
    ├── 00001_长和/                               ← 示例：长和
    │   └── ...
    └── .../
```

**协调器职责**：在 Phase 1 启动前，创建 `{output_dir}` 目录：
```bash
mkdir -p {workspace}/output/{code}_{company}
```

---

*龟龟投资策略 v1.0 | 多阶段 Sub-agent 架构 | Coordinator*
