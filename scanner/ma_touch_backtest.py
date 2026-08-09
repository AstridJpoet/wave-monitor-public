#!/usr/bin/env python3
"""Backtest buying daily MA144/MA249 touches for A-share and US stocks.

Default event rule:
- use adjusted daily OHLC data;
- compute MA with prior closes only: rolling(close, N).mean().shift(1);
- buy at the signal day's adjusted close when price falls from above and
  the intraday low touches or crosses the MA;
- require recent weakness: at least 3 down closes in the last 5 sessions;
- exit after 5 and 21 trading sessions at adjusted close.

This is an event study, not a portfolio simulator. Overlapping trades are kept.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests


SP100_FALLBACK = [
    "AAPL",
    "ABBV",
    "ABT",
    "ACN",
    "ADBE",
    "AMAT",
    "AMD",
    "AMGN",
    "AMT",
    "AMZN",
    "AVGO",
    "AXP",
    "BA",
    "BAC",
    "BKNG",
    "BLK",
    "BMY",
    "BNY",
    "BRK-B",
    "C",
    "CAT",
    "CL",
    "CMCSA",
    "COF",
    "COP",
    "COST",
    "CRM",
    "CSCO",
    "CVS",
    "CVX",
    "DE",
    "DHR",
    "DIS",
    "DUK",
    "EMR",
    "FDX",
    "GD",
    "GE",
    "GEV",
    "GILD",
    "GM",
    "GOOG",
    "GOOGL",
    "GS",
    "HD",
    "HON",
    "IBM",
    "INTC",
    "INTU",
    "ISRG",
    "JNJ",
    "JPM",
    "KO",
    "LIN",
    "LLY",
    "LMT",
    "LOW",
    "LRCX",
    "MA",
    "MCD",
    "MDLZ",
    "MDT",
    "META",
    "MMM",
    "MO",
    "MRK",
    "MS",
    "MSFT",
    "MU",
    "NEE",
    "NFLX",
    "NKE",
    "NOW",
    "NVDA",
    "ORCL",
    "PEP",
    "PFE",
    "PG",
    "PLTR",
    "PM",
    "QCOM",
    "RTX",
    "SBUX",
    "SCHW",
    "SO",
    "SPG",
    "T",
    "TMO",
    "TMUS",
    "TSLA",
    "TXN",
    "UBER",
    "UNH",
    "UNP",
    "UPS",
    "USB",
    "V",
    "VZ",
    "WFC",
    "WMT",
    "XOM",
]

A_SHARE_FALLBACK = [
    ("300750", "宁德时代"),
    ("600519", "贵州茅台"),
    ("601318", "中国平安"),
    ("600036", "招商银行"),
    ("000858", "五粮液"),
    ("601166", "兴业银行"),
    ("601398", "工商银行"),
    ("601288", "农业银行"),
    ("601988", "中国银行"),
    ("601939", "建设银行"),
    ("600900", "长江电力"),
    ("601899", "紫金矿业"),
    ("600030", "中信证券"),
    ("601088", "中国神华"),
    ("600028", "中国石化"),
    ("601857", "中国石油"),
    ("600276", "恒瑞医药"),
    ("000333", "美的集团"),
    ("000651", "格力电器"),
    ("002594", "比亚迪"),
    ("300760", "迈瑞医疗"),
    ("600887", "伊利股份"),
    ("601012", "隆基绿能"),
    ("600809", "山西汾酒"),
    ("002714", "牧原股份"),
    ("600031", "三一重工"),
    ("601668", "中国建筑"),
    ("600309", "万华化学"),
    ("600406", "国电南瑞"),
    ("603259", "药明康德"),
    ("300059", "东方财富"),
    ("002475", "立讯精密"),
    ("000725", "京东方A"),
    ("002415", "海康威视"),
    ("600690", "海尔智家"),
    ("600050", "中国联通"),
    ("601138", "工业富联"),
    ("603501", "韦尔股份"),
    ("688981", "中芯国际"),
    ("688256", "寒武纪"),
]


@dataclass(frozen=True)
class Instrument:
    market: str
    symbol: str
    name: str
    source: str
    weight: float | None = None


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def default_start(end: date) -> date:
    return end - timedelta(days=365 * 5 + 1)


def fetch_start_for_warmup(start: date, max_ma: int) -> date:
    return start - timedelta(days=math.ceil(max_ma * 1.9))


def yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_cached_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, parse_dates=["date"])
    except Exception:
        return None


def write_cache(df: pd.DataFrame, path: Path) -> None:
    ensure_dir(path.parent)
    df.to_csv(path, index=False)


def load_a_share_pool(top_n: int) -> list[Instrument]:
    import akshare as ak

    try:
        df = ak.stock_zh_a_spot_em().copy()
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        empty_numeric = pd.Series(np.nan, index=df.index, dtype=float)
        df["总市值"] = pd.to_numeric(df.get("总市值", empty_numeric), errors="coerce")
        df["成交额"] = pd.to_numeric(df.get("成交额", empty_numeric), errors="coerce")
        df["最新价"] = pd.to_numeric(df.get("最新价", empty_numeric), errors="coerce")
        df = df[
            df["代码"].str.startswith(("0", "3", "6"))
            & ~df["名称"].astype(str).str.upper().str.contains("ST|退", regex=True)
        ]
        if df["最新价"].notna().any():
            df = df[df["最新价"] >= 3]
        if df["总市值"].notna().any():
            df = df[df["总市值"] >= 3_000_000_000]
        if df["成交额"].notna().any():
            df = df[df["成交额"] >= 50_000_000]

        amount_rank = df["成交额"].rank(pct=True).fillna(0)
        cap_rank = df["总市值"].rank(pct=True).fillna(0)
        df["扫描优先级"] = amount_rank * 0.7 + cap_rank * 0.3
        df = df.sort_values(["扫描优先级", "总市值"], ascending=False).head(top_n)
        return [
            Instrument(
                market="CN",
                symbol=str(row["代码"]),
                name=str(row["名称"]),
                source="Eastmoney liquid A-share universe",
                weight=float(row["扫描优先级"]),
            )
            for _, row in df.iterrows()
        ]
    except Exception as exc:
        print(f"Warning: failed to load Eastmoney liquid A-share pool: {exc}")

    try:
        df = ak.index_stock_cons_weight_csindex(symbol="000300")
        df = df.sort_values("权重", ascending=False).head(top_n)
        return [
            Instrument(
                market="CN",
                symbol=str(row["成分券代码"]).zfill(6),
                name=str(row["成分券名称"]),
                source="CSI300 current top weights fallback",
                weight=float(row["权重"]),
            )
            for _, row in df.iterrows()
        ]
    except Exception as exc:
        print(f"Warning: failed to load CSI300 fallback pool: {exc}")

    return [
        Instrument(market="CN", symbol=symbol, name=name, source="Static A-share fallback")
        for symbol, name in A_SHARE_FALLBACK[:top_n]
    ]


def load_sp100_from_wikipedia(timeout: int = 20) -> list[Instrument]:
    url = "https://en.wikipedia.org/wiki/S%26P_100"
    response = request_get_with_retries(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    tables = pd.read_html(io.StringIO(response.text))
    for table in tables:
        if "Symbol" in table.columns and "Name" in table.columns:
            return [
                Instrument(
                    market="US",
                    symbol=str(row["Symbol"]).replace(".", "-"),
                    name=str(row["Name"]),
                    source="S&P 100 current constituents",
                )
                for _, row in table.iterrows()
            ]
    raise RuntimeError("Could not find S&P 100 table on Wikipedia")


def load_us_broad_pool_from_wikipedia(timeout: int = 20) -> list[Instrument]:
    sources = [
        (
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            ("Ticker", "Symbol"),
            ("Company", "Security", "Name"),
            "Nasdaq-100 current constituents",
        ),
        (
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            ("Symbol", "Ticker"),
            ("Security", "Company", "Name"),
            "S&P 500 current constituents",
        ),
    ]
    instruments: list[Instrument] = []
    seen: set[str] = set()
    for url, symbol_names, company_names, source_name in sources:
        response = request_get_with_retries(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        for table in pd.read_html(io.StringIO(response.text)):
            table = table.copy()
            table.columns = [str(column[-1] if isinstance(column, tuple) else column) for column in table.columns]
            symbol_column = next((name for name in symbol_names if name in table.columns), None)
            company_column = next((name for name in company_names if name in table.columns), None)
            if symbol_column is None or company_column is None:
                continue
            for _, row in table.iterrows():
                symbol = str(row[symbol_column]).strip().upper().replace(".", "-")
                if not symbol or symbol == "NAN" or symbol in seen:
                    continue
                seen.add(symbol)
                instruments.append(
                    Instrument(
                        market="US",
                        symbol=symbol,
                        name=str(row[company_column]).strip(),
                        source=source_name,
                    )
                )
            break
    if not instruments:
        raise RuntimeError("Could not load Nasdaq-100 or S&P 500 constituents")
    return instruments


def load_us_pool(top_n: int, refresh: bool) -> list[Instrument]:
    if refresh or top_n > len(SP100_FALLBACK):
        try:
            broad = load_us_broad_pool_from_wikipedia()
            fallback = [
                Instrument(market="US", symbol=symbol, name=symbol, source="S&P 100 fallback list")
                for symbol in SP100_FALLBACK
            ]
            combined = fallback + broad
            seen: set[str] = set()
            unique = []
            for instrument in combined:
                if instrument.symbol in seen:
                    continue
                seen.add(instrument.symbol)
                unique.append(instrument)
            return unique[:top_n]
        except Exception as exc:
            print(f"Warning: failed to refresh broad US pool from Wikipedia: {exc}")
    return [
        Instrument(market="US", symbol=symbol, name=symbol, source="S&P 100 fallback list")
        for symbol in SP100_FALLBACK[:top_n]
    ]


def request_get_with_retries(url: str, attempts: int = 3, **kwargs) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            time.sleep(0.75 * attempt)
    assert last_exc is not None
    raise last_exc


def normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["date", "open", "high", "low", "close", "volume"]
    out = df.loc[:, columns].copy()
    out["date"] = pd.to_datetime(out["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "open", "high", "low", "close"])
    out = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return out


def fetch_a_share_daily(symbol: str, start: date, end: date, cache_dir: Path, use_cache: bool) -> pd.DataFrame:
    cache_path = cache_dir / "CN" / f"{symbol}_{yyyymmdd(start)}_{yyyymmdd(end)}.csv"
    if use_cache:
        cached = read_cached_csv(cache_path)
        if cached is not None:
            return normalize_ohlc(cached)

    import akshare as ak

    raw = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=yyyymmdd(start),
        end_date=yyyymmdd(end),
        adjust="qfq",
        timeout=20,
    )
    if raw.empty:
        raise RuntimeError("empty A-share history")
    df = pd.DataFrame(
        {
            "date": raw["日期"],
            "open": raw["开盘"],
            "high": raw["最高"],
            "low": raw["最低"],
            "close": raw["收盘"],
            "volume": raw.get("成交量", np.nan),
        }
    )
    df = normalize_ohlc(df)
    write_cache(df, cache_path)
    return df


def yahoo_symbol_for_a_share(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return f"{symbol}.SS"
    return f"{symbol}.SZ"


def fetch_yahoo_daily(
    symbol: str,
    start: date,
    end: date,
    cache_dir: Path,
    use_cache: bool,
    cache_market: str = "US",
) -> pd.DataFrame:
    cache_symbol = symbol.replace("/", "_")
    cache_path = cache_dir / cache_market / f"{cache_symbol}_{yyyymmdd(start)}_{yyyymmdd(end)}.csv"
    if use_cache:
        cached = read_cached_csv(cache_path)
        if cached is not None:
            return normalize_ohlc(cached)

    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    response = request_get_with_retries(
        url,
        params=params,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    payload = response.json()
    result = payload.get("chart", {}).get("result")
    if not result:
        raise RuntimeError(payload.get("chart", {}).get("error") or "empty Yahoo response")

    item = result[0]
    timestamps = item.get("timestamp", [])
    quote = item.get("indicators", {}).get("quote", [{}])[0]
    adjclose = item.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose")
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(timestamps, unit="s", utc=True).date,
            "open": quote.get("open", []),
            "high": quote.get("high", []),
            "low": quote.get("low", []),
            "close_raw": quote.get("close", []),
            "volume": quote.get("volume", []),
        }
    )
    raw["adjclose"] = adjclose if adjclose is not None else raw["close_raw"]
    raw["close_raw"] = pd.to_numeric(raw["close_raw"], errors="coerce")
    raw["adjclose"] = pd.to_numeric(raw["adjclose"], errors="coerce")
    factor = raw["adjclose"] / raw["close_raw"]
    factor = factor.replace([np.inf, -np.inf], np.nan).fillna(1.0)
    df = pd.DataFrame(
        {
            "date": raw["date"],
            "open": pd.to_numeric(raw["open"], errors="coerce") * factor,
            "high": pd.to_numeric(raw["high"], errors="coerce") * factor,
            "low": pd.to_numeric(raw["low"], errors="coerce") * factor,
            "close": raw["adjclose"],
            "volume": raw["volume"],
        }
    )
    df = normalize_ohlc(df)
    write_cache(df, cache_path)
    return df


def fetch_a_share_yahoo_daily(symbol: str, start: date, end: date, cache_dir: Path, use_cache: bool) -> pd.DataFrame:
    return fetch_yahoo_daily(
        yahoo_symbol_for_a_share(symbol),
        start,
        end,
        cache_dir,
        use_cache,
        cache_market="CN_YAHOO",
    )


def count_recent_down_closes(close: pd.Series, lookback: int) -> pd.Series:
    down = close.diff() < 0
    return down.rolling(lookback, min_periods=lookback).sum()


def find_touch_events(
    instrument: Instrument,
    prices: pd.DataFrame,
    backtest_start: date,
    backtest_end: date,
    ma_windows: Iterable[int],
    hold_days: Iterable[int],
    down_lookback: int,
    min_down_days: int,
    cooldown_days: int,
) -> list[dict]:
    df = prices.copy()
    df["date_only"] = df["date"].dt.date
    events: list[dict] = []
    recent_down = count_recent_down_closes(df["close"], down_lookback)

    for window in ma_windows:
        ma_col = f"ma{window}"
        df[ma_col] = df["close"].rolling(window, min_periods=window).mean().shift(1)
        last_signal_i = -10_000

        for i in range(1, len(df)):
            trade_date = df.at[i, "date_only"]
            if trade_date < backtest_start or trade_date > backtest_end:
                continue
            if i + max(hold_days) >= len(df):
                continue
            ma_value = df.at[i, ma_col]
            prev_ma_value = df.at[i - 1, ma_col]
            if pd.isna(ma_value) or pd.isna(prev_ma_value):
                continue

            prev_close = df.at[i - 1, "close"]
            low = df.at[i, "low"]
            high = df.at[i, "high"]
            close = df.at[i, "close"]

            touches_from_above = prev_close > prev_ma_value and low <= ma_value <= high
            weak_recently = recent_down.iat[i] >= min_down_days
            cooled_down = i - last_signal_i >= cooldown_days
            if not (touches_from_above and weak_recently and cooled_down):
                continue

            base = {
                "market": instrument.market,
                "symbol": instrument.symbol,
                "name": instrument.name,
                "source": instrument.source,
                "weight": instrument.weight,
                "date": trade_date.isoformat(),
                "ma": window,
                "entry_close": close,
                "ma_value": ma_value,
                "recent_down_days": int(recent_down.iat[i]),
            }
            for days in hold_days:
                exit_i = i + days
                exit_close = df.at[exit_i, "close"]
                ret = exit_close / close - 1
                row = dict(base)
                row.update(
                    {
                        "hold_days": days,
                        "exit_date": df.at[exit_i, "date_only"].isoformat(),
                        "exit_close": exit_close,
                        "return": ret,
                        "win": ret > 0,
                    }
                )
                events.append(row)
            last_signal_i = i

    return events


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()

    def pct05(values: pd.Series) -> float:
        return values.quantile(0.05)

    def pct95(values: pd.Series) -> float:
        return values.quantile(0.95)

    grouped = events.groupby(["market", "ma", "hold_days"], dropna=False)["return"]
    summary = grouped.agg(
        trades="count",
        win_rate=lambda s: (s > 0).mean(),
        avg_return="mean",
        median_return="median",
        p05_return=pct05,
        p95_return=pct95,
        best_return="max",
        worst_return="min",
    ).reset_index()

    overall = (
        events.groupby(["market", "hold_days"], dropna=False)["return"]
        .agg(
            trades="count",
            win_rate=lambda s: (s > 0).mean(),
            avg_return="mean",
            median_return="median",
            p05_return=pct05,
            p95_return=pct95,
            best_return="max",
            worst_return="min",
        )
        .reset_index()
    )
    overall.insert(1, "ma", "ANY")
    return pd.concat([summary, overall], ignore_index=True)


def add_display_percentages(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in [
        "win_rate",
        "avg_return",
        "median_return",
        "p05_return",
        "p95_return",
        "best_return",
        "worst_return",
    ]:
        if col in out.columns:
            out[col] = out[col].astype(float)
    return out


def save_metadata(path: Path, args: argparse.Namespace, instruments: list[Instrument], failures: list[dict]) -> None:
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "instrument_count": len(instruments),
        "instruments": [instrument.__dict__ for instrument in instruments],
        "failures": failures,
        "notes": [
            f"A-share history source: {args.a_history_source}. Eastmoney uses AkShare qfq; Yahoo uses adjusted OHLC via adjclose factor.",
            "US prices use Yahoo Finance chart API and adjusted OHLC via adjclose factor.",
            "Signals use previous-day rolling MA to avoid look-ahead.",
            "Current index constituents introduce survivorship bias.",
        ],
    }
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest MA144/MA249 touch-buy event returns.")
    parser.add_argument("--end", default=date.today().isoformat(), help="Backtest end date, YYYY-MM-DD.")
    parser.add_argument("--start", default=None, help="Backtest start date, YYYY-MM-DD. Default: end - 5 years.")
    parser.add_argument("--a-top-n", type=int, default=100, help="Top CSI300 weighted A-shares to include.")
    parser.add_argument("--us-top-n", type=int, default=len(SP100_FALLBACK), help="S&P 100 US symbols to include.")
    parser.add_argument("--no-a", action="store_true", help="Skip A-shares.")
    parser.add_argument("--no-us", action="store_true", help="Skip US stocks.")
    parser.add_argument(
        "--a-history-source",
        choices=["yahoo", "eastmoney"],
        default="yahoo",
        help="A-share history source. Yahoo is the default because Eastmoney can throttle historical requests.",
    )
    parser.add_argument("--refresh-us-pool", action="store_true", help="Fetch the latest S&P 100 list from Wikipedia.")
    parser.add_argument("--ma", type=int, nargs="+", default=[144, 249], help="MA windows in trading days.")
    parser.add_argument("--hold-days", type=int, nargs="+", default=[5, 21], help="Holding periods in trading days.")
    parser.add_argument("--down-lookback", type=int, default=5, help="Recent sessions used for weakness filter.")
    parser.add_argument("--min-down-days", type=int, default=3, help="Minimum down-close days in recent lookback.")
    parser.add_argument("--cooldown-days", type=int, default=10, help="Minimum sessions between same MA signals per stock.")
    parser.add_argument("--cache-dir", default="data_cache", help="CSV cache directory.")
    parser.add_argument("--out-dir", default="results", help="Output directory.")
    parser.add_argument("--ignore-cache", action="store_true", help="Refetch all histories.")
    parser.add_argument("--sleep", type=float, default=0.15, help="Seconds to sleep between history requests.")
    args = parser.parse_args()

    end = parse_date(args.end)
    start = parse_date(args.start) if args.start else default_start(end)
    fetch_start = fetch_start_for_warmup(start, max(args.ma))
    cache_dir = Path(args.cache_dir)
    out_dir = ensure_dir(Path(args.out_dir))

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
            events = find_touch_events(
                instrument=instrument,
                prices=prices,
                backtest_start=start,
                backtest_end=end,
                ma_windows=args.ma,
                hold_days=args.hold_days,
                down_lookback=args.down_lookback,
                min_down_days=args.min_down_days,
                cooldown_days=args.cooldown_days,
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
    summary_df = add_display_percentages(summarize(events_df))

    events_path = out_dir / "ma_touch_trades.csv"
    summary_path = out_dir / "ma_touch_summary.csv"
    meta_path = out_dir / "run_metadata.json"
    events_df.to_csv(events_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    save_metadata(meta_path, args, instruments, failures)

    if not summary_df.empty:
        display = summary_df.copy()
        percent_cols = [
            "win_rate",
            "avg_return",
            "median_return",
            "p05_return",
            "p95_return",
            "best_return",
            "worst_return",
        ]
        for col in percent_cols:
            display[col] = (display[col] * 100).map(lambda x: f"{x:.2f}%")
        print("\nSummary:")
        print(display.to_string(index=False))
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
