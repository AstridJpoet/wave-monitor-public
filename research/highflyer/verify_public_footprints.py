#!/usr/bin/env python3
"""Verify CNINFO search hits against shareholder-table context in PDFs."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from pypdf import PdfReader

from research.highflyer.collect_public_footprints import MANAGER_ALIASES, extract_product_names


SHAREHOLDER_MARKERS = (
    "前十名股东",
    "前10名股东",
    "前十名无限售条件股东",
    "前10名无限售条件股东",
    "前十名无限售流通股股东",
    "前10名无限售流通股股东",
    "股东名称",
)
INVESTMENT_MARKERS = (
    "私募基金投资情况",
    "证券投资情况",
    "认购了",
    "认购宁波幻方",
    "投资金额",
    "公允价值变动",
)


@dataclass(frozen=True)
class Evidence:
    status: str
    pages: str
    context: str
    manager: str
    product_names: str


def compact_text(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def manager_matches(text: str) -> list[tuple[str, str, int]]:
    compact = compact_text(text)
    matches: list[tuple[str, str, int]] = []
    for legal_name, short_name in MANAGER_ALIASES.items():
        start = compact.find(legal_name)
        while start >= 0:
            matches.append((legal_name, short_name, start))
            start = compact.find(legal_name, start + len(legal_name))
    return sorted(matches, key=lambda item: item[2])


def is_shareholder_context(previous_page: str, page: str, next_page: str = "") -> bool:
    """Return true when a manager occurrence belongs to a shareholder table."""
    current = compact_text(page)
    previous = compact_text(previous_page)[-3500:]
    following = compact_text(next_page)[:1200]
    matches = manager_matches(current)
    if not matches:
        return False

    for legal_name, _short_name, position in matches:
        local = current[max(0, position - 900) : position + len(legal_name) + 900]
        if any(marker in local for marker in INVESTMENT_MARKERS):
            continue
        before = (previous + current[:position])[-5000:]
        after = (current[position:] + following)[:2500]
        if any(marker in before or marker in after for marker in SHAREHOLDER_MARKERS):
            return True
    return False


def evidence_context(previous_page: str, page: str, next_page: str = "") -> str:
    current = compact_text(page)
    previous = compact_text(previous_page)[-3500:]
    following = compact_text(next_page)[:1200]
    for legal_name, _short_name, position in manager_matches(current):
        local = current[max(0, position - 500) : position + len(legal_name) + 700]
        if any(marker in local for marker in INVESTMENT_MARKERS):
            continue
        combined = previous + current + following
        absolute_position = len(previous) + position
        excerpt = combined[max(0, absolute_position - 280) : absolute_position + len(legal_name) + 420]
        return excerpt[:900]
    return ""


def request_pdf(session: requests.Session, url: str, retries: int = 4) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(
                url,
                timeout=45,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; public-disclosure-research/1.0)",
                    "Referer": "https://www.cninfo.com.cn/",
                },
            )
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise RuntimeError("response is not a PDF")
            return response.content
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"PDF download failed: {last_error}")


def read_pdf_pages(content: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(content), strict=False)
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def read_or_extract_pages(cache_path: Path, content: bytes) -> list[str]:
    text_cache = cache_path.with_suffix(".pages.json.gz")
    if text_cache.exists():
        with gzip.open(text_cache, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return [str(page) for page in payload]
    pages = read_pdf_pages(content)
    with gzip.open(text_cache, "wt", encoding="utf-8") as handle:
        json.dump(pages, handle, ensure_ascii=False)
    return pages


def verify_pages(pages: list[str]) -> Evidence:
    verified_pages: list[int] = []
    contexts: list[str] = []
    managers: list[str] = []
    products: list[str] = []
    manager_was_found = False

    for index, page in enumerate(pages):
        page_matches = manager_matches(page)
        if not page_matches:
            continue
        manager_was_found = True
        previous_page = pages[index - 1] if index else ""
        next_page = pages[index + 1] if index + 1 < len(pages) else ""
        if not is_shareholder_context(previous_page, page, next_page):
            continue
        verified_pages.append(index + 1)
        context = evidence_context(previous_page, page, next_page)
        if context and context not in contexts:
            contexts.append(context)
        for _legal_name, short_name, _position in page_matches:
            if short_name not in managers:
                managers.append(short_name)
        for product in extract_product_names(context):
            if product not in products:
                products.append(product)

    if verified_pages:
        return Evidence(
            status="verified_shareholder",
            pages="|".join(str(page) for page in verified_pages),
            context=" || ".join(contexts)[:1800],
            manager="|".join(managers),
            product_names="|".join(products),
        )
    status = "manager_found_non_shareholder" if manager_was_found else "manager_not_found"
    return Evidence(status=status, pages="", context="", manager="", product_names="")


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def verify(args: argparse.Namespace) -> dict[str, int]:
    input_path = Path(args.input)
    output_path = Path(args.output)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(input_path)
    if args.limit:
        rows = rows[: args.limit]
    session = requests.Session()
    checked: list[dict[str, str]] = []
    previous_by_url: dict[str, dict[str, str]] = {}
    if output_path.exists() and not args.refresh:
        previous_by_url = {row.get("source_url", ""): row for row in load_rows(output_path)}

    for number, row in enumerate(rows, 1):
        url = row["source_url"]
        cache_name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20] + ".pdf"
        cache_path = cache_dir / cache_name
        previous = previous_by_url.get(url)
        if previous and previous.get("evidence_status"):
            context = previous.get("evidence_context", "")
            evidence = Evidence(
                status=previous["evidence_status"],
                pages=previous.get("evidence_pages", ""),
                context=context,
                manager=previous.get("verified_manager", ""),
                product_names="|".join(extract_product_names(context)),
            )
        else:
            try:
                if cache_path.exists() and not args.refresh:
                    content = cache_path.read_bytes()
                else:
                    content = request_pdf(session, url)
                    cache_path.write_bytes(content)
                    time.sleep(max(0.0, args.pause_seconds))
                evidence = verify_pages(read_or_extract_pages(cache_path, content))
            except Exception as exc:
                evidence = Evidence(
                    status="pdf_error",
                    pages="",
                    context=str(exc)[:400],
                    manager="",
                    product_names="",
                )
        result = dict(row)
        result.update(
            {
                "evidence_status": evidence.status,
                "evidence_pages": evidence.pages,
                "evidence_context": evidence.context,
                "verified_manager": evidence.manager,
                "verified_product_names": evidence.product_names,
            }
        )
        checked.append(result)
        print(f"[{number}/{len(rows)}] {row['symbol']} {evidence.status} {evidence.pages}")

    fields = list(checked[0]) if checked else []
    write_rows(output_path, checked, fields)
    verified_rows = [row for row in checked if row["evidence_status"] == "verified_shareholder"]
    verified_output = output_path.with_name("verified_footprints.csv")
    write_rows(verified_output, verified_rows, fields)
    summary = {
        "input_count": len(checked),
        "verified_count": len(verified_rows),
        "non_shareholder_count": sum(
            row["evidence_status"] == "manager_found_non_shareholder" for row in checked
        ),
        "manager_not_found_count": sum(row["evidence_status"] == "manager_not_found" for row in checked),
        "pdf_error_count": sum(row["evidence_status"] == "pdf_error" for row in checked),
    }
    print(summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Verify High-Flyer footprints in CNINFO PDFs.")
    parser.add_argument("--input", default=str(root / "output" / "footprints.csv"))
    parser.add_argument("--output", default=str(root / "output" / "verification_audit.csv"))
    parser.add_argument("--cache-dir", default=str(root / "cache" / "pdfs"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pause-seconds", type=float, default=0.3)
    parser.add_argument("--refresh", action="store_true")
    return parser


def main() -> int:
    verify(build_parser().parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
