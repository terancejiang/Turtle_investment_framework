#!/usr/bin/env python3
import argparse
import json
import re


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--years", required=True, help="Comma separated years, e.g. 2024,2023,2022")
    return p.parse_args(argv)


def is_annual_text(text, year):
    if not text:
        return False
    if str(year) not in text:
        return False
    if "半年度" in text or "中期" in text or "中报" in text:
        return False
    if "年度报告" in text:
        return True
    if re.search(r"\bAnnual Report\b", text, re.I):
        return True
    if "年报" in text:
        return True
    return False


def main(argv=None):
    args = parse_args(argv)
    years = [y.strip() for y in args.years.split(",") if y.strip()]
    years_i = [int(y) for y in years]

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    candidates = data.get("candidates") or []
    out = {}
    for year in years_i:
        hits = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or ""
            urls = item.get("pdf_urls") or []
            if not urls:
                continue
            if is_annual_text(text, year):
                hits.append(
                    {
                        "created_at": int(item.get("created_at") or 0),
                        "text": text,
                        "url": urls[0],
                    }
                )
        hits.sort(key=lambda x: x["created_at"], reverse=True)
        out[str(year)] = hits

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
