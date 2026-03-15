---
name: "turtle-analysis"
description: "执行龟龟投资策略全流程分析并生成报告。用户提供 A股/港股代码、要求做基本面分析/生成分析报告时调用。"
---

# Turtle Analysis（龟龟投资策略）

对指定股票运行 Turtle Investment Framework（龟龟投资策略）多阶段基本面分析流水线，产出标准化的中间数据包与最终分析报告。

## 何时使用

- 用户让你“跑一遍龟龟投资策略/生成分析报告/做四因子分析”
- 用户提供股票代码（A 股或港股）并希望输出到 `output/` 下
- 需要结合 Tushare 数据、（可选）年报 PDF、以及定性 WebSearch 信息形成完整结论

## 输入

- `stock_code`：A 股（如 `600887`、`000858.SZ`）或港股（如 `00700.HK`）
- 允许只给 6 位数字；代码标准化逻辑在 [config.py](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/scripts/config.py)

## 依赖与环境

- 环境变量：`TUSHARE_TOKEN`（必需）
- Python 依赖：建议通过 `bash init.sh` 创建 `.venv/` 并安装 `requirements.txt`
- 流水线规范入口： [coordinator.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/coordinator.md)

## 执行流程（按阶段）

在执行前先读取 [coordinator.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/coordinator.md) 获取完整规范，然后按以下阶段推进：

### Phase 0（可选）：年报 PDF 获取

- 默认目标：最近 5 年年报（例如当前年份为 2026，则优先获取 2025–2021 年报）
- 若“最近一年年报”尚未披露：
  - A 股：用本年已披露的季报/半年报补齐（Q1/中报/Q3）
  - 港股：用本年已披露的中报补齐（港股通常无季报）
- 去重策略：若 `output/{code}_{company}/` 下已存在对应 `{code}_{year}_年报.pdf`（或同 year 的目标报表），则不重复下载
- 执行方式：优先调用 `download-report` 技能下载；下载失败时继续后续阶段（无 PDF 降级模式）

### Phase 1A：Tushare 数据采集

运行脚本生成市场数据包：

```bash
mkdir -p output/{code}_{company}
python3 scripts/tushare_collector.py --code <stock_code> --output output/{code}_{company}/data_pack_market.md
```

### Phase 1B：定性信息 WebSearch

按 [phase1_数据采集.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/phase1_数据采集.md) 的要求补充：

- 管理层背景
- 行业与竞争格局
- 近期新闻与事件

把结果追加到 `data_pack_market.md` 的对应章节。

### Phase 2A（可选）：PDF 预处理

若 Phase 0 获取到 PDF，则预处理生成结构化切片：

```bash
python3 scripts/pdf_preprocessor.py --pdf output/{code}_{company}/*.pdf --output output/{code}_{company}/pdf_sections.json
```

### Phase 2B（可选）：PDF 结构化抽取

若存在 `pdf_sections.json`，按 [phase2_PDF解析.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/phase2_PDF解析.md) 抽取关键内容并输出：

- `output/{code}_{company}/data_pack_report.md`

### Phase 3：四因子分析与报告生成

按 [phase3_分析与报告.md](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/phase3_分析与报告.md) 生成最终报告，并可按需引用：

- [references](file:///Users/chenwei/GolandProjects/src/github.com/wei1111/Turtle_investment_framework/prompts/references) 下各因子说明文件

输出：

- `output/{code}_{company}/{company}_{code}_分析报告.md`

## 错误处理（必须遵守）

- 任一阶段失败：记录错误并继续后续阶段
- Phase 2 失败：Phase 3 以无 PDF 模式继续
- Phase 1A 失败：尝试脚本内的替代路径（如 yfinance 集成），并用现有数据继续
- 无论数据是否完整：始终产出最终报告（允许标注数据缺口与不确定性）
