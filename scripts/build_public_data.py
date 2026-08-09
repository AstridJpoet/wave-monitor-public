#!/usr/bin/env python3
"""Convert raw scanner output into a privacy-safe static JSON payload."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


NUMERIC_FIELDS = {
    "score",
    "last_close",
    "pivot_low",
    "pivot_high",
    "retracement",
    "support",
    "invalid_below",
    "target_1",
    "target_2",
    "ma50",
    "ma144",
    "ma249",
    "zigzag_threshold",
    "structure_score",
    "position_score",
    "confirmation_score",
    "trend_score",
    "risk_score",
    "risk_reward",
    "volume_ratio",
}

PUBLIC_FIELDS = {
    "market",
    "symbol",
    "monitor_symbol",
    "name",
    "pattern",
    "wave_level",
    "signal_stage",
    "stage_label",
    "score",
    "recommend_score",
    "recommend_label",
    "last_date",
    "last_close",
    "retracement",
    "support",
    "invalid_below",
    "target_1",
    "target_2",
    "distance_to_support",
    "target_1_upside",
    "position_label",
    "scenario",
    "structure_score",
    "position_score",
    "confirmation_score",
    "trend_score",
    "risk_score",
    "risk_reward",
    "volume_ratio",
    "confirmation_detail",
    "multi_level_alignment",
}


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_symbol(symbol: Any, market: Any) -> str:
    raw = str(symbol or "").strip().upper()
    if str(market or "").strip().upper() == "CN" and raw.isdigit() and len(raw) == 6:
        return f"{raw}.SS" if raw.startswith(("5", "6", "9")) else f"{raw}.SZ"
    return raw


def recommendation_score(row: dict[str, Any]) -> float:
    return round(min(100.0, max(0.0, to_float(row.get("score")) or 0.0)), 1)


def recommendation_label(score: float) -> str:
    if score >= 85:
        return "优先"
    if score >= 75:
        return "较强"
    if score >= 65:
        return "观察"
    return "一般"


def scenario_for(row: dict[str, Any]) -> tuple[str, str]:
    close = to_float(row.get("last_close"))
    support = to_float(row.get("support"))
    invalid = to_float(row.get("invalid_below"))
    pattern = str(row.get("pattern") or "波浪候选")
    if close is None:
        return "等待数据", "等待最新价格后再核对关键位。"
    if invalid is not None and close < invalid:
        return "低于失效位", "剧本暂时失效，等待重新站回关键位。"
    if support and support > 0:
        distance = close / support - 1
        if -0.02 <= distance <= 0.035:
            position = "支撑附近"
        elif distance < -0.02:
            position = "支撑下方"
        elif distance <= 0.12:
            position = "支撑上方"
        else:
            position = "远离支撑"
    else:
        position = "等待确认"

    scripts = {
        "4浪回踩候选": "守住支撑，观察是否展开5浪；跌破失效位则放弃剧本。",
        "2浪回撤候选": "等待2浪调整止跌并出现右侧确认，失效位下方不参与。",
        "ABC/C浪末端候选": "等待C浪衰竭与转向确认，未收复支撑前只观察。",
        "疑似3浪突破": "关注突破后的回踩承接，守住支撑才保留延续预期。",
    }
    return position, scripts.get(pattern, "围绕支撑、失效位和目标位跟踪，不追逐单日波动。")


def public_row(raw: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = dict(raw)
    for field in NUMERIC_FIELDS:
        row[field] = to_float(row.get(field))
    row["market"] = str(row.get("market") or "").strip().upper()
    row["symbol"] = str(row.get("symbol") or "").strip().upper()
    row["monitor_symbol"] = normalize_symbol(row["symbol"], row["market"])
    row["multi_level_alignment"] = str(row.get("multi_level_alignment") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    row["recommend_score"] = recommendation_score(row)
    row["recommend_label"] = recommendation_label(row["recommend_score"])

    close = row.get("last_close")
    support = row.get("support")
    target = row.get("target_1")
    row["distance_to_support"] = round(close / support - 1, 6) if close and support else None
    row["target_1_upside"] = round(target / close - 1, 6) if close and target else None
    row["position_label"], row["scenario"] = scenario_for(row)
    return {field: row.get(field) for field in PUBLIC_FIELDS}


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [public_row(dict(raw)) for raw in csv.DictReader(handle)]


def read_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def build_payload(
    candidates_path: Path,
    metadata_path: Path,
    limit_per_market: int = 30,
) -> dict[str, Any]:
    rows = read_rows(candidates_path)
    raw_candidate_count = len(rows)
    metadata = read_metadata(metadata_path)
    failure_rate = to_float(metadata.get("failure_rate"))
    if failure_rate is not None and failure_rate > 0.55:
        raise RuntimeError(f"scan failure rate is too high: {failure_rate:.1%}")

    rows.sort(
        key=lambda item: (
            0 if item.get("signal_stage") == "trigger" else 1,
            -(to_float(item.get("recommend_score")) or 0),
            -(to_float(item.get("score")) or 0),
            str(item.get("market") or ""),
            str(item.get("symbol") or ""),
        )
    )
    deduped: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("market") or ""), str(row.get("symbol") or ""))
        if key in seen_symbols:
            continue
        seen_symbols.add(key)
        deduped.append(row)
    rows = deduped

    selected: list[dict[str, Any]] = []
    market_stage_counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        market = str(row.get("market") or "OTHER")
        stage = str(row.get("signal_stage") or "watch")
        key = (market, stage)
        if market_stage_counts[key] >= limit_per_market:
            continue
        market_stage_counts[key] += 1
        selected.append(row)

    market_counts = Counter(str(row.get("market") or "OTHER") for row in selected)
    trigger_count = sum(row.get("signal_stage") == "trigger" for row in selected)

    published_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    return {
        "schema_version": 2,
        "published_at": published_at,
        "metadata": {
            "scan_generated_at": metadata.get("generated_at"),
            "duration_seconds": to_float(metadata.get("duration_seconds")),
            "instrument_count": int(to_float(metadata.get("instrument_count")) or 0),
            "candidate_count": len(selected),
            "raw_candidate_count": raw_candidate_count,
            "deduped_candidate_count": len(rows),
            "trigger_count": trigger_count,
            "watch_count": len(selected) - trigger_count,
            "failure_count": int(to_float(metadata.get("failure_count")) or 0),
            "failure_rate": failure_rate,
            "a_share_source_priority": metadata.get("a_share_source_priority"),
            "a_share_fallback_active": bool(metadata.get("a_share_fallback_active")),
            "market_counts": dict(market_counts),
        },
        "candidates": selected,
        "disclaimer": "仅供研究参考，不构成投资建议。波浪识别具有主观性，历史形态不代表未来表现。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build privacy-safe static candidate data.")
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit-per-market", type=int, default=30)
    args = parser.parse_args()

    payload = build_payload(args.candidates, args.metadata, args.limit_per_market)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Published {len(payload['candidates'])} candidates to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
