#!/usr/bin/env python3
"""Persist public recommendation snapshots and calculate forward returns."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


SHANGHAI = ZoneInfo("Asia/Shanghai")
MINIMUM_SCORE = 85.0
ELIGIBLE_STAGES = {"probe", "trigger"}
EPISODE_GAP_DAYS = 7
HORIZONS = (
    ("d5", "一周", "1 week", 5),
    ("d21", "一个月", "1 month", 21),
    ("d63", "三个月", "3 months", 63),
    ("d126", "六个月", "6 months", 126),
)

SNAPSHOT_FIELDS = (
    "market",
    "symbol",
    "monitor_symbol",
    "name",
    "signal_stage",
    "stage_label",
    "recommend_score",
    "recommend_label",
    "last_date",
    "last_close",
    "pattern",
    "wave_level",
    "support",
    "invalid_below",
    "target_1",
    "target_2",
    "risk_reward",
    "market_context_score",
    "market_context_label",
)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(SHANGHAI)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def eligible_recommendations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for raw in payload.get("candidates", []):
        if not isinstance(raw, dict):
            continue
        score = as_float(raw.get("recommend_score"))
        stage = str(raw.get("signal_stage") or "").strip().lower()
        close = as_float(raw.get("last_close"))
        if score is None or score < MINIMUM_SCORE or stage not in ELIGIBLE_STAGES or close is None or close <= 0:
            continue
        row = {field: raw.get(field) for field in SNAPSHOT_FIELDS}
        row["recommend_score"] = score
        row["last_close"] = close
        recommendations.append(row)
    recommendations.sort(
        key=lambda item: (
            -(as_float(item.get("recommend_score")) or 0),
            str(item.get("market") or ""),
            str(item.get("monitor_symbol") or item.get("symbol") or ""),
        )
    )
    return recommendations


def append_daily_snapshot(
    history_dir: Path,
    payload: dict[str, Any],
    recommendations: list[dict[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    published = parse_datetime(payload.get("published_at"))
    snapshot_path = history_dir / "snapshots" / f"{published.date().isoformat()}.json"
    snapshot = load_json(
        snapshot_path,
        {"schema_version": 1, "date": published.date().isoformat(), "runs": []},
    )
    runs = snapshot.get("runs") if isinstance(snapshot, dict) else None
    if not isinstance(runs, list):
        runs = []
    published_at = published.isoformat(timespec="seconds")
    run = {
        "published_at": published_at,
        "scan_generated_at": (payload.get("metadata") or {}).get("scan_generated_at"),
        "recommendation_count": len(recommendations),
        "recommendations": recommendations,
    }
    runs = [item for item in runs if isinstance(item, dict) and item.get("published_at") != published_at]
    runs.append(run)
    runs.sort(key=lambda item: str(item.get("published_at") or ""))
    snapshot = {"schema_version": 1, "date": published.date().isoformat(), "runs": runs}
    write_json(snapshot_path, snapshot)
    return snapshot_path, run


def signal_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("market") or "").strip().upper(),
        str(row.get("monitor_symbol") or row.get("symbol") or "").strip().upper(),
    )


def new_signal(row: dict[str, Any], published_at: str) -> dict[str, Any]:
    market, symbol = signal_key(row)
    safe_stamp = published_at.replace(":", "").replace("+", "p")
    return {
        "id": f"{market}:{symbol}:{safe_stamp}",
        "market": market,
        "symbol": str(row.get("symbol") or "").strip().upper(),
        "monitor_symbol": symbol,
        "name": str(row.get("name") or symbol),
        "entry_published_at": published_at,
        "entry_date": str(row.get("last_date") or ""),
        "entry_price": as_float(row.get("last_close")),
        "entry_score": as_float(row.get("recommend_score")),
        "entry_stage": str(row.get("signal_stage") or ""),
        "entry_pattern": str(row.get("pattern") or ""),
        "wave_level": str(row.get("wave_level") or ""),
        "support": as_float(row.get("support")),
        "invalid_below": as_float(row.get("invalid_below")),
        "target_1": as_float(row.get("target_1")),
        "target_2": as_float(row.get("target_2")),
        "risk_reward": as_float(row.get("risk_reward")),
        "last_seen_at": published_at,
        "latest_score": as_float(row.get("recommend_score")),
        "latest_stage": str(row.get("signal_stage") or ""),
        "latest_date": str(row.get("last_date") or ""),
        "latest_price": as_float(row.get("last_close")),
        "current_return": 0.0,
        "outcomes": {},
    }


def update_signal_ledger(
    signals: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    published_at: str,
) -> list[dict[str, Any]]:
    current_time = parse_datetime(published_at)
    latest_by_symbol: dict[tuple[str, str], dict[str, Any]] = {}
    for signal in sorted(signals, key=lambda item: str(item.get("last_seen_at") or "")):
        latest_by_symbol[signal_key(signal)] = signal

    for row in recommendations:
        key = signal_key(row)
        existing = latest_by_symbol.get(key)
        should_extend = False
        if existing is not None:
            last_seen = parse_datetime(existing.get("last_seen_at"))
            should_extend = current_time - last_seen <= timedelta(days=EPISODE_GAP_DAYS)
        if should_extend and existing is not None:
            existing["last_seen_at"] = published_at
            existing["latest_score"] = as_float(row.get("recommend_score"))
            existing["latest_stage"] = str(row.get("signal_stage") or "")
            existing["latest_date"] = str(row.get("last_date") or "")
            existing["latest_price"] = as_float(row.get("last_close"))
            entry_price = as_float(existing.get("entry_price"))
            if entry_price and existing["latest_price"] is not None:
                existing["current_return"] = round(existing["latest_price"] / entry_price - 1, 8)
            continue
        signal = new_signal(row, published_at)
        signals.append(signal)
        latest_by_symbol[key] = signal
    return signals


def cache_candidates(cache_dir: Path, signal: dict[str, Any]) -> list[Path]:
    market, monitor_symbol = signal_key(signal)
    raw_symbol = str(signal.get("symbol") or monitor_symbol).strip().upper()
    candidates: list[Path] = []
    if market == "CN":
        code = raw_symbol.split(".", 1)[0]
        candidates.extend((cache_dir / "CN").glob(f"{code}_*.csv"))
        candidates.extend((cache_dir / "CN_YAHOO").glob(f"{monitor_symbol}_*.csv"))
    else:
        directory = "GOLD" if market == "GOLD" else "US"
        cache_symbol = raw_symbol.replace("/", "_")
        candidates.extend((cache_dir / directory).glob(f"{cache_symbol}_*.csv"))
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def load_price_history(cache_dir: Path, signal: dict[str, Any]) -> pd.DataFrame | None:
    for path in cache_candidates(cache_dir, signal):
        try:
            frame = pd.read_csv(path)
        except (OSError, pd.errors.ParserError):
            continue
        if not {"date", "close"}.issubset(frame.columns):
            continue
        out = frame.loc[:, ["date", "close"]].copy()
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out["close"] = pd.to_numeric(out["close"], errors="coerce")
        out = out.dropna().sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        if not out.empty:
            return out
    return None


def evaluate_signals(signals: list[dict[str, Any]], cache_dir: Path) -> None:
    for signal in signals:
        entry_price = as_float(signal.get("entry_price"))
        try:
            entry_date = pd.Timestamp(str(signal.get("entry_date") or ""))
        except (TypeError, ValueError):
            signal["pricing_status"] = "invalid_entry"
            continue
        if entry_price is None or entry_price <= 0:
            signal["pricing_status"] = "invalid_entry"
            continue

        history = load_price_history(cache_dir, signal)
        if history is None:
            latest_price = as_float(signal.get("latest_price"))
            if latest_price is not None:
                signal["current_return"] = round(latest_price / entry_price - 1, 8)
            signal["pricing_status"] = "waiting_for_history"
            continue

        latest = history.iloc[-1]
        signal["latest_date"] = pd.Timestamp(latest["date"]).date().isoformat()
        signal["latest_price"] = round(float(latest["close"]), 8)
        signal["current_return"] = round(float(latest["close"]) / entry_price - 1, 8)
        signal["pricing_status"] = "ready"
        future = history[history["date"] > entry_date].reset_index(drop=True)
        outcomes = signal.get("outcomes") if isinstance(signal.get("outcomes"), dict) else {}
        for key, _, _, sessions in HORIZONS:
            if key in outcomes or len(future) < sessions:
                continue
            row = future.iloc[sessions - 1]
            exit_price = float(row["close"])
            outcomes[key] = {
                "sessions": sessions,
                "date": pd.Timestamp(row["date"]).date().isoformat(),
                "price": round(exit_price, 8),
                "return": round(exit_price / entry_price - 1, 8),
            }
        signal["outcomes"] = outcomes


def horizon_summary(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for key, label, label_en, sessions in HORIZONS:
        returns = [
            as_float((signal.get("outcomes") or {}).get(key, {}).get("return"))
            for signal in signals
            if isinstance(signal.get("outcomes"), dict)
        ]
        values = [value for value in returns if value is not None]
        wins = sum(value > 0 for value in values)
        summaries.append(
            {
                "key": key,
                "label": label,
                "label_en": label_en,
                "sessions": sessions,
                "sample_count": len(values),
                "win_count": wins,
                "win_rate": round(wins / len(values), 8) if values else None,
                "average_return": round(statistics.fmean(values), 8) if values else None,
                "median_return": round(statistics.median(values), 8) if values else None,
                "best_return": round(max(values), 8) if values else None,
                "worst_return": round(min(values), 8) if values else None,
            }
        )
    return summaries


def snapshot_index(snapshot_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("*.json"), reverse=True):
        payload = load_json(path, {})
        runs = payload.get("runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            continue
        latest = runs[-1] if runs else {}
        items.append(
            {
                "date": payload.get("date") or path.stem,
                "run_count": len(runs),
                "latest_published_at": latest.get("published_at"),
                "recommendation_count": latest.get("recommendation_count", 0),
                "path": f"./data/history/snapshots/{path.name}",
            }
        )
    return items


def build_summary(signals: list[dict[str, Any]], history_dir: Path, published_at: str) -> dict[str, Any]:
    snapshots = snapshot_index(history_dir / "snapshots")
    ordered_signals = sorted(
        signals,
        key=lambda item: str(item.get("entry_published_at") or ""),
        reverse=True,
    )
    tracking_since = min(
        (str(signal.get("entry_date")) for signal in signals if signal.get("entry_date")),
        default=None,
    )
    return {
        "schema_version": 1,
        "updated_at": published_at,
        "methodology": {
            "minimum_score": MINIMUM_SCORE,
            "eligible_stages": sorted(ELIGIBLE_STAGES),
            "episode_gap_days": EPISODE_GAP_DAYS,
            "win_definition": "forward close return > 0",
        },
        "snapshot_day_count": len(snapshots),
        "snapshot_run_count": sum(int(item.get("run_count") or 0) for item in snapshots),
        "signal_count": len(signals),
        "tracking_since": tracking_since,
        "horizons": horizon_summary(signals),
        "snapshots": snapshots,
        "signals": ordered_signals,
    }


def update_history(payload_path: Path, history_dir: Path, cache_dir: Path) -> dict[str, Any]:
    payload = load_json(payload_path, {})
    if not isinstance(payload, dict) or not payload.get("published_at"):
        raise RuntimeError("current public payload has no published_at timestamp")
    published_at = parse_datetime(payload["published_at"]).isoformat(timespec="seconds")
    recommendations = eligible_recommendations(payload)
    append_daily_snapshot(history_dir, payload, recommendations)

    ledger_path = history_dir / "signals.json"
    ledger = load_json(ledger_path, {"schema_version": 1, "signals": []})
    signals = ledger.get("signals") if isinstance(ledger, dict) else None
    if not isinstance(signals, list):
        signals = []
    signals = update_signal_ledger(signals, recommendations, published_at)
    evaluate_signals(signals, cache_dir)
    write_json(ledger_path, {"schema_version": 1, "updated_at": published_at, "signals": signals})

    summary = build_summary(signals, history_dir, published_at)
    write_json(history_dir / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Update public high-score recommendation history.")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = update_history(args.payload, args.history_dir, args.cache_dir)
    print(
        f"History updated: {summary['snapshot_day_count']} days, "
        f"{summary['signal_count']} independent signals."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
