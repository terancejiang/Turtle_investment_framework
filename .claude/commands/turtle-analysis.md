Run a full Turtle Investment Framework (龟龟投资策略) analysis on stock: $ARGUMENTS

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ) or HK stock (00700.HK)
- If $ARGUMENTS is empty or invalid, ask the user for a valid stock code before proceeding
- If only digits are given, the code will be normalized by scripts/config.py

## Execution Instructions

Read prompts/coordinator.md for the full pipeline specification, then execute each phase:

### Phase 0: PDF Acquisition (conditional)
- Default: use the last 5 years annual reports (年报)
- If the most recent annual report is not available yet:
  - A-share: use current-year Q1/Interim/Q3 reports as substitutes
  - HK: use current-year interim report as substitute
- Skip downloads that already exist under output/{code}_{company}/
- If missing, use /download-report command (scripts/download_report.py) to search and download
- If download fails, proceed without PDF (graceful degradation)

### Phase 1A: Tushare Data Collection (Python script)
```bash
mkdir -p output/{code}_{company}
python3 scripts/tushare_collector.py --code $ARGUMENTS --output output/{code}_{company}/data_pack_market.md
```

### Phase 1B: WebSearch Qualitative Data (Agent)
- Read prompts/phase1_数据采集.md for WebSearch instructions
- Collect: management background, industry analysis, competitive landscape, recent news
- Append results to data_pack_market.md sections §8, §9B, §10

### Phase 2A: PDF Preprocessing (Python script, skip if no PDF)
```bash
python3 scripts/pdf_preprocessor.py --pdf output/{code}_{company}/*.pdf --output output/{code}_{company}/pdf_sections.json
```

### Phase 2B: PDF Structured Extraction (Agent, skip if no PDF)
- Read prompts/phase2_PDF解析.md for extraction instructions
- Extract P2/P3/P4/P6/P13 + MDA + SUB from pdf_sections.json
- Output: output/{code}_{company}/data_pack_report.md

### Phase 3: 4-Factor Analysis and Report (Agent)
- Read prompts/phase3_分析与报告.md for analysis framework
- Load references/ factor files as needed
- Output: output/{code}_{company}/{company}_{code}_分析报告.md

## Error Recovery
- If any phase fails, log the error and continue with remaining phases
- Phase 2 failure → Phase 3 runs in no-PDF degraded mode
- Phase 1A failure → attempt yfinance fallback, then proceed with available data
- Always produce a final report even if partial data

## Output
Final report: output/{code}_{company}/{company}_{code}_分析报告.md

Usage: /turtle-analysis 600887 or /turtle-analysis 00700.HK
