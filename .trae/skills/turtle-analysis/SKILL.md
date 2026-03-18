***

name: "turtle-analysis"
description: "执行龟龟投资策略全流程分析并生成报告。用户提供 A股/港股代码、要求做基本面分析/生成分析报告时调用。"
------------------------------------------------------------------

# Turtle Analysis（龟龟投资策略）

对指定股票运行 Turtle Investment Framework（龟龟投资策略）多阶段基本面分析流水线，产出标准化的中间数据包与最终分析报告。

## 何时使用

- 用户让你"跑一遍龟龟投资策略/生成分析报告/做四因子分析"
- 用户提供股票代码（A 股或港股）并希望输出到 `output/` 下
- 需要结合 Tushare 数据、（可选）年报 PDF、以及定性 WebSearch 信息形成完整结论

## 输入

- `stock_code`：A 股（如 `600887`、`000858.SZ`）或港股（如 `00700.HK`）
- 允许只给 6 位数字；代码标准化逻辑在 [config.py](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/scripts/config.py)

## ⚠️ 核心原则：本地优先（MANDATORY）

**这是整个技能最重要的规则，违反此规则将导致分析质量严重下降。**

### 必须使用的本地资源

本项目的所有资源均在以下根目录下：

```
/Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/
```

执行分析时，**必须按以下优先级使用资源**：

| 优先级    | 资源类型      | 本地路径                                                          | 说明                                   |
| ------ | --------- | ------------------------------------------------------------- | ------------------------------------ |
| **P0** | 流程规范      | `prompts/coordinator.md`                                      | **必须首先读取**，它是整个流水线的调度规范              |
| **P0** | 阶段指令      | `prompts/phase1_数据采集.md`、`phase2_PDF解析.md`、`phase3_分析与报告.md`  | 各阶段的详细执行指令                           |
| **P0** | 因子参考      | `prompts/references/factor1~4_*.md`                           | 四因子分析的评分标准与计算公式                      |
| **P1** | 年报 PDF    | `output/{code}_{company}/*.pdf`                               | **已下载的财报是一手数据源，必须优先使用**              |
| **P1** | PDF 解析脚本  | `scripts/pdf_preprocessor.py`、`scripts/pdf_annual_metrics.py` | 用本地脚本解析 PDF，不要自行从网上搜索财务数据            |
| **P1** | 数据采集脚本    | `scripts/tushare_collector.py`                                | 结构化市场数据采集                            |
| **P2** | 已有中间产物    | `output/{code}_{company}/data_pack_*.md`、`pdf_sections*.json` | 若已存在则直接复用，无需重新生成                     |
| **P3** | WebSearch | 外部搜索                                                          | **仅用于补充定性信息**（管理层、行业、新闻），不用于替代本地财务数据 |

### 严禁行为

1. **严禁绕过本地 PDF 直接从网上搜索财务数据**：如果本地已有财报PDF，所有财务数字必须从这些 PDF 中提取，只有本地缺失的财报数据才需要在网上爬取
2. **严禁跳过 coordinator.md**：不读取 coordinator.md 就开始分析等于"没有图纸就施工"
3. **严禁忽略本地脚本**：`pdf_preprocessor.py` 和 `pdf_annual_metrics.py` 是专门为本框架开发的解析工具，必须使用
4. **严禁用 WebSearch 结果替代年报附注数据**：应收账款账龄、关联交易、受限资产等附注数据只能从 PDF 中提取
5. **严禁自行编造分析框架**：四因子的评分标准、计算公式、报告模板均在 `prompts/` 和 `references/` 中定义，必须遵循

## 依赖与环境

- 环境变量：`TUSHARE_TOKEN`（必需；未配置时降级使用 yfinance）
- Python 依赖：建议通过 `bash init.sh` 创建 `.venv/` 并安装 `requirements.txt`
- 流水线规范入口：[coordinator.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/coordinator.md)

## 执行流程（按阶段）

### Step 0：读取规范（MANDATORY — 不可跳过）

在执行任何分析之前，**必须先读取以下文件**：

1. [coordinator.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/coordinator.md) — 获取完整的阶段调度规范、变量定义、异常处理策略
2. 检查 `output/{code}_{company}/` 目录是否已存在：
   - 已有 PDF → 跳过 Phase 0
   - 已有 `data_pack_market.md` → 检查是否需要更新（Phase 1A 可复用）
   - 已有 `pdf_sections*.json` → Phase 2A 可跳过
   - 已有 `data_pack_report.md` → Phase 2B 可跳过

### Phase 0（条件触发）：年报 PDF 获取

- 默认目标：最近 5 年年报（例如当前年份为 2026，则优先获取 2025–2021 年报）
- **去重策略**：若 `output/{code}_{company}/` 下已存在对应 `{code}_{year}_年报.pdf`，则**不重复下载**
- 若当前时间到最近的年报中间有季报/半年报/中报也需要获取（入26年2月还没有25年年报，可以用25年季报/半年报补齐）：
  - A 股：用本年已披露的季报/半年报补齐（Q1/中报/Q3）
  - 港股：用本年已披露的中报补齐（港股通常无季报）
- 执行方式：优先调用 `download-report` 技能下载；下载失败时继续后续阶段（无 PDF 降级模式）

### Phase 1A：Tushare 数据采集

运行脚本生成市场数据包：

```bash
cd /Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework
mkdir -p output/{code}_{company}
python3 scripts/tushare_collector.py --code <stock_code> --output output/{code}_{company}/data_pack_market.md
```

若 `data_pack_market.md` 已存在且内容完整（含 §1-§6 等章节），可直接复用。

### Phase 1B：定性信息 WebSearch

按 [phase1\_数据采集.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/phase1_数据采集.md) 的要求补充：

- 管理层背景（§7）
- 行业与竞争格局（§8）
- MD\&A 摘要（§10，优先从 pdf\_sections.json 的 MDA 字段获取）
- 风险提示（§13）

**注意**：WebSearch 仅用于补充定性信息，不用于获取财务数字。

### Phase 2A（条件触发）：PDF 预处理

若 `output/{code}_{company}/` 下存在 PDF 但尚无对应的 `pdf_sections*.json`：

```bash
cd /Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework
python3 scripts/pdf_preprocessor.py --pdf output/{code}_{company}/{code}_{year}_年报.pdf --output output/{code}_{company}/pdf_sections_{year}.json
```

**对每一年的 PDF 分别运行**，生成各年的 `pdf_sections_{year}.json`。

还可用 `pdf_annual_metrics.py` 提取关键财务指标：

```bash
python3 scripts/pdf_annual_metrics.py --pdf-dir output/{code}_{company}/ --code {code} --years 2020,2021,2022,2023,2024
```

### Phase 2B（条件触发）：PDF 结构化抽取

若存在 `pdf_sections*.json`，按 [phase2\_PDF解析.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/phase2_PDF解析.md) 抽取关键附注内容并输出：

- `output/{code}_{company}/data_pack_report.md`

### Phase 3：四因子分析与报告生成

按 [phase3\_分析与报告.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/phase3_分析与报告.md) 生成最终报告。

**必须按需读取因子参考文件**：

- [factor1\_资产质量与商业模式.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/references/factor1_资产质量与商业模式.md)
- [factor2\_穿透回报率粗算.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/references/factor2_穿透回报率粗算.md)
- [factor3\_穿透回报率精算.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/references/factor3_穿透回报率精算.md)
- [factor4\_估值与安全边际.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/references/factor4_估值与安全边际.md)

输出：

- `output/{code}_{company}/{company}_{code}_分析报告.md`

## 已有中间产物的复用规则

为避免重复工作，执行前必须检查 `output/{code}_{company}/` 下的已有文件：

| 文件                    | 存在时的处理                        | 不存在时的处理       |
| --------------------- | ----------------------------- | ------------- |
| `*.pdf`               | 跳过 Phase 0 对应年份的下载            | 触发 Phase 0 下载 |
| `data_pack_market.md` | 检查完整性，完整则复用；仅补充缺失章节           | 运行 Phase 1A   |
| `pdf_sections*.json`  | 跳过 Phase 2A 对应年份的预处理          | 运行 Phase 2A   |
| `data_pack_report.md` | 跳过 Phase 2B                   | 运行 Phase 2B   |
| `*_分析报告.md`           | **仍然重新生成**（用户显式要求分析意味着需要最新报告） | 运行 Phase 3    |

## 错误处理（必须遵守）

- 任一阶段失败：记录错误并继续后续阶段
- Phase 2 失败：Phase 3 以无 PDF 模式继续（但需在报告中标注降级）
- Phase 1A 失败：尝试脚本内的替代路径（如 yfinance 集成），并用现有数据继续
- 无论数据是否完整：始终产出最终报告（允许标注数据缺口与不确定性）

## 常见错误模式（AI 必读）

以下是过去执行中出现过的错误，**必须避免**：

1. **❌ 不读 coordinator.md 就开始分析** → 导致遗漏阶段、格式不符、计算公式错误
2. **❌ 忽略本地已有 PDF，从 Yahoo Finance / 东方财富网搜索财务数据** → 数据精度下降，缺少附注细节（账龄、关联交易等）
3. **❌ 不使用 pdf\_preprocessor.py，手动从网上拼凑数据** → 失去结构化切片能力，无法提取 P2-P13 附注
4. **❌ 不读 references/ 下的因子参考文件，自行编造评分标准** → 评分体系与框架不一致
5. **❌ 用 WebSearch 结果替代年报中的精确数字** → 二手数据可能有时滞、四舍五入、口径不一致等问题

