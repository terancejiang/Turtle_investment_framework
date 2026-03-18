---
name: "download-report"
description: "搜索并下载 A股/港股财报 PDF 到本地。用户要求下载年报/中报/季报或缺少报告 PDF 时调用。"
---

# Download Report（财报下载）

从雪球（`stockn.xueqiu.com`）或同花顺公告（`notice.10jqka.com.cn`）搜索并下载指定股票的财报 PDF，配合 `scripts/download_report.py` 落盘到本地目录。

## 何时使用

- 用户要求“下载年报/中报/一季报/三季报 PDF”
- Turtle 分析流水线需要 PDF，但输出目录中尚未找到对应报告

## 输入解析

将用户输入解析为：

- `stock_code`（必需）：股票代码
- `year`（可选）：默认选择最近可用年份
- `report_type`（可选）：默认“年报”

### 市场识别与代码格式化

- 6 位且以 `6` 开头：上交所 A 股，前缀 `SH`（如 `600887` → `SH600887`）
- 6 位且以 `0`/`3` 开头：深交所 A 股，前缀 `SZ`（如 `300750` → `SZ300750`）
- 1-5 位：港股，左侧补零到 5 位（如 `700` → `00700`）
- 已带 `SH`/`SZ` 前缀：直接使用

### 报告类型映射

- 年报：搜索关键词 `年度报告`（A 股）/ `annual report`（港股）
- 中报：搜索关键词 `半年度报告`（A 股）/ `interim report`（港股）
- 一季报 / 三季报：仅支持 A 股

## 执行步骤

### 1) 搜索 PDF 链接

使用 WebSearch 构造查询：

- A 股年报：`site:stockn.xueqiu.com <formatted_code> 年度报告 <year>`
- A 股中报：`site:stockn.xueqiu.com <formatted_code> 半年度报告 <year>`
- A 股一季报：`site:stockn.xueqiu.com <formatted_code> 第一季度报告 <year>`
- A 股三季报：`site:stockn.xueqiu.com <formatted_code> 第三季度报告 <year>`
- 港股年报：`site:stockn.xueqiu.com <formatted_code> annual report <year>`
- 港股中报：`site:stockn.xueqiu.com <formatted_code> interim report <year>`

若未指定年份：

- 先尝试当前年，再尝试上一年，选择最新匹配结果

若雪球无结果：

- 尝试同花顺：`site:notice.10jqka.com.cn <formatted_code> <search_keyword> <year>`
- 最后再尝试去掉 `site:` 约束

### 2) 过滤并选择正确的 PDF

只接受以下域名的直链 PDF：

- `https://stockn.xueqiu.com/.../*.pdf`
- `https://notice.10jqka.com.cn/.../*.pdf`

排除包含关键词的结果：

摘要、审计报告、公告、利润分配、可持续发展、股东大会、ESG、summary、auditor、dividend、更正、补充、意见、内部控制

优先选择：

- 标题含“年度报告/半年度报告/第一季度报告/第三季度报告”，且不含“摘要”
- URL 或标题体现的日期更接近常规披露时间

### 3) 下载并解析脚本输出

使用脚本下载：

```bash
python3 scripts/download_report.py \
  --url "<PDF_URL>" \
  --stock-code "<formatted_stock_code>" \
  --report-type "<report_type>" \
  --year "<year>" \
  --save-dir "output"
```

脚本输出会在 `---RESULT---` 与 `---END---` 之间给出结构化字段（`status/filepath/filesize/message`）。按结果向用户汇报下载路径与文件大小；失败时给出 `message` 并建议重试或检查参数。

## 反爬绕过（雪球 WAF / 签名限制）

当 WebSearch 无结果或 requests 翻页被拦截时：

1) 优先让用户提供 Cookie（至少 `xq_a_token`、`xq_r_token`）
2) 使用 Playwright 模式自动翻页抓取公告流并解析出年报直链：

```bash
python3 scripts/download_report.py \
  --xueqiu-use-playwright \
  --cookie "<COOKIE>" \
  --stock-code "<formatted_stock_code>" \
  --report-type "<report_type>" \
  --year "<year>" \
  --save-dir "output" \
  --xueqiu-max-pages 200
```

## 输出路径约定

下载落盘在：

- `output/<code>_<company>/<code>_<year>_<report_type>.pdf`

其中 `<code>` 会去掉 `SH`/`SZ`/`HK` 前缀，`<company>` 来自 `--company-name`（可选）。

## 06049 保利物业中报下载问题与解决方案沉淀

### 背景

目标：从雪球公告获取“2025中期报告”PDF，并落地到本地用于财报解析。

### 遇到的问题

1) Playwright 拦截不到 stock_timeline.json  
现象：脚本输出 `No API response intercepted during page load`，仅从页面 HTML 拿到少量 PDF。  
原因：雪球页面出现登录/验证码门槛时，前端不会触发 `stock_timeline.json` 请求。

2) 直接 curl timeline 接口返回 400  
现象：返回 `遇到错误，请刷新页面或者重新登录帐号后再试`。  
原因：缺少有效登录态 Cookie 或 md5__1038 签名失效。

### 解决办法

#### 方案 A（推荐）：复用本机浏览器登录态

用 Chrome 个人资料目录启动 Playwright，避免手动粘贴 Cookie。

```bash
.venv/bin/python scripts/xueqiu_playwright_crawler.py \
  --symbol 06049 \
  --user-data-dir "/Users/chenwei/Library/Application Support/Google/Chrome" \
  --max-pages 30 \
  --timeout 30 \
  --output output/06049_保利物业/xueqiu_profile_try.json
```

结果：成功拦截 timeline，候选列表里出现 2025 中期报告 PDF。

中报 PDF 链接：  
https://stockn.xueqiu.com/06049/20250925504864.pdf

本地落地文件：  
output/06049_保利物业/06049_2025_中报.pdf

#### 方案 B：使用 cookie 文件（避免把 token 写进命令行）

1) 把 Cookie 字符串保存为本地文件（不进 git），如 `~/.xq_cookie.txt`  
2) 运行：

```bash
.venv/bin/python scripts/xueqiu_playwright_crawler.py \
  --symbol 06049 \
  --cookie-file ~/.xq_cookie.txt \
  --max-pages 30 \
  --timeout 30 \
  --output output/06049_保利物业/xueqiu_profile_try.json
```

### 经验要点

- 没有登录态就抓不到公告 timeline，拦截一定为 0
- 中报 PDF 可以直接下载，无需二次签名：只要从 timeline 的候选中解析出 PDF 链接即可
- 避免泄露 Cookie/Token：不要把完整 token 写进项目文件或 shell 历史
