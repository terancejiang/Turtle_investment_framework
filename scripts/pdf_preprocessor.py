#!/usr/bin/env python3
"""Turtle Investment Framework - PDF Preprocessor (Phase 2A).

Scans annual report PDFs for 7 target sections using keyword matching
and outputs structured JSON for Agent fine-extraction.

Target sections:
    P2: Restricted cash (受限资产)
    P3: AR aging (应收账款账龄)
    P4: Related party transactions (关联方交易)
    P6: Contingent liabilities (或有负债)
    P13: Non-recurring items (非经常性损益)
    MDA: Management Discussion & Analysis (管理层讨论与分析)
    SUB: Subsidiary holdings (主要控股参股公司)

Usage:
    python3 scripts/pdf_preprocessor.py --pdf report.pdf
    python3 scripts/pdf_preprocessor.py --pdf report.pdf --output output/sections.json
    python3 scripts/pdf_preprocessor.py --pdf report.pdf --market HK --verbose
    python3 scripts/pdf_preprocessor.py --pdf report.pdf --verbose --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pdfplumber


# ---------------------------------------------------------------------------
# Feature #38: SECTION_KEYWORDS for 5 target sections
# Feature #43: Traditional Chinese keyword support
# ---------------------------------------------------------------------------

SECTION_KEYWORDS: Dict[str, List[str]] = {
    "P2": [
        # Simplified Chinese
        "所有权或使用权受限资产",
        "受限资产",
        "使用受限的资产",
        "所有权受限",
        "使用权受到限制",
        "受限的货币资金",
        "受到限制的资产",
        # Traditional Chinese (HK reports)
        "所有權或使用權受限資產",
        "受限資產",
        "使用受限的資產",
    ],
    "P3": [
        # Simplified Chinese
        "应收账款账龄",
        "应收账款的账龄",
        "账龄分析",
        "应收账款按账龄披露",
        "应收账款按账龄列示",
        "应收款项账龄",
        # Traditional Chinese
        "應收賬款賬齡",
        "應收賬款的賬齡",
        "賬齡分析",
    ],
    "P4": [
        # Simplified Chinese
        "关联方交易",
        "关联交易",
        "关联方及关联交易",
        "关联方关系及其交易",
        "重大关联交易",
        # Traditional Chinese
        "關聯方交易",
        "關聯交易",
        "關聯方及關聯交易",
    ],
    "P6": [
        # Simplified Chinese
        "或有负债",
        "或有事项",
        "未决诉讼",
        "重大诉讼",
        "对外担保",
        "承诺及或有事项",
        "承诺和或有负债",
        # Traditional Chinese
        "或有負債",
        "或有事項",
        "未決訴訟",
        "承諾及或有事項",
    ],
    "P13": [
        # Simplified Chinese - specific (prefer these for supplement zone)
        "非经常性损益项目及金额",
        "非经常性损益合计",
        # Simplified Chinese - general
        "非经常性损益",
        "非经常性损益明细",
        "非经常性损益项目",
        "扣除非经常性损益",
        "非经常性损益的项目和金额",
        # Traditional Chinese
        "非經常性損益",
        "非經常性損益明細",
        "非經常性損益項目及金額",
    ],
    "MDA": [
        # Simplified Chinese
        "管理层讨论与分析",
        "经营情况讨论与分析",
        "经营情况的讨论与分析",
        "管理层分析与讨论",
        "董事会报告",
        # Traditional Chinese
        "管理層討論與分析",
        "經營情況討論與分析",
        "董事會報告",
    ],
    "SUB": [
        # 高特异性 ── 主匹配
        "主要控股参股公司分析",
        "主要子公司及对公司净利润的影响",
        "主要控股参股公司情况",
        "控股子公司情况",
        # 中特异性
        "在子公司中的权益",
        "在其他主体中的权益",
        "纳入合并范围的主体",
        "合并范围的变化",
        # 新增: 更具体的变体
        "长期股权投资——对子公司",
        "长期股权投资——联营企业",
        # 繁体
        "主要控股參股公司分析",
        "在子公司中的權益",
        "在其他主體中的權益",
        "長期股權投資——對子公司",
    ],
}

# ---------------------------------------------------------------------------
# HK Market (HKFRS) keyword dictionaries ── Plan A adaptation
# ---------------------------------------------------------------------------

SECTION_KEYWORDS_HK: Dict[str, List[str]] = {
    "P2": [
        # HK HKFRS ── restricted cash / pledged deposits
        "受限制銀行存款",
        "已抵押銀行存款",
        "受限制存款",
        "已質押存款",
        "受限制現金",
        "已抵押的銀行存款",
        "使用受限制的銀行結餘",
        # Fallback: broader terms
        "受限制的資產",
        "使用權受限資產",
    ],
    "P3": [
        # HK ── AR aging
        "應收賬款賬齡",
        "應收賬款的賬齡",
        "賬齡分析",
        "應收貿易賬款賬齡",
        "應收貿易賬款的賬齡",
        "貿易應收款項的賬齡",
        "貿易應收款項賬齡",
        "按賬齡分析",
    ],
    "P4": [
        # HK uses 關連 (connected transactions), NOT 關聯
        "關連交易",
        "關連人士交易",
        "持續關連交易",
        "須予披露的關連交易",
        "關連人士",
        # Fallback: some HK reports still use 關聯
        "關聯方交易",
        "關聯交易",
    ],
    "P6": [
        # HK ── contingent liabilities
        "或然負債",
        "或然負債及承擔",
        "資本承擔",
        "經營租賃承擔",
        "承擔及或然負債",
        # Broader
        "承擔",
        "或有事項",
    ],
    "P13": [
        # HK has NO "非經常性損益" concept; use equivalent disclosure items
        "其他收入及其他收益及虧損淨額",
        "其他收入及其他收益及虧損",
        "其他收入及收益",
        "其他收益及虧損",
        "其他淨收入",
        "其他收入淨額",
        # Some HK reports may still have this (dual-listed)
        "非經常性損益",
    ],
    "MDA": [
        # HK ── Management Discussion & Analysis
        "管理層討論及分析",
        "管理層討論與分析",
        "業務回顧",
        "營運回顧",
        "主席報告",
        "行政總裁報告",
        "董事會報告",
    ],
    "SUB": [
        # HK ── subsidiaries
        "主要附屬公司的詳情",
        "主要附屬公司",
        "附屬公司的詳情",
        "於附屬公司的權益",
        "在附屬公司的權益",
        "於附屬公司之權益",
        "附屬公司名單",
        "附屬公司列表",
    ],
}

# Per-section extraction parameters (overrides defaults)
SECTION_EXTRACT_CONFIG: Dict[str, Dict[str, int]] = {
    "MDA": {"buffer_pages": 3, "max_chars": 8000},
    "SUB": {"buffer_pages": 2, "max_chars": 6000},
}
DEFAULT_BUFFER_PAGES = 1
DEFAULT_MAX_CHARS = 4000

# ---------------------------------------------------------------------------
# Zone detection markers for A-share annual reports (CSRC format)
# ---------------------------------------------------------------------------

ZONE_MARKERS: List[Tuple[str, str]] = [
    (r"第[一二三四五六七八九十百]+节\s*重要提示", "INTRO_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*公司简介", "INTRO_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*管理层讨论与分析", "MDA_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*经营情况讨论与分析", "MDA_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*公司治理", "GOVERNANCE_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*财务报告", "FIN_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*会计数据", "FIN_ZONE"),
    # Sub-zones within financial report
    (r"[四五六]\s*[、.．]\s*重要会计政策", "POLICY_ZONE"),
    (r"七\s*[、.．]\s*合并财务报表项目注释", "NOTES_ZONE"),
    (r"[一二三四五六七八九十]+[、.．]\s*补充资料", "SUPPLEMENT_ZONE"),
]

# ---------------------------------------------------------------------------
# Zone detection markers for HK (HKFRS) annual reports
# ---------------------------------------------------------------------------

ZONE_MARKERS_HK: List[Tuple[str, str]] = [
    # Corporate Governance / 企業管治
    (r"企業管治報告", "GOVERNANCE_ZONE"),
    (r"企業管治", "GOVERNANCE_ZONE"),
    # Directors' Report / 董事會報告
    (r"董事會報告", "DIRECTORS_ZONE"),
    # MDA / 管理層討論
    (r"管理層討論及分析", "MDA_ZONE"),
    (r"管理層討論與分析", "MDA_ZONE"),
    (r"業務回顧", "MDA_ZONE"),
    (r"營運回顧", "MDA_ZONE"),
    # Financial Statements / 綜合財務報表
    (r"綜合損益表", "FIN_ZONE"),
    (r"綜合財務狀況表", "FIN_ZONE"),
    (r"綜合全面收益表", "FIN_ZONE"),
    # Accounting Policies / 重要會計政策
    (r"重要會計政策", "POLICY_ZONE"),
    (r"主要會計政策", "POLICY_ZONE"),
    # Notes / 綜合財務報表附註
    (r"綜合財務報表附註", "NOTES_ZONE"),
    (r"財務報表附註", "NOTES_ZONE"),
    # Five Year Financial Summary / 五年財務摘要
    (r"五年財務摘要", "SUPPLEMENT_ZONE"),
]

SECTION_ZONE_PREFERENCES: Dict[str, Dict[str, List[str]]] = {
    "P2":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P3":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P4":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P6":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P13": {"prefer": ["SUPPLEMENT_ZONE", "NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "MDA": {"prefer": ["MDA_ZONE"], "avoid": ["NOTES_ZONE", "FIN_ZONE", "POLICY_ZONE", "SUPPLEMENT_ZONE"]},
    "SUB": {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
}

SECTION_ZONE_PREFERENCES_HK: Dict[str, Dict[str, List[str]]] = {
    "P2":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE", "MDA_ZONE"]},
    "P3":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE", "MDA_ZONE"]},
    "P4":  {"prefer": ["DIRECTORS_ZONE", "NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P6":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE", "GOVERNANCE_ZONE"]},
    "P13": {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE", "MDA_ZONE"]},
    "MDA": {"prefer": ["MDA_ZONE"], "avoid": ["NOTES_ZONE", "FIN_ZONE", "POLICY_ZONE"]},
    "SUB": {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE", "MDA_ZONE"]},
}


# ---------------------------------------------------------------------------
# Feature #37: PDF text extraction with pdfplumber
# Feature #44: PyMuPDF fallback for garbled text
# Feature #45: Table-aware extraction
# ---------------------------------------------------------------------------

def is_garbled(text: str, threshold: float = 0.30) -> bool:
    """Detect garbled text: >threshold fraction of non-CJK/ASCII/common-punct chars."""
    if not text:
        return True
    normal = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x20 <= cp <= 0x7E
            or 0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0x3000 <= cp <= 0x303F
            or 0xFF00 <= cp <= 0xFFEF
            or ch in "\n\r\t"
        ):
            normal += 1
    ratio = normal / len(text)
    return ratio < (1 - threshold)


def _tables_to_markdown(tables: list) -> str:
    """Convert pdfplumber tables to markdown format."""
    parts = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        cleaned = []
        for row in table:
            cleaned.append([
                (cell or "").replace("\n", " ").strip()
                for cell in row
            ])
        header = cleaned[0]
        md = "| " + " | ".join(header) + " |\n"
        md += "| " + " | ".join(["---"] * len(header)) + " |\n"
        for row in cleaned[1:]:
            while len(row) < len(header):
                row.append("")
            md += "| " + " | ".join(row[:len(header)]) + " |\n"
        parts.append(md)
    return "\n".join(parts)


def extract_all_pages(pdf_path: str, verbose: bool = False) -> List[Tuple[int, str]]:
    """Extract text from all pages of a PDF using pdfplumber."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages_text: List[Tuple[int, str]] = []
    garbled_count = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            if verbose:
                print(f"Extracting {total} pages with pdfplumber...")

            for i, page in enumerate(pdf.pages):
                page_num = i + 1
                text = page.extract_text() or ""

                tables = page.extract_tables()
                if tables:
                    table_md = _tables_to_markdown(tables)
                    if table_md:
                        text = text + "\n\n[TABLE]\n" + table_md

                if is_garbled(text) and len(text) > 50:
                    garbled_count += 1

                pages_text.append((page_num, text))

                if verbose and page_num % 50 == 0:
                    print(f"  ...page {page_num}/{total}")

    except Exception as e:
        err_msg = str(e).lower()
        if "encrypt" in err_msg or "password" in err_msg:
            raise RuntimeError(f"PDF is encrypted: {pdf_path}") from e
        raise RuntimeError(f"Cannot open PDF: {pdf_path}: {e}") from e

    if total > 0 and garbled_count / total > 0.30:
        if verbose:
            print(f"Garbled text detected ({garbled_count}/{total} pages), trying PyMuPDF...")
        fallback = fallback_extract_pymupdf(pdf_path, verbose=verbose)
        if fallback:
            return fallback

    return pages_text


def fallback_extract_pymupdf(pdf_path: str, verbose: bool = False) -> Optional[List[Tuple[int, str]]]:
    """Fallback extraction using PyMuPDF (fitz)."""
    try:
        import fitz
    except ImportError:
        if verbose:
            print("PyMuPDF not installed, skipping fallback.")
        return None

    pages_text: List[Tuple[int, str]] = []
    try:
        doc = fitz.open(pdf_path)
        total = len(doc)
        if verbose:
            print(f"Extracting {total} pages with PyMuPDF...")
        for i in range(total):
            page = doc[i]
            text = page.get_text() if hasattr(page, 'get_text') else page.getText()
            pages_text.append((i + 1, text or ""))
        doc.close()
    except Exception as e:
        if verbose:
            print(f"PyMuPDF fallback failed: {e}")
        return None

    return pages_text


# ---------------------------------------------------------------------------
# Zone detection for annual report structure
# ---------------------------------------------------------------------------

def detect_zones(
    pages_text: List[Tuple[int, str]],
    market_type: str = "CN",
) -> Dict[int, str]:
    """Detect report structure zones by scanning for section markers.

    Args:
        pages_text: List of (page_number, text) tuples.
        market_type: 'CN' for A-share (CSRC format) or 'HK' for HKFRS format.

    Returns:
        Dict mapping page_number -> zone_name.
    """
    markers = ZONE_MARKERS_HK if market_type == "HK" else ZONE_MARKERS

    zone_transitions: List[Tuple[int, str]] = []

    for page_num, text in pages_text:
        if not text:
            continue
        for pattern, zone_name in markers:
            if re.search(pattern, text):
                zone_transitions.append((page_num, zone_name))
                break

    if not zone_transitions:
        return {}

    zone_transitions.sort(key=lambda x: x[0])
    page_zones: Dict[int, str] = {}
    current_zone = None
    transition_idx = 0

    for page_num, _ in pages_text:
        while transition_idx < len(zone_transitions) and zone_transitions[transition_idx][0] <= page_num:
            current_zone = zone_transitions[transition_idx][1]
            transition_idx += 1
        if current_zone:
            page_zones[page_num] = current_zone

    return page_zones


# ---------------------------------------------------------------------------
# Feature #39: Keyword matching to locate sections
# Feature #46: Section priority scoring
# ---------------------------------------------------------------------------

def _score_match(
    page_num: int, total_pages: int, text: str, keyword: str,
    zone: Optional[str] = None, section_id: Optional[str] = None,
    market_type: str = "CN",
) -> float:
    """Score a keyword match: prefer correct report zone over TOC.

    Scoring:
        +1.0 base for a match
        +2.0 if page is in a preferred zone for this section
        -2.0 if page is in an avoided zone for this section
        +0.5 fallback position bonus if no zone info available
        -0.5 if page looks like TOC
        +0.3 if keyword appears in a heading-like context
        -0.3 if keyword only appears as a cross-reference
    """
    score = 1.0

    # Select zone preferences based on market
    zone_prefs = SECTION_ZONE_PREFERENCES_HK if market_type == "HK" else SECTION_ZONE_PREFERENCES

    # Zone-aware scoring
    if zone and section_id and section_id in zone_prefs:
        prefs = zone_prefs[section_id]
        if zone in prefs.get("prefer", []):
            score += 2.0
        elif zone in prefs.get("avoid", []):
            score -= 2.0
    elif total_pages > 0:
        if page_num / total_pages > 0.30:
            score += 0.5

    # Penalize TOC pages (both Simplified and Traditional)
    if any(toc in text for toc in ["目录", "目 录", "目錄", "目 錄"]):
        score -= 0.5

    # Penalize cross-references
    kw_pos = text.find(keyword)
    if kw_pos > 0:
        before = text[max(0, kw_pos - 30):kw_pos]
        xrefs = ["详见", "参见", "参照", "詳見", "參見", "參照", "載於"]
        if any(ref in before for ref in xrefs):
            score -= 0.3

    # SUB context scoring
    if section_id == "SUB" and kw_pos >= 0:
        context_window = text[max(0, kw_pos - 200):min(len(text), kw_pos + 200)]
        if market_type == "HK":
            acct_hk = ["會計政策", "確認及計量", "減值", "公允價值", "合併"]
            if sum(1 for a in acct_hk if a in context_window) >= 2:
                score -= 1.5
            subs_hk = ["主要業務", "註冊地", "持股比例", "股本", "附屬公司名稱"]
            if sum(1 for s in subs_hk if s in context_window) >= 2:
                score += 1.0
        else:
            acct = ["权益法", "账面余额", "减值准备", "成本法", "账面价值"]
            if sum(1 for a in acct if a in context_window) >= 2:
                score -= 1.5
            subs = ["主营业务", "营业收入", "净利润", "注册资本", "持股比例"]
            if sum(1 for s in subs if s in context_window) >= 2:
                score += 1.0

    # P3 context scoring: penalize non-AR aging
    if section_id == "P3" and kw_pos >= 0:
        context_window = text[max(0, kw_pos - 200):min(len(text), kw_pos + 200)]
        if market_type == "HK":
            non_ar_hk = ["預付款項", "預付賬款", "應付賬款", "應付票據", "其他應付"]
            if any(term in context_window for term in non_ar_hk):
                score -= 2.0
        else:
            non_ar = ["预付款项", "预付账款", "预付", "应付账款", "应付票据", "其他应付"]
            if any(term in context_window for term in non_ar):
                score -= 2.0

    # P6 context scoring for HK: reward actual figures, penalize policy
    if section_id == "P6" and market_type == "HK" and kw_pos >= 0:
        context_window = text[max(0, kw_pos - 200):min(len(text), kw_pos + 200)]
        fig_indicators = ["千港元", "百萬", "人民幣", "港元"]
        if any(ind in context_window for ind in fig_indicators):
            score += 0.5
        policy_indicators = ["會計政策", "確認及計量", "準則規定"]
        if sum(1 for p in policy_indicators if p in context_window) >= 2:
            score -= 1.0

    # Bonus: keyword appears near a numbered heading pattern
    if market_type == "HK":
        heading_patterns = [
            r"\d+[.．、]\s*" + re.escape(keyword),
            r"[（(]\s*[a-zA-Z0-9]+\s*[)）]\s*" + re.escape(keyword),
        ]
    else:
        heading_patterns = [
            r"\d+[、.．]\s*" + re.escape(keyword),
            r"[一二三四五六七八九十]+[、.．]\s*" + re.escape(keyword),
        ]
    for pat in heading_patterns:
        if re.search(pat, text):
            score += 0.3
            break

    return score


def find_section_pages(
    pages_text: List[Tuple[int, str]],
    section_keywords: Dict[str, List[str]] = None,
    market_type: str = "CN",
) -> Dict[str, List[int]]:
    """Locate sections by scanning all pages for keywords.

    Args:
        pages_text: List of (page_number, text) tuples.
        section_keywords: Keyword dict (default: auto-selected by market_type).
        market_type: 'CN' or 'HK'.

    Returns:
        Dict mapping section_id -> [page_numbers] sorted by priority score.
    """
    if section_keywords is None:
        section_keywords = SECTION_KEYWORDS_HK if market_type == "HK" else SECTION_KEYWORDS

    total_pages = len(pages_text)
    results: Dict[str, List[int]] = {}

    page_zones = detect_zones(pages_text, market_type=market_type)

    for section_id, keywords in section_keywords.items():
        scored_matches: List[Tuple[float, int]] = []

        for page_num, text in pages_text:
            if not text:
                continue
            for kw in keywords:
                if kw in text:
                    zone = page_zones.get(page_num)
                    score = _score_match(page_num, total_pages, text, kw,
                                         zone=zone, section_id=section_id,
                                         market_type=market_type)
                    scored_matches.append((score, page_num))
                    break

        scored_matches.sort(key=lambda x: (-x[0], x[1]))

        seen = set()
        ordered_pages = []
        for _, pn in scored_matches:
            if pn not in seen:
                seen.add(pn)
                ordered_pages.append(pn)

        results[section_id] = ordered_pages

    return results


# ---------------------------------------------------------------------------
# Feature #40: Context extraction with page buffer
# ---------------------------------------------------------------------------

def extract_section_context(
    pages_text: List[Tuple[int, str]],
    section_pages: Dict[str, List[int]],
    section_keywords: Dict[str, List[str]] = None,
    buffer_pages: int = 1,
    max_chars: int = 4000,
) -> Dict[str, Optional[str]]:
    """Extract context text for each section using best-match page +/- buffer."""
    if section_keywords is None:
        section_keywords = SECTION_KEYWORDS

    page_lookup: Dict[int, str] = {pn: text for pn, text in pages_text}
    contexts: Dict[str, Optional[str]] = {}

    for section_id, matched_pages in section_pages.items():
        if not matched_pages:
            contexts[section_id] = None
            continue

        cfg = SECTION_EXTRACT_CONFIG.get(section_id, {})
        sect_buffer = cfg.get("buffer_pages", buffer_pages)
        sect_max = cfg.get("max_chars", max_chars)

        best_page = matched_pages[0]

        parts = []
        for offset in range(-sect_buffer, sect_buffer + 1):
            target = best_page + offset
            if target in page_lookup:
                text = page_lookup[target]
                if text:
                    parts.append(f"--- p.{target} ---\n{text}")

        combined = "\n\n".join(parts)

        if len(combined) > sect_max:
            keywords = section_keywords.get(section_id, [])
            combined = _center_truncate(combined, keywords, sect_max)

        contexts[section_id] = combined

    return contexts


def _center_truncate(text: str, keywords: list, max_chars: int) -> str:
    """Truncate text centered around the first keyword match."""
    match_pos = len(text)
    for kw in keywords:
        pos = text.find(kw)
        if pos >= 0 and pos < match_pos:
            match_pos = pos

    if match_pos == len(text):
        return _truncate_at_boundary(text, max_chars)

    half = max_chars // 2
    start = max(0, match_pos - half // 2)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)

    result = text[start:end]

    if start > 0:
        newline_pos = result.find("\n")
        if newline_pos >= 0 and newline_pos < 200:
            result = result[newline_pos + 1:]

    return _truncate_at_boundary(result, max_chars)


def _truncate_at_boundary(text: str, max_chars: int) -> str:
    """Truncate text at the last sentence boundary before max_chars."""
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]

    for sep in ["\u3002", "\n", "\uff1b", ".", "!", "\uff01"]:
        last_pos = truncated.rfind(sep)
        if last_pos > max_chars * 0.5:
            return truncated[:last_pos + 1]

    return truncated


# ---------------------------------------------------------------------------
# Market type auto-detection
# ---------------------------------------------------------------------------

def detect_market_type(stock_code: str = "", pdf_path: str = "") -> str:
    """Auto-detect market type from stock code or PDF filename.

    Returns 'HK' for Hong Kong stocks, 'CN' for A-share (default).
    """
    code = stock_code.upper()
    if ".HK" in code or code.startswith("HK"):
        return "HK"

    basename = os.path.basename(pdf_path).upper()
    if ".HK" in basename:
        return "HK"

    # 5-digit codes starting with 0 and < 10000 are likely HK
    digits = re.findall(r"(\d{5})", basename)
    for d in digits:
        if d.startswith("0") and int(d) < 10000:
            return "HK"

    return "CN"


# ---------------------------------------------------------------------------
# Feature #41: JSON output writer
# ---------------------------------------------------------------------------

def write_output(
    contexts: Dict[str, Optional[str]],
    pdf_path: str,
    total_pages: int,
    output_path: str,
    market_type: str = "CN",
) -> dict:
    """Write pdf_sections.json with 7 sections + metadata."""
    found_count = sum(1 for v in contexts.values() if v is not None)

    output = {
        "metadata": {
            "pdf_file": os.path.basename(pdf_path),
            "total_pages": total_pages,
            "extract_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sections_found": found_count,
            "sections_total": len(contexts),
            "market_type": market_type,
        },
    }

    for section_id in ["P2", "P3", "P4", "P6", "P13", "MDA", "SUB"]:
        output[section_id] = contexts.get(section_id)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output


# ---------------------------------------------------------------------------
# Feature #42: Main pipeline
# ---------------------------------------------------------------------------

def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Extract target sections from annual report PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --pdf report.pdf
  %(prog)s --pdf report.pdf --output output/pdf_sections.json --verbose
  %(prog)s --pdf 06049_2024_年报.pdf --market HK --verbose
        """,
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the annual report PDF file",
    )
    parser.add_argument(
        "--output",
        default="output/pdf_sections.json",
        help="Output JSON file path (default: output/pdf_sections.json)",
    )
    parser.add_argument(
        "--hints",
        default=None,
        help="Path to toc_hints.json (optional, from Phase 2A.5 TOC analysis)",
    )
    parser.add_argument(
        "--market",
        default=None,
        choices=["CN", "HK", "auto"],
        help="Market type: CN (A-share), HK (Hong Kong), auto (auto-detect). Default: auto",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress messages during extraction",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed arguments and exit without processing",
    )
    return parser.parse_args(args)


def _load_hints(hints_path: Optional[str]) -> Dict[str, dict]:
    """Load TOC hints from JSON file."""
    if not hints_path or not os.path.exists(hints_path):
        return {}
    try:
        with open(hints_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load hints file '{hints_path}': {e}", file=sys.stderr)
        return {}


def run_pipeline(
    pdf_path: str,
    output_path: str,
    verbose: bool = False,
    hints_path: Optional[str] = None,
    market_type: Optional[str] = None,
) -> dict:
    """Run the full extraction pipeline.

    Args:
        pdf_path: Path to the PDF.
        output_path: Path for JSON output.
        verbose: Print progress.
        hints_path: Optional path to toc_hints.json.
        market_type: 'CN', 'HK', or None for auto-detection.

    Returns:
        The output dict written to JSON.
    """
    try:
        from scripts.config import validate_pdf
    except ModuleNotFoundError:
        from config import validate_pdf

    is_valid, reason = validate_pdf(pdf_path)
    if not is_valid:
        raise RuntimeError(f"Invalid PDF: {reason}")

    # Auto-detect market type if not specified
    if market_type is None or market_type == "auto":
        market_type = detect_market_type(pdf_path=pdf_path)
        if verbose:
            print(f"  Auto-detected market type: {market_type}")

    kw_dict = SECTION_KEYWORDS_HK if market_type == "HK" else SECTION_KEYWORDS

    # Step 1: Extract all pages
    print(f"[1/4] Extracting pages from {pdf_path}...")
    pages_text = extract_all_pages(pdf_path, verbose=verbose)
    total_pages = len(pages_text)

    if total_pages == 0:
        raise RuntimeError("PDF has no extractable pages")

    print(f"  Extracted {total_pages} pages (market={market_type})")

    # Load TOC hints (Phase 2A.5)
    hints = _load_hints(hints_path)
    if hints and verbose:
        print(f"  Loaded TOC hints for: {list(hints.keys())}")

    # Step 2: Find section pages via keyword matching
    print("[2/4] Scanning for target sections...")
    section_pages = find_section_pages(
        pages_text, section_keywords=kw_dict, market_type=market_type,
    )

    for sid, hint in hints.items():
        if sid in section_pages and "page" in hint:
            hint_page = hint["page"]
            if 1 <= hint_page <= total_pages:
                section_pages[sid] = [hint_page]
                if verbose:
                    print(f"  {sid}: overridden by hint -> page {hint_page}")

    if verbose:
        for sid, pages in section_pages.items():
            if pages:
                print(f"  {sid}: found on pages {pages[:5]}")
            else:
                print(f"  {sid}: not found")

    # Step 3: Extract context around best matches
    print("[3/4] Extracting section context...")
    contexts = extract_section_context(pages_text, section_pages,
                                       section_keywords=kw_dict)

    # Step 4: Write output
    print(f"[4/4] Writing output to {output_path}...")
    result = write_output(contexts, pdf_path, total_pages, output_path,
                          market_type=market_type)

    found = result["metadata"]["sections_found"]
    total = result["metadata"]["sections_total"]
    print(f"Done: {found}/{total} sections found")

    return result


def main():
    args = parse_args()

    if args.dry_run:
        print("=== Dry Run ===")
        print(f"  PDF: {args.pdf}")
        print(f"  Output: {args.output}")
        print(f"  Market: {args.market}")
        print(f"  Hints: {args.hints}")
        print(f"  Verbose: {args.verbose}")
        return

    market = args.market
    if market is None:
        market = "auto"

    try:
        result = run_pipeline(
            args.pdf, args.output, verbose=args.verbose,
            hints_path=args.hints, market_type=market,
        )
        found = result["metadata"]["sections_found"]
        total = result["metadata"]["sections_total"]
        mkt = result["metadata"]["market_type"]
        print(f"Extracted {found}/{total} sections (market={mkt}) -> {args.output}")
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
