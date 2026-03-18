#!/usr/bin/env python3
"""
财报PDF下载工具 (Financial Report PDF Downloader)

从 stockn.xueqiu.com 或 notice.10jqka.com.cn 下载A股/港股财报PDF文件。
支持年报、中报、一季报、三季报。

Usage:
    python3 scripts/download_report.py \
        --url "https://stockn.xueqiu.com/.../report.pdf" \
        --stock-code SH600887 \
        --report-type 年报 \
        --year 2024 \
        --save-dir output

    python3 scripts/download_report.py \
        --xueqiu-timeline-url "https://xueqiu.com/statuses/stock_timeline.json?...&md5__1038=..." \
        --cookie "xq_a_token=...; xq_r_token=...; u=...; xq_is_login=1" \
        --stock-code 06049 \
        --report-type 年报 \
        --year 2024 \
        --save-dir output
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

import requests

# Exit codes
EXIT_SUCCESS = 0
EXIT_NETWORK_FAILURE = 1
EXIT_PDF_VALIDATION_FAILURE = 2
EXIT_BAD_ARGUMENTS = 3

# Constants
PDF_MAGIC_BYTES = b"%PDF-"
MIN_FILE_SIZE_WARNING = 100 * 1024  # 100KB
DOWNLOAD_TIMEOUT = 120
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE = 3  # seconds

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

URL_PATTERN = re.compile(
    r"^https?://(stockn\.xueqiu\.com|[\w.-]*10jqka\.com\.cn)/.+\.pdf$",
    re.IGNORECASE,
)

XUEQIU_PDF_URL_PATTERN = re.compile(
    r"https?://stockn\.xueqiu\.com/[^\s\"'<>]+?\.pdf", re.IGNORECASE
)


def get_headers(url):
    """Return headers with Referer matching the URL domain."""
    headers = dict(BASE_HEADERS)
    if "10jqka.com.cn" in url:
        headers["Referer"] = "https://10jqka.com.cn/"
    else:
        headers["Referer"] = "https://xueqiu.com/"
    return headers


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Download financial report PDF from stockn.xueqiu.com or 10jqka.com.cn"
    )
    parser.add_argument(
        "--url", help="PDF URL from stockn.xueqiu.com or 10jqka.com.cn"
    )
    parser.add_argument(
        "--xueqiu-timeline-url",
        help="Signed timeline url copied from browser (statuses/stock_timeline.json)",
    )
    parser.add_argument(
        "--cookie", help="Cookie string copied from browser"
    )
    parser.add_argument(
        "--stock-code", required=True, help="Stock code (e.g. SH600887, 00700)"
    )
    parser.add_argument(
        "--report-type",
        required=True,
        help="Report type (年报/中报/一季报/三季报/annual/interim)",
    )
    parser.add_argument(
        "--year", required=True, help="Report year (e.g. 2024)"
    )
    parser.add_argument(
        "--save-dir",
        default="output",
        help="Directory to save the PDF (default: output)",
    )
    parser.add_argument(
        "--company-name",
        help="Company name used in output folder (e.g. 保利物业)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Max download retries (default: {DEFAULT_MAX_RETRIES})",
    )
    parser.add_argument(
        "--xueqiu-max-pages",
        type=int,
        default=50,
        help="Max pages when crawling Xueqiu timeline (default: 50)",
    )
    parser.add_argument(
        "--xueqiu-count",
        type=int,
        default=50,
        help="Items per page when crawling Xueqiu timeline (default: 50)",
    )
    parser.add_argument(
        "--xueqiu-timeout",
        type=int,
        default=20,
        help="Timeout seconds for Xueqiu crawling requests (default: 20)",
    )
    parser.add_argument(
        "--xueqiu-source",
        default="公告",
        help="Timeline source for Xueqiu crawling (default: 公告)",
    )
    parser.add_argument(
        "--xueqiu-use-playwright",
        action="store_true",
        help="Use Playwright to crawl Xueqiu timeline (fallback for WAF)",
    )
    return parser.parse_args(argv)


def validate_url(url):
    """Validate that the URL points to a supported source and ends with .pdf."""
    if not URL_PATTERN.match(url):
        return False, (
            f"Invalid URL: {url}\n"
            "URL must be a .pdf link from stockn.xueqiu.com or 10jqka.com.cn"
        )
    return True, ""


def build_filename(stock_code, report_type, year):
    """Build output filename: {code}_{year}_{report_type}.pdf

    Strips SH/SZ prefix from stock_code to match coordinator.md convention
    (e.g. 600887_2024_年报.pdf).
    """
    # Normalize report type
    type_map = {
        "annual": "年报",
        "interim": "中报",
        "q1": "一季报",
        "q3": "三季报",
    }
    normalized = type_map.get(report_type.lower(), report_type)
    # Strip exchange prefix for filename
    code = re.sub(r"^(SH|SZ|HK)", "", stock_code, flags=re.IGNORECASE)
    return f"{code}_{year}_{normalized}.pdf"


def sanitize_path_component(name):
    s = (name or "").strip()
    if not s:
        return ""
    s = re.sub(r"[\\\\/:*?\"<>|]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_save_path(save_dir, stock_code, report_type, year, company_name=None):
    code = re.sub(r"^(SH|SZ|HK)", "", stock_code, flags=re.IGNORECASE)
    filename = build_filename(stock_code, report_type, year)
    company = sanitize_path_component(company_name)
    folder = "{}_{}".format(code, company) if company else code
    target_dir = os.path.join(save_dir, folder)
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, filename)


def parse_cookie_string(cookie_string):
    cookies = {}
    if not cookie_string:
        return cookies
    parts = [p.strip() for p in cookie_string.split(";") if p.strip()]
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


def parse_timeline_url(url):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid xueqiu timeline url")
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return base_url, params


def response_is_json(resp):
    content_type = resp.headers.get("Content-Type", "")
    if "json" in content_type.lower():
        return True
    return resp.text.lstrip().startswith("{")


def score_timeline_entry(text, year, report_type):
    if not text:
        return -10
    t = text.lower()
    y = str(year).lower()
    score = 0
    if y and y in t:
        score += 10
    exclude = [
        "摘要",
        "审计报告",
        "公告",
        "利润分配",
        "可持续发展",
        "股东大会",
        "esg",
        "summary",
        "auditor",
        "dividend",
        "更正",
        "补充",
        "意见",
        "内部控制",
        "业绩公告",
        "结果",
        "董事",
        "委任",
        "更换",
    ]
    for kw in exclude:
        if kw.lower() in t:
            score -= 2
    include = []
    normalized = report_type.strip().lower()
    if normalized in {"年报", "annual"}:
        include = ["年度报告", "年报", "annual report"]
    elif normalized in {"中报", "interim"}:
        include = ["半年度报告", "中期报告", "中报", "interim report"]
    elif normalized in {"一季报", "q1"}:
        include = ["第一季度报告", "一季报"]
    elif normalized in {"三季报", "q3"}:
        include = ["第三季度报告", "三季报"]
    for kw in include:
        if kw.lower() in t:
            score += 8
    return score


def extract_pdf_urls_from_entry(entry):
    urls = set()
    if not isinstance(entry, dict):
        return urls
    for field in ("text", "description"):
        value = entry.get(field) or ""
        if isinstance(value, str):
            urls.update(XUEQIU_PDF_URL_PATTERN.findall(value))
    quote_cards = entry.get("quote_cards") or []
    if isinstance(quote_cards, list):
        for card in quote_cards:
            if isinstance(card, dict):
                target = card.get("target_url") or ""
                if isinstance(target, str) and target.lower().endswith(".pdf"):
                    if "stockn.xueqiu.com" in target.lower():
                        urls.add(target)
    return urls


def resolve_pdf_url_from_xueqiu_timeline(
    stock_code,
    report_type,
    year,
    timeline_url,
    cookie_string,
    max_pages,
    count,
    timeout,
    source,
):
    base_url, params = parse_timeline_url(timeline_url)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": BASE_HEADERS["User-Agent"],
            "Accept": "*/*",
            "Accept-Language": BASE_HEADERS["Accept-Language"],
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://xueqiu.com/S/{re.sub(r'^(SH|SZ)', '', stock_code, flags=re.IGNORECASE)}",
        }
    )
    session.cookies.update(parse_cookie_string(cookie_string))

    symbol_id = re.sub(r"^(SH|SZ|HK)", "", stock_code, flags=re.IGNORECASE)
    params.setdefault("source", source)
    if "symbol_id" in params:
        params["symbol_id"] = symbol_id
    elif "symbol" in params:
        params["symbol"] = stock_code.upper()
    else:
        params["symbol_id"] = symbol_id

    best = None
    best_score = -10_000
    fallback = []
    year_str = str(year)

    for page in range(1, max_pages + 1):
        page_params = dict(params)
        page_params["page"] = page
        if count:
            page_params["count"] = count
        resp = session.get(base_url, params=page_params, timeout=timeout)
        resp.raise_for_status()
        if not response_is_json(resp):
            break
        data = resp.json()
        items = data.get("list") if isinstance(data, dict) else None
        if not items:
            break
        found_any = False
        for entry in items:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text") or entry.get("description") or ""
            urls = sorted(extract_pdf_urls_from_entry(entry))
            if not urls:
                continue
            found_any = True
            score = score_timeline_entry(text, year, report_type)
            created_at = entry.get("created_at") or 0
            key = (score, created_at)
            if best is None or key > (best_score, best.get("created_at") or 0):
                best_score = score
                best = {
                    "url": urls[0],
                    "created_at": created_at,
                    "text": text,
                    "score": score,
                }
            if year_str:
                for url in urls:
                    if year_str in url:
                        fallback.append((created_at, url, text))
        if not found_any and page >= 2:
            break

    if not best or best_score < 8:
        if fallback:
            fallback.sort(key=lambda item: item[0], reverse=True)
            return True, fallback[0][1], "Resolved PDF URL by year match"
        return False, "", "No matching PDF found from Xueqiu timeline"
    return True, best["url"], "Resolved PDF URL from Xueqiu timeline"


def normalize_symbol_for_playwright(stock_code):
    s = (stock_code or "").strip().upper()
    if s.startswith(("SH", "SZ", "HK")):
        return s
    digits = re.sub(r"\D", "", s)
    if len(digits) <= 5:
        return "HK{}".format(digits.zfill(5))
    if len(digits) == 6:
        return "SH{}".format(digits)
    return s


def resolve_pdf_url_from_xueqiu_playwright(
    stock_code,
    report_type,
    year,
    timeline_url,
    cookie_string,
    max_pages,
    timeout,
):
    try:
        import argparse as _argparse
        from xueqiu_playwright_crawler import (
            crawl_timeline_with_playwright_fetch,
            crawl_with_playwright,
            normalize_symbol,
        )
    except Exception as exc:
        return False, "", "Playwright crawler unavailable: {}".format(exc)

    symbol = normalize_symbol(stock_code, None)
    args = _argparse.Namespace(
        symbol=symbol,
        market=None,
        max_pages=max_pages,
        timeout=timeout,
        wait_between_pages=0.5,
        cookie=cookie_string,
        user_data_dir=None,
        headed=False,
        output=None,
        timeline_url=timeline_url,
    )
    try:
        if timeline_url:
            _, _, candidates = crawl_timeline_with_playwright_fetch(symbol, args)
        else:
            _, _, candidates = crawl_with_playwright(symbol, args)
    except Exception as exc:
        return False, "", "Playwright crawl failed: {}".format(exc)

    best_url = ""
    best_key = (-10_000, -1)
    fallback = []
    year_str = str(year)

    for item in candidates or []:
        text = item.get("text") if isinstance(item, dict) else ""
        created_at = item.get("created_at") if isinstance(item, dict) else 0
        pdfs = item.get("pdf_urls") if isinstance(item, dict) else None
        if not pdfs:
            continue
        sc = score_timeline_entry(text or "", year, report_type)
        key = (sc, created_at or 0)
        if key > best_key:
            best_key = key
            best_url = pdfs[0]
        for u in pdfs:
            if year_str and year_str in u:
                fallback.append((created_at or 0, u))

    if best_url and best_key[0] >= 8:
        return True, best_url, "Resolved PDF URL from Xueqiu playwright"
    if fallback:
        fallback.sort(key=lambda x: x[0], reverse=True)
        return True, fallback[0][1], "Resolved PDF URL from Xueqiu playwright by year match"
    return False, "", "No matching PDF found from Xueqiu playwright"


def download_annual_report(url, save_path, max_retries=DEFAULT_MAX_RETRIES):
    """
    Download PDF with retry and validation.

    Returns:
        tuple: (success: bool, message: str, filesize: int)
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            print(
                f"Downloading (attempt {attempt}/{max_retries}): {url}",
                file=sys.stderr,
            )

            response = requests.get(
                url,
                headers=get_headers(url),
                timeout=DOWNLOAD_TIMEOUT,
                stream=True,
            )
            response.raise_for_status()

            # Check Content-Type
            content_type = response.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
                print(
                    f"Warning: Content-Type is '{content_type}', expected PDF",
                    file=sys.stderr,
                )

            # Download to temporary path first, then rename
            tmp_path = save_path + ".tmp"
            total_size = 0
            first_chunk = True

            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        # Validate PDF magic bytes on first chunk
                        if first_chunk:
                            if not chunk[:5].startswith(PDF_MAGIC_BYTES):
                                os.remove(tmp_path)
                                return (
                                    False,
                                    "PDF validation failed: file does not start with %PDF- magic bytes",
                                    0,
                                )
                            first_chunk = False
                        f.write(chunk)
                        total_size += len(chunk)

            # Rename tmp to final
            if os.path.exists(save_path):
                os.remove(save_path)
            os.rename(tmp_path, save_path)

            # Size warning
            if total_size < MIN_FILE_SIZE_WARNING:
                print(
                    f"Warning: file size ({total_size} bytes) is smaller than expected (<100KB)",
                    file=sys.stderr,
                )

            return True, "Download successful", total_size

        except requests.exceptions.RequestException as e:
            last_error = str(e)
            print(
                f"Attempt {attempt} failed: {last_error}", file=sys.stderr
            )
            # Clean up partial download
            tmp_path = save_path + ".tmp"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            if attempt < max_retries:
                wait_time = BACKOFF_BASE * attempt  # 3s, 6s, 9s
                print(f"Retrying in {wait_time}s...", file=sys.stderr)
                time.sleep(wait_time)

    return False, f"Download failed after {max_retries} attempts: {last_error}", 0


def print_result(success, filepath="", filesize=0, url="", stock_code="",
                 report_type="", year="", message=""):
    """Print structured result block for Claude to parse."""
    status = "SUCCESS" if success else "FAILED"
    print("\n---RESULT---")
    print(f"status: {status}")
    print(f"filepath: {filepath}")
    print(f"filesize: {filesize}")
    print(f"url: {url}")
    print(f"stock_code: {stock_code}")
    print(f"report_type: {report_type}")
    print(f"year: {year}")
    print(f"message: {message}")
    print("---END---")


def main(argv=None):
    args = parse_args(argv)

    resolved_url = args.url
    if not resolved_url:
        if not args.cookie:
            err_msg = "Missing --url. Provide --url or --cookie with Xueqiu crawling."
            print(f"Error: {err_msg}", file=sys.stderr)
            print_result(
                success=False,
                url="",
                stock_code=args.stock_code,
                report_type=args.report_type,
                year=args.year,
                message=err_msg,
            )
            sys.exit(EXIT_BAD_ARGUMENTS)
        try:
            ok, resolved_url, msg = (False, "", "")
            if args.xueqiu_timeline_url:
                ok, resolved_url, msg = resolve_pdf_url_from_xueqiu_timeline(
                    stock_code=args.stock_code,
                    report_type=args.report_type,
                    year=args.year,
                    timeline_url=args.xueqiu_timeline_url,
                    cookie_string=args.cookie,
                    max_pages=args.xueqiu_max_pages,
                    count=args.xueqiu_count,
                    timeout=args.xueqiu_timeout,
                    source=args.xueqiu_source,
                )
        except (ValueError, requests.RequestException) as exc:
            err_msg = f"Xueqiu crawl failed: {exc}"
            print(f"Error: {err_msg}", file=sys.stderr)
            print_result(
                success=False,
                url="",
                stock_code=args.stock_code,
                report_type=args.report_type,
                year=args.year,
                message=err_msg,
            )
            sys.exit(EXIT_NETWORK_FAILURE)
        if not ok and args.xueqiu_use_playwright:
            ok, resolved_url, msg = resolve_pdf_url_from_xueqiu_playwright(
                stock_code=args.stock_code,
                report_type=args.report_type,
                year=args.year,
                timeline_url=args.xueqiu_timeline_url,
                cookie_string=args.cookie,
                max_pages=args.xueqiu_max_pages,
                timeout=max(args.xueqiu_timeout, 30),
            )
        if not ok:
            print(f"Error: {msg}", file=sys.stderr)
            print_result(
                success=False,
                url="",
                stock_code=args.stock_code,
                report_type=args.report_type,
                year=args.year,
                message=msg,
            )
            sys.exit(EXIT_NETWORK_FAILURE)

    valid, err_msg = validate_url(resolved_url)
    if not valid:
        print(f"Error: {err_msg}", file=sys.stderr)
        print_result(
            success=False,
            url=resolved_url,
            stock_code=args.stock_code,
            report_type=args.report_type,
            year=args.year,
            message=err_msg,
        )
        sys.exit(EXIT_BAD_ARGUMENTS)

    # Ensure save directory exists
    save_path = build_save_path(
        args.save_dir,
        args.stock_code,
        args.report_type,
        args.year,
        args.company_name,
    )
    # Download
    success, message, filesize = download_annual_report(
        url=resolved_url,
        save_path=save_path,
        max_retries=args.max_retries,
    )

    # Print result
    print_result(
        success=success,
        filepath=os.path.abspath(save_path) if success else "",
        filesize=filesize,
        url=resolved_url,
        stock_code=args.stock_code,
        report_type=args.report_type,
        year=args.year,
        message=message,
    )

    if not success:
        if "validation" in message.lower():
            sys.exit(EXIT_PDF_VALIDATION_FAILURE)
        else:
            sys.exit(EXIT_NETWORK_FAILURE)

    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
