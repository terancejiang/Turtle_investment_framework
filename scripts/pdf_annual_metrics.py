#!/usr/bin/env python3
"""Extract key metrics from annual report PDFs (HKFRS Chinese/English).

This script is designed to support the Turtle Investment Framework when
Tushare data is unavailable. It parses a small set of stable line-items
from the PDF text using keyword matching.

Usage:
  python3 scripts/pdf_annual_metrics.py --pdf output/06049_保利物业/06049_2024_年报.pdf --year 2024
  python3 scripts/pdf_annual_metrics.py --pdf-dir output/06049_保利物业 --years 2020 2021 2022 2023 2024
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Optional

import pdfplumber


def _parse_amount(token: str) -> float:
    s = token.strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace(",", "")
    return (-1.0 if neg else 1.0) * float(s)


def _first_num_on_line(text: str, label: str) -> Optional[float]:
    for line in text.splitlines():
        line_norm = re.sub(r"\s+", "", line)
        if label in line_norm:
            tokens = re.findall(r"\(?\d[\d,]*\)?", line)
            if not tokens:
                return None
            candidates = []
            for tok in tokens:
                raw = tok.strip("()")
                raw_digits = raw.replace(",", "")
                if "," in tok:
                    candidates.append(tok)
                    continue
                if len(raw_digits) >= 4:
                    candidates.append(tok)
            pick = candidates[0] if candidates else tokens[-1]
            try:
                return _parse_amount(pick)
            except ValueError:
                return None
    return None


def _find_page_text(pages: list[str], must_have: list[str]) -> Optional[str]:
    must_norm = [re.sub(r"\s+", "", m) for m in must_have]
    for t in pages:
        t_norm = re.sub(r"\s+", "", t)
        if all(k in t_norm for k in must_norm):
            return t
    return None


def extract_metrics(pdf_path: str, year: int) -> dict:
    with pdfplumber.open(pdf_path) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]

    cf = _find_page_text(pages, ["現金流量表", "經營活動", f"截至{year}年12月31日止年度"]) or \
        _find_page_text(pages, ["現金流量表", "經營活動"])
    bs = _find_page_text(pages, ["綜合財務狀況表", f"於{year}年12月31日", "流動資產"])
    bs2 = _find_page_text(pages, ["綜合財務狀況表", f"於{year}年12月31日", "非流動負債"])
    da = _find_page_text(pages, ["除稅前溢利已扣除", "折舊", "人民幣千元"]) or \
        _find_page_text(pages, ["除稅前溢利已扣除", "折舊"])

    r: dict = {"year": year, "unit": "RMB_million"}

    if cf:
        r["ocf"] = _first_num_on_line(cf, "經營活動所得現金淨額")
        r["capex_ppe"] = _first_num_on_line(cf, "購買物業、廠房及設備")
        r["capex_lease_ip"] = _first_num_on_line(cf, "購買租賃資產及其他投資物業")
        r["div_paid_owner"] = _first_num_on_line(cf, "已付本公司擁有人的股息")
        r["cash_end"] = _first_num_on_line(cf, "年末現金及現金等價物")
    else:
        r["ocf"] = None
        r["capex_ppe"] = None
        r["capex_lease_ip"] = None
        r["div_paid_owner"] = None
        r["cash_end"] = None

    if bs:
        r["term_deposit"] = _first_num_on_line(bs, "定期存款")
        r["lease_liab_cur"] = _first_num_on_line(bs, "租賃負債")
    else:
        r["term_deposit"] = None
        r["lease_liab_cur"] = None

    r["lease_liab_noncur"] = _first_num_on_line(bs2, "租賃負債") if bs2 else None

    if da:
        d1 = _first_num_on_line(da, "物業、廠房及設備折舊")
        d2 = _first_num_on_line(da, "租賃資產及投資物業折舊")
        d3 = _first_num_on_line(da, "無形資產攤銷")
        r["da_total"] = sum(v for v in (d1, d2, d3) if v is not None)
    else:
        r["da_total"] = None

    for k, v in list(r.items()):
        if k in ("year", "unit"):
            continue
        if v is not None:
            r[k] = float(v) / 1000.0

    cap_total = None
    if r.get("capex_ppe") is not None or r.get("capex_lease_ip") is not None:
        cap_total = (r.get("capex_ppe") or 0.0) + (r.get("capex_lease_ip") or 0.0)
    r["capex_total"] = cap_total

    return r


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract key annual-report metrics from PDFs")
    p.add_argument("--pdf", default=None, help="Path to one annual report PDF")
    p.add_argument("--year", type=int, default=None, help="Fiscal year of --pdf")
    p.add_argument("--pdf-dir", default=None, help="Directory containing {code}_{year}_年报.pdf")
    p.add_argument("--years", nargs="*", type=int, default=None, help="Years to extract when using --pdf-dir")
    p.add_argument("--code", default=None, help="Numeric code used in filename pattern (e.g., 06049)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.pdf:
        if not args.year:
            raise SystemExit("--year is required when using --pdf")
        out = extract_metrics(args.pdf, args.year)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if not args.pdf_dir:
        raise SystemExit("Either --pdf or --pdf-dir must be provided")

    code = args.code or ""
    years = args.years or []
    if not years:
        raise SystemExit("--years is required when using --pdf-dir")

    pdf_dir = Path(args.pdf_dir)
    rows = []
    for y in years:
        name = f"{code}_{y}_年报.pdf" if code else f"*{y}*年报*.pdf"
        matches = list(pdf_dir.glob(name))
        if not matches:
            matches = list(pdf_dir.glob(f"*{y}*.pdf"))
        if not matches:
            rows.append({"year": y, "error": "pdf_not_found"})
            continue
        rows.append(extract_metrics(str(matches[0]), y))

    print(json.dumps({"rows": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
