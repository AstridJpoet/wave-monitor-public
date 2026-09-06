#!/usr/bin/env python3
"""Collect High-Flyer public footprints from official CNINFO disclosures.

This collector intentionally works only with public filings. It does not claim
to reconstruct private trades, positions below disclosure thresholds, or hedge
books.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import requests


CNINFO_SEARCH_URL = "https://www.cninfo.com.cn/new/fulltextSearch/full"
CNINFO_PDF_ROOT = "https://static.cninfo.com.cn/"
DEFAULT_TERMS = (
    "宁波幻方",
    "九章幻方",
)
MANAGER_ALIASES = {
    "宁波幻方量化投资管理合伙企业": "宁波幻方量化",
    "浙江九章资产管理有限公司": "浙江九章资产",
}
REPORT_KINDS = (
    ("第一季度报告", "Q1", "03-31"),
    ("一季度报告", "Q1", "03-31"),
    ("半年度报告", "H1", "06-30"),
    ("第三季度报告", "Q3", "09-30"),
    ("三季度报告", "Q3", "09-30"),
    ("年度报告", "FY", "12-31"),
)
EXCLUDED_TITLE_PARTS = (
    "摘要",
    "英文版",
    "审计报告",
    "审核问询",
    "问询函",
    "回复",
    "关于",
    "取消",
)


@dataclass(frozen=True)
class Footprint:
    announcement_id: str
    sec_code: str
    symbol: str
    sec_name: str
    report_kind: str
    report_period: str
    publication_date: str
    first_publication_date: str
    title: str
    manager: str
    product_names: str
    source_url: str
    search_terms: str
    source_snippet: str


def clean_markup(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_symbol(sec_code: str) -> str | None:
    code = str(sec_code).strip()
    if not re.fullmatch(r"\d{6}", code):
        return None
    suffix = ".SS" if code.startswith(("5", "6", "9")) else ".SZ"
    return f"{code}{suffix}"


def parse_report_period(title: str) -> tuple[str, str] | None:
    clean = clean_markup(title)
    year_match = re.search(r"(20\d{2})\s*年", clean)
    if not year_match:
        return None
    year = int(year_match.group(1))
    for marker, kind, month_day in REPORT_KINDS:
        if marker in clean:
            return kind, f"{year:04d}-{month_day}"
    return None


def is_primary_periodic_report(title: str) -> bool:
    clean = clean_markup(title)
    if parse_report_period(clean) is None:
        return False
    return not any(part in clean for part in EXCLUDED_TITLE_PARTS)


def publication_date_from_ms(value: int | str | None) -> str:
    timestamp = int(value or 0) / 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def extract_managers(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", clean_markup(text))
    managers = []
    for alias, canonical in MANAGER_ALIASES.items():
        if alias in compact and canonical not in managers:
            managers.append(canonical)
    return managers


def extract_product_names(text: str) -> list[str]:
    clean = clean_markup(text)
    clean = re.sub(r"\s+", "", clean)
    products: list[str] = []
    pattern = re.compile(
        r"((?:九章幻方|幻方量化|幻方星月石)[一-龥A-Za-z0-9]{0,45}?"
        r"(?:私募证券投资基金|私募基金))"
    )
    for match in pattern.finditer(clean):
        product = match.group(1).strip("－—-:：")
        if product and product not in products:
            products.append(product)
    return products


def request_json(session: requests.Session, params: dict, retries: int = 4) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(
                CNINFO_SEARCH_URL,
                params=params,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; public-disclosure-research/1.0)",
                    "Referer": "https://www.cninfo.com.cn/new/fulltextSearch",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("CNINFO returned a non-object response")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"CNINFO request failed: {last_error}")


def search_term(
    session: requests.Session,
    term: str,
    start: str,
    end: str,
    page_size: int,
    pause_seconds: float,
    max_pages: int,
) -> list[dict]:
    records: list[dict] = []
    page = 1
    while True:
        payload = request_json(
            session,
            {
                "searchkey": term,
                "sdate": start,
                "edate": end,
                "isfulltext": "true",
                "sortName": "pubdate",
                "sortType": "desc",
                "pageNum": page,
                "pageSize": page_size,
            },
        )
        announcements = payload.get("announcements") or []
        if not announcements:
            break
        for item in announcements:
            item = dict(item)
            item["matched_search_term"] = term
            records.append(item)
        total = int(payload.get("totalRecordNum") or payload.get("totalAnnouncement") or 0)
        print(f"{term}: page {page}, collected {len(records)}/{total or '?'}")
        if len(records) >= total > 0 or len(announcements) < page_size:
            break
        if max_pages and page >= max_pages:
            break
        page += 1
        time.sleep(max(0.0, pause_seconds))
    return records


def select_footprints(records: Iterable[dict]) -> list[Footprint]:
    grouped: dict[tuple[str, str], dict] = {}
    for item in records:
        title = clean_markup(item.get("announcementTitle"))
        if not is_primary_periodic_report(title):
            continue
        snippet = clean_markup(item.get("announcementContent"))
        managers = extract_managers(snippet)
        if not managers:
            continue
        symbol = canonical_symbol(str(item.get("secCode") or ""))
        period = parse_report_period(title)
        if symbol is None or period is None:
            continue
        kind, report_period = period
        key = (symbol, report_period)
        current = grouped.get(key)
        item = dict(item)
        item["clean_title"] = title
        item["clean_snippet"] = snippet
        item["managers"] = managers
        item["report_kind"] = kind
        item["report_period"] = report_period
        item_time = int(item.get("announcementTime") or 0)
        if current is None:
            item["first_announcement_time"] = item_time
            item["all_search_terms"] = [item.get("matched_search_term")]
            grouped[key] = item
        else:
            terms = set(current.get("all_search_terms") or [current.get("matched_search_term")])
            terms.add(item.get("matched_search_term"))
            first_time = min(int(current.get("first_announcement_time") or 0), item_time)
            if item_time > int(current.get("announcementTime") or 0):
                item["all_search_terms"] = sorted(term for term in terms if term)
                item["first_announcement_time"] = first_time
                grouped[key] = item
            else:
                current["all_search_terms"] = sorted(term for term in terms if term)
                current["first_announcement_time"] = first_time

    footprints: list[Footprint] = []
    for item in grouped.values():
        snippet = str(item.get("clean_snippet") or "")
        managers = list(item.get("managers") or extract_managers(snippet))
        products = extract_product_names(snippet)
        terms = item.get("all_search_terms") or [item.get("matched_search_term")]
        adjunct = str(item.get("adjunctUrl") or "").lstrip("/")
        footprints.append(
            Footprint(
                announcement_id=str(item.get("announcementId") or ""),
                sec_code=str(item.get("secCode") or ""),
                symbol=canonical_symbol(str(item.get("secCode") or "")) or "",
                sec_name=clean_markup(item.get("secName")),
                report_kind=str(item["report_kind"]),
                report_period=str(item["report_period"]),
                publication_date=publication_date_from_ms(item.get("announcementTime")),
                first_publication_date=publication_date_from_ms(item.get("first_announcement_time")),
                title=str(item["clean_title"]),
                manager="|".join(managers),
                product_names=json.dumps(products, ensure_ascii=False),
                source_url=f"{CNINFO_PDF_ROOT}{adjunct}",
                search_terms="|".join(sorted(term for term in terms if term)),
                source_snippet=snippet,
            )
        )
    return sorted(footprints, key=lambda row: (row.report_period, row.symbol))


def write_csv(path: Path, rows: list[Footprint]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(Footprint.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def collect(args: argparse.Namespace) -> dict:
    session = requests.Session()
    output_dir = Path(args.output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw: list[dict] = []
    for term in args.term:
        cache_key = hashlib.sha256(
            f"{term}|{args.start}|{args.end}|{args.page_size}|{args.max_pages}".encode("utf-8")
        ).hexdigest()[:16]
        cache_path = raw_dir / f"search_{cache_key}.json"
        if cache_path.exists() and not args.refresh:
            term_rows = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"{term}: loaded {len(term_rows)} cached results")
        else:
            term_rows = search_term(
                session,
                term,
                args.start,
                args.end,
                args.page_size,
                args.pause_seconds,
                args.max_pages,
            )
            cache_path.write_text(json.dumps(term_rows, ensure_ascii=False), encoding="utf-8")
        raw.extend(term_rows)
    footprints = select_footprints(raw)
    write_csv(output_dir / "footprints.csv", footprints)
    metadata = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "CNINFO public full-text search",
        "source_url": "https://www.cninfo.com.cn/new/fulltextSearch",
        "start": args.start,
        "end": args.end,
        "search_terms": args.term,
        "raw_result_count": len(raw),
        "periodic_footprint_count": len(footprints),
        "unique_company_count": len({row.symbol for row in footprints}),
        "limitations": [
            "Quarter-end top-shareholder snapshots are not transaction records.",
            "Positions below the disclosure threshold and hedge positions are absent.",
            "Publication dates lag the underlying report periods.",
        ],
    }
    (output_dir / "collection_summary.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect High-Flyer public holdings footprints from CNINFO.")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--pause-seconds", type=float, default=0.25)
    parser.add_argument("--max-pages", type=int, default=0, help="0 means all pages")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached search responses")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "output"),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.term:
        args.term = list(DEFAULT_TERMS)
    collect(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
