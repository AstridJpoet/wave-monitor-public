#!/usr/bin/env python3
"""Backtest a stricter MA pullback-confirmation strategy.

Strategy:
- trend filter on touch day: MA144 > MA249, and both MAs slope upward;
- pullback touch: previous close above the target MA, touch day low <= target MA;
- confirmation: within N sessions, close back above the target MA and above the
  touch day's high; buy at that confirmation close;
- exits: fixed holding periods, with and without a fixed stop loss.

This is an event study. Overlapping events are kept.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

try:
    from .ma_touch_backtest import (
        Instrument,
        SP100_FALLBACK,
        default_start,
        ensure_dir,
        fetch_a_share_daily,
        fetch_a_share_yahoo_daily,
        fetch_start_for_warmup,
        fetch_yahoo_daily,
        load_a_share_pool,
        load_us_pool,
        parse_date,
    )
except ImportError:
    from ma_touch_backtest import (
        Instrument,
        SP100_FALLBACK,
        default_start,
        ensure_dir,
        fetch_a_share_daily,
        fetch_a_share_yahoo_daily,
        fetch_start_for_warmup,
        fetch_yahoo_daily,
        load_a_share_pool,
        load_us_pool,
        parse_date,
    )


def load_pool_from_metadata(path: Path, a_top_n: int, us_top_n: int) -> list[Instrument]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    instruments = []
    a_count = 0
    us_count = 0
    for item in payload["instruments"]:
        if item["market"] == "CN":
            if a_count >= a_top_n:
                continue
            a_count += 1
        elif item["market"] == "US":
            if us_count >= us_top_n:
                continue
            us_count += 1
        instruments.append(
            Instrument(
                market=item["market"],
                symbol=item["symbol"],
                name=item["name"],
                source=item.get("source", "metadata pool"),
                weight=item.get("weight"),
            )
        )
    return instruments


def add_prior_mas(prices: pd.DataFrame, windows: Iterable[int]) -> pd.DataFrame:
    df = prices.copy()
    df["date_only"] = df["date"].dt.date
    for window in sorted(set(windows)):
        df[f"ma{window}"] = df["close"].rolling(window, min_periods=window).mean().shift(1)
    return df


def fixed_horizon_return(
    df: pd.DataFrame,
    entry_i: int,
    hold_days: int,
    entry_price: float,
    stop_loss: float,
) -> tuple[dict, dict]:
    exit_i = entry_i + hold_days
    exit_close = df.at[exit_i, "close"]
    no_stop = {
        "exit_model": "no_stop",
        "exit_date": df.at[exit_i, "date_only"].isoformat(),
        "exit_close": exit_close,
        "return": exit_close / entry_price - 1,
        "stop_hit": False,
    }

    stop_price = entry_price * (1 - stop_loss)
    stopped = None
    for i in range(entry_i + 1, exit_i + 1):
        if df.at[i, "low"] <= stop_price:
            stopped = i
            break
    if stopped is None:
        stop_exit_close = exit_close
        stop_exit_i = exit_i
        stop_hit = False
    else:
        stop_exit_close = stop_price
        stop_exit_i = stopped
        stop_hit = True

    with_stop = {
        "exit_model": f"stop_{int(stop_loss * 100)}pct",
        "exit_date": df.at[stop_exit_i, "date_only"].isoformat(),
        "exit_close": stop_exit_close,
        "return": stop_exit_close / entry_price - 1,
        "stop_hit": stop_hit,
    }
    return no_stop, with_stop


def find_confirmed_events(
    instrument: Instrument,
    prices: pd.DataFrame,
    backtest_start: date,
    backtest_end: date,
    ma_windows: Iterable[int],
    hold_days: Iterable[int],
    confirm_days: int,
    slope_lookback: int,
    cooldown_days: int,
    stop_loss: float,
) -> list[dict]:
    all_ma_windows = sorted(set(ma_windows) | {144, 249})
    df = add_prior_mas(prices, all_ma_windows)
    max_hold = max(hold_days)
    events: list[dict] = []

    for window in ma_windows:
        ma_col = f"ma{window}"
        last_entry_i = -10_000
        for touch_i in range(max(250, slope_lookback + 1), len(df)):
            touch_date = df.at[touch_i, "date_only"]
            if touch_date < backtest_start or touch_date > backtest_end:
                continue

            ma_value = df.at[touch_i, ma_col]
            ma144 = df.at[touch_i, "ma144"]
            ma249 = df.at[touch_i, "ma249"]
            prev_ma_value = df.at[touch_i - 1, ma_col]
            if pd.isna(ma_value) or pd.isna(ma144) or pd.isna(ma249) or pd.isna(prev_ma_value):
                continue

            slope_i = touch_i - slope_lookback
            ma144_then = df.at[slope_i, "ma144"]
            ma249_then = df.at[slope_i, "ma249"]
            if pd.isna(ma144_then) or pd.isna(ma249_then):
                continue

            trend_ok = ma144 > ma249 and ma144 > ma144_then and ma249 > ma249_then
            touched_from_above = df.at[touch_i - 1, "close"] > prev_ma_value and df.at[touch_i, "low"] <= ma_value
            cooled_down = touch_i - last_entry_i >= cooldown_days
            if not (trend_ok and touched_from_above and cooled_down):
                continue

            confirm_i = None
            max_confirm_i = min(touch_i + confirm_days, len(df) - max_hold - 1)
            for candidate_i in range(touch_i + 1, max_confirm_i + 1):
                candidate_ma = df.at[candidate_i, ma_col]
                if pd.isna(candidate_ma):
                    continue
                confirms = df.at[candidate_i, "close"] > candidate_ma and df.at[candidate_i, "close"] > df.at[touch_i, "high"]
                if confirms:
                    confirm_i = candidate_i
                    break
            if confirm_i is None:
                continue

            entry_date = df.at[confirm_i, "date_only"]
            if entry_date < backtest_start or entry_date > backtest_end:
                continue

            entry_price = df.at[confirm_i, "close"]
            base = {
                "market": instrument.market,
                "symbol": instrument.symbol,
                "name": instrument.name,
                "source": instrument.source,
                "weight": instrument.weight,
                "touch_date": touch_date.isoformat(),
                "entry_date": entry_date.isoformat(),
                "ma": window,
                "entry_close": entry_price,
                "touch_high": df.at[touch_i, "high"],
                "touch_low": df.at[touch_i, "low"],
                "touch_ma_value": ma_value,
                "ma144": ma144,
                "ma249": ma249,
                "confirm_lag_days": confirm_i - touch_i,
            }
            for days in hold_days:
                no_stop, with_stop = fixed_horizon_return(df, confirm_i, days, entry_price, stop_loss)
                for exit_row in [no_stop, with_stop]:
                    row = dict(base)
                    row.update(exit_row)
                    row.update(
                        {
                            "hold_days": days,
                            "win": row["return"] > 0,
                        }
                    )
                    events.append(row)
            last_entry_i = confirm_i
    return events


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()

    def stop_rate(series: pd.Series) -> float:
        return series.astype(bool).mean()

    grouped = events.groupby(["market", "ma", "hold_days", "exit_model"], dropna=False)
    summary = grouped.agg(
        trades=("return", "count"),
        win_rate=("return", lambda s: (s > 0).mean()),
        avg_return=("return", "mean"),
        median_return=("return", "median"),
        p05_return=("return", lambda s: s.quantile(0.05)),
        p95_return=("return", lambda s: s.quantile(0.95)),
        best_return=("return", "max"),
        worst_return=("return", "min"),
        stop_rate=("stop_hit", stop_rate),
    ).reset_index()

    overall = (
        events.groupby(["market", "hold_days", "exit_model"], dropna=False)
        .agg(
            trades=("return", "count"),
            win_rate=("return", lambda s: (s > 0).mean()),
            avg_return=("return", "mean"),
            median_return=("return", "median"),
            p05_return=("return", lambda s: s.quantile(0.05)),
            p95_return=("return", lambda s: s.quantile(0.95)),
            best_return=("return", "max"),
            worst_return=("return", "min"),
            stop_rate=("stop_hit", stop_rate),
        )
        .reset_index()
    )
    overall.insert(1, "ma", "ANY")
    return pd.concat([summary, overall], ignore_index=True)


def display_summary(summary: pd.DataFrame) -> pd.DataFrame:
    out = summary.copy()
    for col in [
        "win_rate",
        "avg_return",
        "median_return",
        "p05_return",
        "p95_return",
        "best_return",
        "worst_return",
        "stop_rate",
    ]:
        if col in out.columns:
            out[col] = (out[col].astype(float) * 100).map(lambda value: f"{value:.2f}%")
    return out


def save_metadata(
    path: Path,
    args: argparse.Namespace,
    instruments: list[Instrument],
    failures: list[dict],
) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "instrument_count": len(instruments),
        "instruments": [asdict(item) for item in instruments],
        "failures": failures,
        "notes": [
            "Enhanced strategy: trend filter + MA touch + confirmation breakout + fixed hold exits.",
            "MA values are based on prior closes via shifted rolling averages.",
            "Stop-loss exit assumes fills at the stop price when intraday low crosses the stop; gap risk is not modeled.",
            "Current index constituents introduce survivorship bias.",
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest confirmed MA pullback strategy.")
    parser.add_argument("--end", default=date.today().isoformat(), help="Backtest end date, YYYY-MM-DD.")
    parser.add_argument("--start", default=None, help="Backtest start date, YYYY-MM-DD. Default: end - 5 years.")
    parser.add_argument("--a-top-n", type=int, default=100, help="Top CSI300 weighted A-shares to include.")
    parser.add_argument("--us-top-n", type=int, default=len(SP100_FALLBACK), help="S&P 100 US symbols to include.")
    parser.add_argument("--no-a", action="store_true", help="Skip A-shares.")
    parser.add_argument("--no-us", action="store_true", help="Skip US stocks.")
    parser.add_argument("--pool-metadata", default=None, help="Reuse instruments from an earlier run_metadata.json.")
    parser.add_argument("--refresh-us-pool", action="store_true", help="Fetch the latest S&P 100 list from Wikipedia.")
    parser.add_argument("--a-history-source", choices=["yahoo", "eastmoney"], default="yahoo")
    parser.add_argument("--ma", type=int, nargs="+", default=[144, 249], help="Target MA touch windows.")
    parser.add_argument("--hold-days", type=int, nargs="+", default=[5, 21], help="Holding periods in trading days.")
    parser.add_argument("--confirm-days", type=int, default=3, help="Max sessions after touch to wait for confirmation.")
    parser.add_argument("--slope-lookback", type=int, default=20, help="Sessions used to test upward MA slope.")
    parser.add_argument("--cooldown-days", type=int, default=10, help="Minimum sessions between same MA entries per stock.")
    parser.add_argument("--stop-loss", type=float, default=0.05, help="Fixed stop loss, e.g. 0.05 means -5%.")
    parser.add_argument("--cache-dir", default="data_cache", help="CSV cache directory.")
    parser.add_argument("--out-dir", default="results_confirmed", help="Output directory.")
    parser.add_argument("--ignore-cache", action="store_true", help="Refetch all histories.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between history requests.")
    args = parser.parse_args()

    end = parse_date(args.end)
    start = parse_date(args.start) if args.start else default_start(end)
    fetch_start = fetch_start_for_warmup(start, max(max(args.ma), 249))
    cache_dir = Path(args.cache_dir)
    out_dir = ensure_dir(Path(args.out_dir))

    if args.pool_metadata:
        instruments = load_pool_from_metadata(Path(args.pool_metadata), args.a_top_n, args.us_top_n)
        if args.no_a:
            instruments = [item for item in instruments if item.market != "CN"]
        if args.no_us:
            instruments = [item for item in instruments if item.market != "US"]
    else:
        instruments: list[Instrument] = []
        if not args.no_a:
            instruments.extend(load_a_share_pool(args.a_top_n))
        if not args.no_us:
            instruments.extend(load_us_pool(args.us_top_n, args.refresh_us_pool))

    print(f"Backtest window: {start} to {end}; fetch starts {fetch_start} for MA warmup")
    print(f"Instrument count: {len(instruments)}")

    all_events: list[dict] = []
    failures: list[dict] = []
    use_cache = not args.ignore_cache
    for idx, instrument in enumerate(instruments, start=1):
        try:
            if instrument.market == "CN":
                if args.a_history_source == "eastmoney":
                    prices = fetch_a_share_daily(instrument.symbol, fetch_start, end, cache_dir, use_cache)
                else:
                    prices = fetch_a_share_yahoo_daily(instrument.symbol, fetch_start, end, cache_dir, use_cache)
            elif instrument.market == "US":
                prices = fetch_yahoo_daily(instrument.symbol, fetch_start, end, cache_dir, use_cache)
            else:
                raise ValueError(f"Unknown market: {instrument.market}")
            events = find_confirmed_events(
                instrument=instrument,
                prices=prices,
                backtest_start=start,
                backtest_end=end,
                ma_windows=args.ma,
                hold_days=args.hold_days,
                confirm_days=args.confirm_days,
                slope_lookback=args.slope_lookback,
                cooldown_days=args.cooldown_days,
                stop_loss=args.stop_loss,
            )
            all_events.extend(events)
            print(f"[{idx:03d}/{len(instruments)}] {instrument.market} {instrument.symbol}: {len(events)} rows")
        except Exception as exc:
            failures.append(
                {
                    "market": instrument.market,
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"[{idx:03d}/{len(instruments)}] {instrument.market} {instrument.symbol}: FAILED {exc}")
        time.sleep(args.sleep)

    events_df = pd.DataFrame(all_events)
    summary_df = summarize(events_df)

    events_path = out_dir / "ma_touch_confirm_trades.csv"
    summary_path = out_dir / "ma_touch_confirm_summary.csv"
    meta_path = out_dir / "run_metadata.json"
    events_df.to_csv(events_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    save_metadata(meta_path, args, instruments, failures)

    if not summary_df.empty:
        print("\nSummary:")
        print(display_summary(summary_df).to_string(index=False))
    else:
        print("\nNo events found.")
    print(f"\nSaved: {summary_path}")
    print(f"Saved: {events_path}")
    print(f"Saved: {meta_path}")
    if failures:
        print(f"Failures: {len(failures)} symbols; see metadata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
