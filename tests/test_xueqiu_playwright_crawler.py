import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from xueqiu_playwright_crawler import (
    build_symbol_id,
    extract_symbol_from_url,
    find_pdf_links_in_object,
    normalize_symbol,
    parse_timeline_url,
)


def test_extract_symbol_from_url():
    assert extract_symbol_from_url("https://xueqiu.com/S/06049") == "06049"
    assert extract_symbol_from_url("https://xueqiu.com/S/HK06049") == "HK06049"


def test_normalize_symbol():
    assert normalize_symbol("06049") == "HK06049"
    assert normalize_symbol("HK06049") == "HK06049"
    assert normalize_symbol("sh600887") == "SH600887"
    assert normalize_symbol("600887") == "SH600887"
    assert normalize_symbol("600887", "SZ") == "SZ600887"


def test_build_symbol_id():
    assert build_symbol_id("HK06049") == "06049"
    assert build_symbol_id("SH600887") == "600887"
    assert build_symbol_id("06049") == "06049"


def test_parse_timeline_url():
    url = (
        "https://xueqiu.com/statuses/stock_timeline.json?"
        "symbol_id=06049&count=10&source=%E5%85%AC%E5%91%8A&page=1"
    )
    base_url, params = parse_timeline_url(url)
    assert base_url == "https://xueqiu.com/statuses/stock_timeline.json"
    assert params["symbol_id"] == "06049"
    assert params["count"] == "10"


def test_find_pdf_links_in_object():
    data = {
        "list": [
            {
                "text": "公告 https://stockn.xueqiu.com/06049/20240101001.pdf",
            }
        ]
    }
    links = find_pdf_links_in_object(data)
    assert "https://stockn.xueqiu.com/06049/20240101001.pdf" in links
