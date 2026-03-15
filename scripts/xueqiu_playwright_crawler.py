#!/usr/bin/env python3
"""
Xueqiu Announcement PDF Link Crawler - Playwright Automation

Core principle:
  Uses Playwright to open the Xueqiu stock announcement page. The browser
  executes Xueqiu's own frontend JS which auto-generates the md5__1038
  signature for every API request. We intercept stock_timeline.json
  responses via page.on("response") and extract PDF links.

  No signature reverse-engineering needed. No manual curl required.

Install:
  pip install playwright
  playwright install chromium

Usage:
  python3 xueqiu_playwright_crawler.py --symbol 06049
  python3 xueqiu_playwright_crawler.py --symbol 06049 --headed
  python3 xueqiu_playwright_crawler.py --symbol 600887 --market SH
  python3 xueqiu_playwright_crawler.py --symbol 06049 --max-pages 50
  python3 xueqiu_playwright_crawler.py --symbol 06049 --cookie "xq_a_token=xxx; ..."
  python3 xueqiu_playwright_crawler.py --symbol 06049 --output result.json
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

EXIT_SUCCESS = 0
EXIT_NETWORK_FAILURE = 1
EXIT_BAD_ARGUMENTS = 2
EXIT_DEPENDENCY_MISSING = 3

XUEQIU_PDF_URL_PATTERN = re.compile(
    r"https?://stockn\.xueqiu\.com/[^\s\"'<>]+?\.pdf", re.IGNORECASE
)

MAX_CONSECUTIVE_NO_NEW = 5


def check_playwright():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        print(
            "Error: playwright is not installed.\n"
            "Install with:\n"
            "  pip install playwright\n"
            "  playwright install chromium\n",
            file=sys.stderr,
        )
        return False


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Crawl Xueqiu announcement PDF links via Playwright (auto-signature)"
    )
    parser.add_argument(
        "--url",
        help="Xueqiu stock page url, e.g. https://xueqiu.com/S/06049",
    )
    parser.add_argument(
        "--symbol",
        help="Stock symbol, e.g. 06049 / SH600887 / HK06049",
    )
    parser.add_argument(
        "--market", choices=["HK", "SH", "SZ"],
        help="Market prefix for numeric-only symbol",
    )
    parser.add_argument(
        "--max-pages", type=int, default=10,
        help="Max pages to auto-paginate (default: 10)",
    )
    parser.add_argument(
        "--timeout", type=int, default=30,
        help="Page load timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--wait-between-pages", type=float, default=1.5,
        help="Wait seconds between pagination actions (default: 1.5)",
    )
    parser.add_argument(
        "--cookie",
        help="Cookie string from browser (optional, for logged-in state)",
    )
    parser.add_argument(
        "--timeline-url",
        help="Signed stock_timeline.json url copied from browser (optional)",
    )
    parser.add_argument(
        "--user-data-dir",
        help="Path to Chrome user data dir to reuse login state",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Run browser in headed mode (visible, for debugging)",
    )
    parser.add_argument(
        "--output",
        help="Write result JSON to file",
    )
    return parser.parse_args(argv)


def extract_symbol_from_url(url):
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    for i, part in enumerate(parts):
        if part.upper() == "S" and i + 1 < len(parts):
            return parts[i + 1]
    if parts:
        return parts[-1]
    raise ValueError("Invalid Xueqiu URL: cannot parse symbol")



def normalize_symbol(raw_symbol, market=None):
    if not raw_symbol:
        raise ValueError("Empty symbol")
    symbol = raw_symbol.strip().upper()
    if symbol.startswith(("SH", "SZ", "HK")):
        return symbol
    if symbol.isdigit():
        if len(symbol) == 5:
            return "{}{}".format(market or "HK", symbol)
        if len(symbol) == 6:
            return "{}{}".format(market or "SH", symbol)
    return symbol


def build_symbol_id(symbol):
    s = symbol.strip().upper()
    if s.startswith(("SH", "SZ", "HK")):
        return s[2:]
    return s


def build_stock_page_url(symbol):
    """Build the Xueqiu stock page URL that loads announcements."""
    raw = symbol.strip().upper()
    # HK stocks: /S/06049  A-shares: /S/SH600887
    if raw.startswith("HK"):
        url_symbol = raw[2:]
    else:
        url_symbol = raw
    return "https://xueqiu.com/S/{}".format(url_symbol)


def parse_cookie_string(cookie_string):
    cookies = []
    if not cookie_string:
        return cookies
    parts = [p.strip() for p in cookie_string.split(";") if p.strip()]
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies.append({
            "name": key.strip(),
            "value": value.strip(),
            "domain": ".xueqiu.com",
            "path": "/",
        })
    return cookies


def find_pdf_links_in_object(obj):
    urls = set()
    if isinstance(obj, str):
        urls.update(XUEQIU_PDF_URL_PATTERN.findall(obj))
    elif isinstance(obj, dict):
        for value in obj.values():
            urls.update(find_pdf_links_in_object(value))
    elif isinstance(obj, list):
        for value in obj:
            urls.update(find_pdf_links_in_object(value))
    return urls


def extract_candidates_from_items(items):
    candidates = []
    if not items:
        return candidates
    for entry in items:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text") or entry.get("description") or ""
        entry_pdfs = find_pdf_links_in_object(entry)
        if not entry_pdfs:
            continue
        created_at = entry.get("created_at") or 0
        candidates.append({
            "created_at": created_at,
            "text": text,
            "pdf_urls": sorted(entry_pdfs),
        })
    return candidates


def parse_timeline_url(timeline_url):
    parsed = urlparse(timeline_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid timeline url")
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    base_url = "{}://{}{}".format(parsed.scheme, parsed.netloc, parsed.path)
    return base_url, params


def crawl_timeline_with_playwright_fetch(symbol, args):
    from playwright.sync_api import sync_playwright

    base_url, base_params = parse_timeline_url(args.timeline_url)

    pdf_urls = set()
    api_responses = []
    pdf_candidates = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            ),
        )
        if args.cookie:
            cks = parse_cookie_string(args.cookie)
            if cks:
                context.add_cookies(cks)
                print("  Injected {} cookies".format(len(cks)), file=sys.stderr)
        pg = context.new_page()
        pg.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        pg.goto("https://xueqiu.com/", wait_until="domcontentloaded", timeout=args.timeout * 1000)

        symbol_id = build_symbol_id(symbol)

        for page_num in range(1, args.max_pages + 1):
            params = dict(base_params)
            params["page"] = str(page_num)
            if "symbol_id" in params:
                params["symbol_id"] = symbol_id
            if "symbol" in params:
                params["symbol"] = symbol

            result = pg.evaluate(
                """async ({baseUrl, params}) => {
                    const u = new URL(baseUrl);
                    Object.entries(params).forEach(([k,v]) => u.searchParams.set(k, v));
                    const r = await fetch(u.toString(), {credentials: 'include'});
                    const ct = r.headers.get('content-type') || '';
                    const text = await r.text();
                    return {status: r.status, ct, url: u.toString(), text};
                }""",
                {"baseUrl": base_url, "params": params},
            )

            if not isinstance(result, dict):
                break
            status = int(result.get("status") or 0)
            ct = str(result.get("ct") or "")
            text = str(result.get("text") or "")

            if status != 200:
                api_responses.append({"page": page_num, "status": status, "pdf_count": 0, "items": 0})
                if page_num > 1:
                    break
                continue
            if "json" not in ct.lower() and not text.lstrip().startswith("{"):
                api_responses.append({"page": page_num, "status": status, "pdf_count": 0, "items": 0})
                if page_num > 1:
                    break
                continue

            body = json.loads(text)
            found = find_pdf_links_in_object(body)
            pdf_urls.update(found)
            items = body.get("list") if isinstance(body, dict) else []
            pdf_candidates.extend(extract_candidates_from_items(items))
            item_count = len(items) if isinstance(items, list) else 0
            api_responses.append({"page": page_num, "status": status, "pdf_count": len(found), "items": item_count})
            print(
                "  [fetch] page {}: {} items, {} PDFs (total: {})".format(
                    page_num, item_count, len(found), len(pdf_urls)
                ),
                file=sys.stderr,
            )
            if item_count == 0 and page_num > 1:
                break
            if args.wait_between_pages > 0:
                time.sleep(args.wait_between_pages)

        browser.close()

    return sorted(pdf_urls), api_responses, pdf_candidates


def crawl_with_playwright(symbol, args):
    """
    Single unified crawling path:
    1. Open the stock page in Playwright
    2. Xueqiu's own JS generates signatures and fires API requests
    3. We intercept every stock_timeline.json response
    4. We trigger pagination (scroll / click) to get more pages
    5. Each pagination triggers Xueqiu JS to generate a NEW valid signature
    """
    from playwright.sync_api import sync_playwright

    stock_url = build_stock_page_url(symbol)
    pdf_urls = set()
    api_responses = []
    pdf_candidates = []
    timeline_response_count = 0
    last_intercept_time = [time.time()]  # mutable for closure

    def handle_response(response):
        nonlocal timeline_response_count
        url = response.url
        if "stock_timeline.json" not in url:
            return
        try:
            if response.status == 200:
                body = response.json()
                timeline_response_count += 1
                last_intercept_time[0] = time.time()
                found = find_pdf_links_in_object(body)
                pdf_urls.update(found)
                items = []
                if isinstance(body, dict):
                    items = body.get("list") or []
                pdf_candidates.extend(extract_candidates_from_items(items))
                item_count = len(items) if isinstance(items, list) else 0
                api_responses.append({
                    "page": timeline_response_count,
                    "status": response.status,
                    "pdf_count": len(found),
                    "items": item_count,
                })
                print(
                    "  [intercept] #{}: {} items, {} PDFs (total: {})".format(
                        timeline_response_count, item_count,
                        len(found), len(pdf_urls)
                    ),
                    file=sys.stderr,
                )
            else:
                print(
                    "  [warn] stock_timeline.json status {}".format(
                        response.status
                    ),
                    file=sys.stderr,
                )
        except Exception as e:
            print(
                "  [error] parse response: {}".format(e),
                file=sys.stderr,
            )

    with sync_playwright() as p:
        ua_string = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        )
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]

        if args.user_data_dir:
            expanded_dir = os.path.expanduser(args.user_data_dir)
            context = p.chromium.launch_persistent_context(
                expanded_dir,
                headless=not args.headed,
                args=launch_args,
                viewport={"width": 1280, "height": 900},
                user_agent=ua_string,
            )
            pg = context.pages[0] if context.pages else context.new_page()
            browser = None
        else:
            browser = p.chromium.launch(
                headless=not args.headed,
                args=launch_args,
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=ua_string,
            )
            pg = context.new_page()

        # Inject cookies
        if args.cookie:
            cks = parse_cookie_string(args.cookie)
            if cks:
                context.add_cookies(cks)
                print("  Injected {} cookies".format(len(cks)), file=sys.stderr)

        # Anti-detection
        pg.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        # Register response interceptor BEFORE navigation
        pg.on("response", handle_response)

        # Navigate to stock page - this triggers Xueqiu's JS to load
        # and automatically fire stock_timeline.json with valid signatures
        print("Opening: {}".format(stock_url), file=sys.stderr)
        try:
            pg.goto(
                stock_url,
                wait_until="domcontentloaded",
                timeout=args.timeout * 1000,
            )
        except Exception as e:
            print("Page load error: {}".format(e), file=sys.stderr)

        # Wait for initial API call to be intercepted
        print("Waiting for page JS to fire initial API call...", file=sys.stderr)
        deadline = time.time() + 10
        while timeline_response_count == 0 and time.time() < deadline:
            time.sleep(0.5)

        if timeline_response_count == 0:
            print("  No API response intercepted during page load.", file=sys.stderr)
            # Try clicking announcement tab to trigger API call
            for sel in [
                'a:has-text("公告")',
                'span:has-text("公告")',
                'div:has-text("公告")',
            ]:
                try:
                    tab = pg.locator(sel).first
                    if tab.is_visible(timeout=2000):
                        tab.click()
                        print("  Clicked '{}' tab".format(sel), file=sys.stderr)
                        time.sleep(3)
                        break
                except Exception:
                    continue

        if timeline_response_count == 0:
            print("  Still no API response. Page may require login or has changed.", file=sys.stderr)

        # === Pagination loop ===
        # Trigger the page to load more data. Xueqiu's JS will generate
        # fresh signatures for each subsequent request automatically.
        consecutive_no_new = 0
        for page_num in range(2, args.max_pages + 1):
            prev_count = len(pdf_urls)
            prev_intercepts = timeline_response_count
            triggered = False

            # Strategy 1: Click pagination / load-more buttons
            for selector in [
                'a:has-text("加载更多")',
                'button:has-text("加载更多")',
                'a:has-text("Load More")',
                'a.next',
                'a:has-text("下一页")',
                'button:has-text("下一页")',
            ]:
                try:
                    btn = pg.locator(selector).first
                    if btn.is_visible(timeout=800):
                        btn.scroll_into_view_if_needed()
                        btn.click()
                        triggered = True
                        print(
                            "  Page {}: clicked '{}'".format(page_num, selector),
                            file=sys.stderr,
                        )
                        break
                except Exception:
                    continue

            # Strategy 2: Scroll to bottom (triggers infinite-scroll if applicable)
            if not triggered:
                try:
                    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    print("  Page {}: scrolled to bottom".format(page_num), file=sys.stderr)
                except Exception:
                    pass

            # Wait for the new API response to be intercepted
            wait_deadline = time.time() + max(args.wait_between_pages, 3.0)
            while timeline_response_count == prev_intercepts and time.time() < wait_deadline:
                time.sleep(0.3)

            # Check progress
            if len(pdf_urls) == prev_count:
                consecutive_no_new += 1
                if consecutive_no_new >= MAX_CONSECUTIVE_NO_NEW:
                    print(
                        "  No new PDFs for {} consecutive pages, stopping.".format(
                            consecutive_no_new
                        ),
                        file=sys.stderr,
                    )
                    break
            else:
                consecutive_no_new = 0

            # Small additional delay for rate limiting
            if args.wait_between_pages > 0:
                remaining = args.wait_between_pages - (time.time() - (wait_deadline - max(args.wait_between_pages, 3.0)))
                if remaining > 0:
                    time.sleep(remaining)

        # Final: also extract from page HTML as fallback
        try:
            page_html = pg.content()
            html_pdfs = set(XUEQIU_PDF_URL_PATTERN.findall(page_html))
            new_from_html = html_pdfs - pdf_urls
            if new_from_html:
                pdf_urls.update(new_from_html)
                print(
                    "  {} additional PDF links from page HTML".format(
                        len(new_from_html)
                    ),
                    file=sys.stderr,
                )
        except Exception:
            pass

        # Cleanup
        if browser:
            browser.close()
        else:
            context.close()

    return sorted(pdf_urls), api_responses, pdf_candidates


def print_result(success, symbol, pdf_urls, message, api_responses=None, candidates=None):
    status = "SUCCESS" if success else "FAILED"
    print("\n---RESULT---")
    print("status: {}".format(status))
    print("symbol: {}".format(symbol))
    print("pdf_count: {}".format(len(pdf_urls)))
    print("pdf_urls: {}".format(json.dumps(pdf_urls, ensure_ascii=False)))
    print("api_interceptions: {}".format(len(api_responses or [])))
    print("candidate_count: {}".format(len(candidates or [])))
    print("message: {}".format(message))
    print("---END---")


def main(argv=None):
    args = parse_args(argv)
    if not args.symbol and not args.url:
        print("Error: --symbol or --url is required", file=sys.stderr)
        print_result(False, "", [], "Missing symbol or url", [], [])
        sys.exit(EXIT_BAD_ARGUMENTS)

    if not check_playwright():
        sys.exit(EXIT_DEPENDENCY_MISSING)

    try:
        raw_symbol = args.symbol or extract_symbol_from_url(args.url)
        symbol = normalize_symbol(raw_symbol, args.market)
    except ValueError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        print_result(False, "", [], str(exc))
        sys.exit(EXIT_BAD_ARGUMENTS)

    print("Target: {}".format(symbol), file=sys.stderr)
    print("Max pages: {}".format(args.max_pages), file=sys.stderr)
    print("Mode: {}".format("headed" if args.headed else "headless"), file=sys.stderr)
    print("Strategy: browser interception (auto-signature by Xueqiu JS)", file=sys.stderr)
    print("---", file=sys.stderr)

    try:
        if args.timeline_url:
            pdf_list, api_responses, candidates = crawl_timeline_with_playwright_fetch(symbol, args)
        else:
            pdf_list, api_responses, candidates = crawl_with_playwright(symbol, args)
    except Exception as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        print_result(False, symbol, [], "Crawl failed: {}".format(exc), [])
        sys.exit(EXIT_NETWORK_FAILURE)

    if pdf_list:
        print_result(True, symbol, pdf_list, "OK", api_responses, candidates)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "symbol": symbol,
                        "pdf_urls": pdf_list,
                        "api_interceptions": len(api_responses),
                        "candidates": candidates,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            print("\nResult saved to: {}".format(args.output), file=sys.stderr)
        sys.exit(EXIT_SUCCESS)
    else:
        print_result(False, symbol, [], "No PDF links found", api_responses, candidates)
        sys.exit(EXIT_NETWORK_FAILURE)


if __name__ == "__main__":
    main()
