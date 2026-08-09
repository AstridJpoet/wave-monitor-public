#!/usr/bin/env python3
"""Scan representative A-share/US/gold symbols for objective Elliott-like setups."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from .ma_touch_backtest import (
        Instrument,
        SP100_FALLBACK,
        fetch_a_share_daily,
        fetch_a_share_yahoo_daily,
        fetch_yahoo_daily,
        load_a_share_pool,
        load_us_pool,
        normalize_ohlc,
        parse_date,
        read_cached_csv,
        request_get_with_retries,
    )
    from .ma_touch_confirm_backtest import load_pool_from_metadata
except ImportError:
    from ma_touch_backtest import (
        Instrument,
        SP100_FALLBACK,
        fetch_a_share_daily,
        fetch_a_share_yahoo_daily,
        fetch_yahoo_daily,
        load_a_share_pool,
        load_us_pool,
        normalize_ohlc,
        parse_date,
        read_cached_csv,
        request_get_with_retries,
    )
    from ma_touch_confirm_backtest import load_pool_from_metadata


SCRIPT_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_ROOT.parent
DEFAULT_DATA_ROOT = PACKAGE_ROOT / "data"


@dataclass(frozen=True)
class Pivot:
    idx: int
    date: pd.Timestamp
    price: float
    kind: str


MIN_GAIN = {"CN": 0.55, "US": 0.25, "GOLD": 0.12}
MIN_GAIN_MEDIUM = {"CN": 0.25, "US": 0.15, "GOLD": 0.08}
WAVE2_RETRACE = (0.45, 0.786)
WAVE4_RETRACE = (0.236, 0.50)
EASTMONEY_HISTORY_ENABLED = True
EASTMONEY_QUOTE_ENABLED = True
INDEX_DEFINITIONS = [
    {"market": "CN", "symbol": "000001.SS", "name": "上证指数"},
    {"market": "CN", "symbol": "000300.SS", "name": "沪深300"},
    {"market": "US", "symbol": "^GSPC", "name": "标普500"},
    {"market": "US", "symbol": "^IXIC", "name": "纳斯达克综合"},
]


def zigzag(frame: pd.DataFrame, pct: float, col: str = "close") -> list[Pivot]:
    prices = frame[col].to_numpy(float)
    dates = pd.to_datetime(frame["date"]).to_numpy()
    if len(prices) == 0:
        return []

    pivots: list[Pivot] = []
    last_i = 0
    last_p = prices[0]
    trend = 0
    extreme_i = 0
    extreme_p = prices[0]
    for i, price in enumerate(prices[1:], start=1):
        if not np.isfinite(price):
            continue
        if trend == 0:
            change = price / last_p - 1
            if change >= pct:
                trend = 1
                extreme_i = i
                extreme_p = price
                pivots.append(Pivot(last_i, pd.Timestamp(dates[last_i]), last_p, "L"))
            elif change <= -pct:
                trend = -1
                extreme_i = i
                extreme_p = price
                pivots.append(Pivot(last_i, pd.Timestamp(dates[last_i]), last_p, "H"))
        elif trend == 1:
            if price > extreme_p:
                extreme_i = i
                extreme_p = price
            elif price / extreme_p - 1 <= -pct:
                pivots.append(Pivot(extreme_i, pd.Timestamp(dates[extreme_i]), extreme_p, "H"))
                trend = -1
                extreme_i = i
                extreme_p = price
        else:
            if price < extreme_p:
                extreme_i = i
                extreme_p = price
            elif price / extreme_p - 1 >= pct:
                pivots.append(Pivot(extreme_i, pd.Timestamp(dates[extreme_i]), extreme_p, "L"))
                trend = 1
                extreme_i = i
                extreme_p = price
    if not pivots or pivots[-1].idx != extreme_i:
        pivots.append(Pivot(extreme_i, pd.Timestamp(dates[extreme_i]), extreme_p, "H" if trend == 1 else "L"))
    return pivots


def resample_weekly(prices: pd.DataFrame) -> pd.DataFrame:
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    weekly = (
        frame.set_index("date")
        .resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return weekly


def fib_score(value: float, target: float, width: float) -> float:
    return max(0.0, 1.0 - abs(value - target) / width)


def min_gain_for(market: str, wave_level: str) -> float:
    table = MIN_GAIN if wave_level == "大级别" else MIN_GAIN_MEDIUM
    return table.get(market, 0.20)


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def rolling_mas(prices: pd.DataFrame) -> pd.Series:
    close = prices["close"].astype(float)
    return pd.Series(
        {
            "ma20": close.rolling(20).mean().iloc[-1],
            "ma50": close.rolling(50).mean().iloc[-1],
            "ma144": close.rolling(144).mean().iloc[-1],
            "ma249": close.rolling(249).mean().iloc[-1],
        }
    )


def trend_points(last_close: float, mas: pd.Series) -> float:
    score = 0.0
    if pd.notna(mas["ma249"]) and last_close > mas["ma249"]:
        score += 8.0
    if pd.notna(mas["ma144"]) and last_close > mas["ma144"]:
        score += 8.0
    if pd.notna(mas["ma50"]) and last_close > mas["ma50"]:
        score += 4.0
    return score


def latest_high_pullback(
    instrument: Instrument,
    prices: pd.DataFrame,
    structure: pd.DataFrame,
    pivots: list[Pivot],
    wave_level: str,
) -> dict | None:
    max_age = 90 if wave_level == "大级别" else 180
    highs = [p for p in pivots if p.kind == "H" and (structure.index[-1] - p.idx) <= max_age]
    if not highs:
        return None
    high = max(highs[-6:], key=lambda p: p.price)
    lows_before = [p for p in pivots if p.kind == "L" and p.idx < high.idx]
    if not lows_before:
        return None
    low = lows_before[-1]
    last_close = float(prices["close"].iloc[-1])
    gain = high.price / low.price - 1
    if gain < min_gain_for(instrument.market, wave_level) or last_close >= high.price:
        return None
    retrace = (high.price - last_close) / (high.price - low.price)
    if not (WAVE2_RETRACE[0] <= retrace <= WAVE2_RETRACE[1]):
        return None

    mas = rolling_mas(prices)
    bounce = last_close / prices["low"].tail(10).min() - 1
    score = 45 * fib_score(retrace, 0.618, 0.22) + trend_points(last_close, mas) + min(20, max(0, bounce * 140))
    target_1 = high.price
    target_2 = last_close + (high.price - low.price)
    post_high = [pivot for pivot in pivots if pivot.idx > high.idx]
    is_abc = (
        len(post_high) >= 3
        and post_high[0].kind == "L"
        and post_high[1].kind == "H"
        and post_high[2].kind == "L"
    )
    return {
        "market": instrument.market,
        "symbol": instrument.symbol,
        "name": instrument.name,
        "pattern": "ABC/C浪末端候选" if is_abc else "2浪回撤候选",
        "wave_level": wave_level,
        "structure_fit": round(fib_score(retrace, 0.618, 0.22), 6),
        "score": round(score, 1),
        "last_date": pd.Timestamp(prices["date"].iloc[-1]).date().isoformat(),
        "last_close": round(last_close, 4),
        "pivot_low_date": low.date.date().isoformat(),
        "pivot_low": round(low.price, 4),
        "pivot_high_date": high.date.date().isoformat(),
        "pivot_high": round(high.price, 4),
        "retracement": retrace,
        "support": round(high.price - (high.price - low.price) * 0.618, 4),
        "invalid_below": round(min(low.price, prices["low"].tail(20).min()), 4),
        "target_1": round(target_1, 4),
        "target_2": round(target_2, 4),
        "ma50": round(float(mas["ma50"]), 4) if pd.notna(mas["ma50"]) else np.nan,
        "ma144": round(float(mas["ma144"]), 4) if pd.notna(mas["ma144"]) else np.nan,
        "ma249": round(float(mas["ma249"]), 4) if pd.notna(mas["ma249"]) else np.nan,
    }


def wave4_candidate(
    instrument: Instrument,
    prices: pd.DataFrame,
    pivots: list[Pivot],
    wave_level: str,
) -> dict | None:
    if len(pivots) < 4:
        return None
    last_close = float(prices["close"].iloc[-1])
    candidates = []
    for i in range(len(pivots) - 3):
        a, b, c, d = pivots[i : i + 4]
        if not (a.kind == "L" and b.kind == "H" and c.kind == "L" and d.kind == "H"):
            continue
        max_age = 52 if wave_level == "大级别" else 120
        if pivots[-1].idx - d.idx > max_age:
            continue
        if not (d.price > b.price and c.price > a.price):
            continue
        gain3 = d.price / c.price - 1
        if gain3 < min_gain_for(instrument.market, wave_level):
            continue
        retrace = (d.price - last_close) / (d.price - c.price)
        if WAVE4_RETRACE[0] <= retrace <= WAVE4_RETRACE[1] and last_close > b.price:
            candidates.append((a, b, c, d, retrace))
    if not candidates:
        return None
    a, b, c, d, retrace = candidates[-1]
    mas = rolling_mas(prices)
    score = 50 * fib_score(retrace, 0.382, 0.16) + trend_points(last_close, mas) + 12
    target_1 = d.price
    target_2 = d.price + 0.618 * (d.price - c.price)
    return {
        "market": instrument.market,
        "symbol": instrument.symbol,
        "name": instrument.name,
        "pattern": "4浪回踩候选",
        "wave_level": wave_level,
        "structure_fit": round(fib_score(retrace, 0.382, 0.16), 6),
        "score": round(score, 1),
        "last_date": pd.Timestamp(prices["date"].iloc[-1]).date().isoformat(),
        "last_close": round(last_close, 4),
        "pivot_low_date": c.date.date().isoformat(),
        "pivot_low": round(c.price, 4),
        "pivot_high_date": d.date.date().isoformat(),
        "pivot_high": round(d.price, 4),
        "retracement": retrace,
        "support": round(d.price - (d.price - c.price) * 0.382, 4),
        "invalid_below": round(b.price, 4),
        "target_1": round(target_1, 4),
        "target_2": round(target_2, 4),
        "ma50": round(float(mas["ma50"]), 4) if pd.notna(mas["ma50"]) else np.nan,
        "ma144": round(float(mas["ma144"]), 4) if pd.notna(mas["ma144"]) else np.nan,
        "ma249": round(float(mas["ma249"]), 4) if pd.notna(mas["ma249"]) else np.nan,
    }


def breakout_candidate(
    instrument: Instrument,
    prices: pd.DataFrame,
    pivots: list[Pivot],
    wave_level: str,
) -> dict | None:
    if len(pivots) < 3:
        return None
    last_close = float(prices["close"].iloc[-1])
    matches = []
    for i in range(len(pivots) - 2):
        a, b, c = pivots[i : i + 3]
        if not (a.kind == "L" and b.kind == "H" and c.kind == "L"):
            continue
        max_age = 52 if wave_level == "大级别" else 120
        if pivots[-1].idx - c.idx > max_age:
            continue
        gain = b.price / a.price - 1
        retrace = (b.price - c.price) / (b.price - a.price)
        if gain < min_gain_for(instrument.market, wave_level) or not (0.382 <= retrace <= 0.786):
            continue
        if last_close <= b.price:
            continue
        extension_target = c.price + 1.618 * (b.price - a.price)
        extension_progress = (last_close - b.price) / max(1e-9, extension_target - b.price)
        if extension_progress > 0.75:
            continue
        matches.append((a, b, c, retrace, extension_target, extension_progress))
    if not matches:
        return None
    a, b, c, retrace, extension_target, extension_progress = matches[-1]
    mas = rolling_mas(prices)
    score = 42 * (1 - min(1, extension_progress)) + 25 * fib_score(retrace, 0.618, 0.24) + trend_points(last_close, mas)
    structure_fit = 0.55 * fib_score(retrace, 0.618, 0.24) + 0.45 * (1 - min(1, extension_progress))
    return {
        "market": instrument.market,
        "symbol": instrument.symbol,
        "name": instrument.name,
        "pattern": "疑似3浪突破",
        "wave_level": wave_level,
        "structure_fit": round(structure_fit, 6),
        "score": round(score, 1),
        "last_date": pd.Timestamp(prices["date"].iloc[-1]).date().isoformat(),
        "last_close": round(last_close, 4),
        "pivot_low_date": c.date.date().isoformat(),
        "pivot_low": round(c.price, 4),
        "pivot_high_date": b.date.date().isoformat(),
        "pivot_high": round(b.price, 4),
        "retracement": retrace,
        "support": round(b.price, 4),
        "invalid_below": round(c.price, 4),
        "target_1": round(extension_target, 4),
        "target_2": round(c.price + 2.0 * (b.price - a.price), 4),
        "ma50": round(float(mas["ma50"]), 4) if pd.notna(mas["ma50"]) else np.nan,
        "ma144": round(float(mas["ma144"]), 4) if pd.notna(mas["ma144"]) else np.nan,
        "ma249": round(float(mas["ma249"]), 4) if pd.notna(mas["ma249"]) else np.nan,
    }


def threshold_for_market(market: str, wave_level: str = "大级别") -> float:
    if wave_level == "中级别":
        if market == "CN":
            return 0.12
        if market == "GOLD":
            return 0.05
        return 0.08
    if market == "CN":
        return 0.25
    if market == "GOLD":
        return 0.08
    return 0.14


def volume_ratio_at(prices: pd.DataFrame, index: int) -> float | None:
    volumes = pd.to_numeric(prices["volume"], errors="coerce")
    if index < 0:
        index += len(volumes)
    if index <= 0 or index >= len(volumes):
        return None
    current = volumes.iloc[index]
    baseline = volumes.iloc[max(0, index - 20) : index].replace(0, np.nan).mean()
    if not np.isfinite(current) or not np.isfinite(baseline) or baseline <= 0:
        return None
    return float(current / baseline)


def recent_breakout_index(prices: pd.DataFrame, support: float, lookback: int = 5) -> int | None:
    closes = pd.to_numeric(prices["close"], errors="coerce").to_numpy(float)
    for index in range(len(closes) - 1, max(0, len(closes) - lookback - 1), -1):
        if np.isfinite(closes[index]) and np.isfinite(closes[index - 1]):
            if closes[index] > support and closes[index - 1] <= support:
                return index
    return None


def trend_score_v2(prices: pd.DataFrame) -> float:
    closes = pd.to_numeric(prices["close"], errors="coerce")
    ma144 = closes.rolling(144).mean()
    ma249 = closes.rolling(249).mean()
    score = 0.0
    if pd.notna(ma249.iloc[-1]) and closes.iloc[-1] > ma249.iloc[-1]:
        score += 2.5
    if pd.notna(ma144.iloc[-1]) and pd.notna(ma249.iloc[-1]) and ma144.iloc[-1] > ma249.iloc[-1]:
        score += 2.5
    if len(ma144) >= 21 and pd.notna(ma144.iloc[-21]) and ma144.iloc[-1] > ma144.iloc[-21]:
        score += 2.5
    if len(ma249) >= 21 and pd.notna(ma249.iloc[-21]) and ma249.iloc[-1] > ma249.iloc[-21]:
        score += 2.5
    return score


def market_index_snapshot(definition: dict, prices: pd.DataFrame) -> dict:
    if len(prices) < 145:
        raise ValueError("index history requires at least 145 sessions")
    frame = prices.reset_index(drop=True)
    closes = pd.to_numeric(frame["close"], errors="coerce")
    latest = float(closes.iloc[-1])
    ma20 = float(closes.rolling(20).mean().iloc[-1])
    ma50 = float(closes.rolling(50).mean().iloc[-1])
    ma144 = float(closes.rolling(144).mean().iloc[-1])
    change_1d = latest / float(closes.iloc[-2]) - 1
    change_5d = latest / float(closes.iloc[-6]) - 1
    change_20d = latest / float(closes.iloc[-21]) - 1

    score = 0.0
    score += 25.0 if latest > ma20 else 0.0
    score += 20.0 if ma20 > ma50 else 0.0
    score += 20.0 if latest > ma50 else 0.0
    score += 15.0 if latest > ma144 else 0.0
    score += 10.0 if change_5d > 0 else 0.0
    score += 10.0 if change_20d > 0 else 0.0
    if score >= 80:
        status = "强势"
    elif score >= 60:
        status = "偏强"
    elif score >= 40:
        status = "震荡"
    else:
        status = "偏弱"
    return {
        "market": str(definition["market"]),
        "symbol": str(definition["symbol"]),
        "name": str(definition["name"]),
        "last_date": pd.Timestamp(frame["date"].iloc[-1]).date().isoformat(),
        "last_close": round(latest, 4),
        "change_1d": round(change_1d, 6),
        "change_5d": round(change_5d, 6),
        "change_20d": round(change_20d, 6),
        "ma20": round(ma20, 4),
        "ma50": round(ma50, 4),
        "ma144": round(ma144, 4),
        "score": round(score, 1),
        "status": status,
    }


def scan_market_indices(
    start: date,
    end: date,
    cache_dir: Path,
) -> tuple[list[dict], list[dict]]:
    snapshots: list[dict] = []
    failures: list[dict] = []
    for definition in INDEX_DEFINITIONS:
        try:
            prices = fetch_yahoo_daily(
                str(definition["symbol"]),
                start,
                end,
                cache_dir,
                True,
                cache_market="INDEX",
            )
            snapshots.append(market_index_snapshot(definition, prices))
        except Exception as exc:
            failures.append(
                {
                    "market": definition["market"],
                    "symbol": definition["symbol"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return snapshots, failures


def market_context_scores(index_snapshots: list[dict]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for snapshot in index_snapshots:
        grouped.setdefault(str(snapshot["market"]), []).append(float(snapshot["score"]))
    return {market: round(float(np.mean(scores)), 1) for market, scores in grouped.items() if scores}


def market_context_label(score: float | None) -> str:
    if score is None:
        return "未取得大盘数据"
    if score >= 80:
        return "大盘强势"
    if score >= 60:
        return "大盘偏强"
    if score >= 40:
        return "大盘震荡"
    return "大盘偏弱"


def market_score_adjustment(score: float | None) -> float:
    if score is None or score >= 60:
        return 0.0
    if score >= 40:
        return -3.0
    return -8.0


def score_candidate_v2(
    row: dict,
    prices: pd.DataFrame,
    multi_level_alignment: bool,
    market_context_score: float | None = None,
) -> dict | None:
    close = float(row["last_close"])
    support = float(row["support"])
    invalid = float(row["invalid_below"])
    target = float(row["target_1"])
    if support <= 0 or close <= invalid or target <= close:
        return None

    pattern = str(row["pattern"])
    is_breakout = pattern == "疑似3浪突破"
    distance = close / support - 1
    if is_breakout:
        if not (-0.01 <= distance <= 0.10):
            return None
        position_distance = max(0.0, distance)
    else:
        if not (-0.05 <= distance <= 0.12):
            return None
        position_distance = abs(distance)

    if position_distance <= 0.02:
        position_score = 25.0
    elif position_distance <= 0.04:
        position_score = 22.0
    elif position_distance <= 0.08:
        position_score = 16.0
    else:
        position_score = 8.0

    risk = close - invalid
    reward = target - close
    if risk <= 0:
        return None
    risk_reward = reward / risk
    if risk_reward < 1.5:
        return None
    if risk_reward >= 3.0:
        risk_score = 10.0
    elif risk_reward >= 2.0:
        risk_score = 8.0
    else:
        risk_score = 6.0

    structure_fit = float(row.get("structure_fit") or 0.0)
    structure_score = min(35.0, 22.0 + 8.0 * structure_fit + (5.0 if multi_level_alignment else 0.0))
    trend_score = trend_score_v2(prices)

    opens = pd.to_numeric(prices["open"], errors="coerce")
    highs = pd.to_numeric(prices["high"], errors="coerce")
    lows = pd.to_numeric(prices["low"], errors="coerce")
    closes = pd.to_numeric(prices["close"], errors="coerce")
    latest_volume_ratio = volume_ratio_at(prices, -1)
    confirmation_score = 0.0
    confirmation: list[str] = []
    trigger = False

    if is_breakout:
        cross_index = recent_breakout_index(prices, support)
        breakout_volume_ratio = volume_ratio_at(prices, cross_index) if cross_index is not None else None
        if cross_index is not None:
            confirmation_score += 10.0
            confirmation.append("近5日突破")
        if breakout_volume_ratio is not None and breakout_volume_ratio >= 1.2:
            confirmation_score += 6.0
            confirmation.append("突破放量")
        if cross_index is not None and closes.iloc[cross_index:].min() >= support:
            confirmation_score += 4.0
            confirmation.append("突破后守位")
        trigger = bool(
            cross_index is not None
            and breakout_volume_ratio is not None
            and breakout_volume_ratio >= 1.2
            and 0 <= distance <= 0.08
        )
        volume_ratio = breakout_volume_ratio
    else:
        bullish_reclaim = bool(
            lows.iloc[-1] <= support * 1.03
            and closes.iloc[-1] >= support
            and closes.iloc[-1] > opens.iloc[-1]
        )
        higher_low = bool(lows.iloc[-1] > lows.iloc[-2] and closes.iloc[-1] > closes.iloc[-2])
        breaks_recent_high = bool(closes.iloc[-1] > highs.iloc[-4:-1].max())
        if bullish_reclaim:
            confirmation_score += 8.0
            confirmation.append("支撑收复")
        if higher_low:
            confirmation_score += 5.0
            confirmation.append("更高低点")
        if breaks_recent_high:
            confirmation_score += 5.0
            confirmation.append("突破3日高点")
        if latest_volume_ratio is not None and latest_volume_ratio >= 1.2:
            confirmation_score += 2.0
            confirmation.append("成交放量")
        trigger = bool(
            (bullish_reclaim or (higher_low and breaks_recent_high))
            and -0.02 <= distance <= 0.08
        )
        volume_ratio = latest_volume_ratio

    market_adjustment = market_score_adjustment(market_context_score)
    total_score = round(
        structure_score + position_score + confirmation_score + trend_score + risk_score + market_adjustment,
        1,
    )
    if total_score < 65:
        return None
    left_probe = bool(
        not is_breakout
        and not trigger
        and -0.02 <= distance <= 0.025
        and structure_score >= 28
        and trend_score >= 5
        and risk_reward >= 2.0
        and total_score >= 75
    )
    if trigger and total_score >= 75:
        signal_stage = "trigger"
        stage_label = "右侧触发"
    elif left_probe:
        signal_stage = "probe"
        stage_label = "左侧试错"
    else:
        signal_stage = "watch"
        stage_label = "观察候选"
    row.update(
        {
            "score": total_score,
            "signal_stage": signal_stage,
            "stage_label": stage_label,
            "structure_score": round(structure_score, 1),
            "position_score": round(position_score, 1),
            "confirmation_score": round(confirmation_score, 1),
            "trend_score": round(trend_score, 1),
            "risk_score": round(risk_score, 1),
            "risk_reward": round(risk_reward, 3),
            "volume_ratio": round(volume_ratio, 3) if volume_ratio is not None else np.nan,
            "confirmation_detail": "、".join(confirmation) if confirmation else "等待右侧确认",
            "multi_level_alignment": multi_level_alignment,
            "market_context_score": round(market_context_score, 1) if market_context_score is not None else np.nan,
            "market_context_label": market_context_label(market_context_score),
            "market_adjustment": market_adjustment,
        }
    )
    return row


def scan_instrument(
    instrument: Instrument,
    prices: pd.DataFrame,
    market_context_score: float | None = None,
) -> list[dict]:
    if len(prices) < 260:
        return []
    prices = prices.reset_index(drop=True)
    structures = [
        ("大级别", resample_weekly(prices).reset_index(drop=True)),
        ("中级别", prices),
    ]
    setups: list[dict] = []
    for wave_level, structure in structures:
        threshold = threshold_for_market(instrument.market, wave_level)
        pivots = zigzag(structure, threshold)
        if len(pivots) < 6:
            pivots = zigzag(structure, threshold * 0.75)
        for fn in [latest_high_pullback, wave4_candidate, breakout_candidate]:
            if fn is latest_high_pullback:
                row = fn(instrument, prices, structure, pivots, wave_level)
            else:
                row = fn(instrument, prices, pivots, wave_level)
            if row:
                row["zigzag_threshold"] = threshold
                setups.append(row)

    pattern_levels: dict[str, set[str]] = {}
    for row in setups:
        pattern_levels.setdefault(str(row["pattern"]), set()).add(str(row["wave_level"]))

    rows = []
    for row in setups:
        aligned = len(pattern_levels.get(str(row["pattern"]), set())) > 1
        scored = score_candidate_v2(row, prices, aligned, market_context_score)
        if scored:
            rows.append(scored)
    stage_priority = {"trigger": 2, "probe": 1, "watch": 0}
    return sorted(rows, key=lambda item: (stage_priority.get(item["signal_stage"], 0), item["score"]), reverse=True)


def yyyymmdd_to_date(value: str) -> date | None:
    if len(value) != 8 or not value.isdigit():
        return None
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def cache_market_for_instrument(instrument: Instrument) -> str:
    if instrument.market == "CN":
        return "CN"
    if instrument.market == "GOLD":
        return "GOLD"
    return "US"


def cache_symbol_for_instrument(instrument: Instrument) -> str:
    return instrument.symbol.replace("/", "_")


def parse_cache_span(path: Path) -> tuple[date, date] | None:
    parts = path.stem.rsplit("_", 2)
    if len(parts) != 3:
        return None
    start = yyyymmdd_to_date(parts[1])
    end = yyyymmdd_to_date(parts[2])
    if start is None or end is None:
        return None
    return start, end


def recent_cached_prices(
    instrument: Instrument,
    start: date,
    end: date,
    cache_dir: Path,
    recent_cache_days: int,
) -> pd.DataFrame | None:
    if recent_cache_days <= 0:
        return None
    market_dir = cache_dir / cache_market_for_instrument(instrument)
    if not market_dir.exists():
        return None

    cache_symbol = cache_symbol_for_instrument(instrument)
    candidates = []
    for path in market_dir.glob(f"{cache_symbol}_*.csv"):
        span = parse_cache_span(path)
        if span is None:
            continue
        cache_start, cache_end = span
        if cache_end < end - timedelta(days=recent_cache_days):
            continue
        if cache_start > start + timedelta(days=10):
            continue
        candidates.append((cache_end, cache_start, path))
    if not candidates:
        return None

    for _, _, path in sorted(candidates, reverse=True):
        cached = read_cached_csv(path)
        if cached is None:
            continue
        frame = normalize_ohlc(cached)
        frame = frame[(frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)].reset_index(drop=True)
        if len(frame) >= 260:
            return frame
    return None


def fetch_eastmoney_a_share_quote(symbol: str, end: date) -> dict | None:
    market_id = "1" if symbol.startswith(("5", "6", "9")) else "0"
    response = request_get_with_retries(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        attempts=1,
        timeout=8,
        params={
            "secid": f"{market_id}.{symbol}",
            "klt": "101",
            "fqt": "0",
            "lmt": "1",
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    rows = ((response.json() or {}).get("data") or {}).get("klines") or []
    if not rows:
        return None
    fields = str(rows[-1]).split(",")
    if len(fields) < 6:
        return None
    quote_date = date.fromisoformat(fields[0])
    close = float(fields[2])
    if quote_date > end or close <= 0:
        return None
    return {
        "date": quote_date,
        "open": float(fields[1]),
        "high": float(fields[3]),
        "low": float(fields[4]),
        "close": close,
        "volume": float(fields[5]),
    }


def fetch_sina_a_share_quote(symbol: str, end: date) -> dict | None:
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    response = request_get_with_retries(
        f"https://hq.sinajs.cn/list={prefix}{symbol}",
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
    )
    payload = response.content.decode("gbk", errors="ignore")
    if '="' not in payload:
        return None
    fields = payload.split('="', 1)[1].rsplit('"', 1)[0].split(",")
    if len(fields) < 32 or not fields[0] or not fields[30]:
        return None
    quote_date = date.fromisoformat(fields[30])
    if quote_date > end:
        return None
    close = float(fields[3])
    if close <= 0:
        return None
    return {
        "date": quote_date,
        "open": float(fields[1]),
        "high": float(fields[4]),
        "low": float(fields[5]),
        "close": close,
        "volume": float(fields[8]),
    }


def fetch_latest_a_share_quote(symbol: str, end: date) -> dict | None:
    global EASTMONEY_QUOTE_ENABLED
    if EASTMONEY_QUOTE_ENABLED:
        try:
            quote = fetch_eastmoney_a_share_quote(symbol, end)
        except Exception:
            EASTMONEY_QUOTE_ENABLED = False
        else:
            if quote is not None:
                return quote
            EASTMONEY_QUOTE_ENABLED = False
    try:
        quote = fetch_sina_a_share_quote(symbol, end)
    except Exception:
        return None
    if quote is not None:
        return quote
    return None


def fetch_primary_a_share_history(
    symbol: str,
    start: date,
    end: date,
    cache_dir: Path,
    use_cache: bool,
) -> pd.DataFrame:
    global EASTMONEY_HISTORY_ENABLED
    if EASTMONEY_HISTORY_ENABLED:
        try:
            return fetch_a_share_daily(symbol, start, end, cache_dir, use_cache)
        except Exception:
            EASTMONEY_HISTORY_ENABLED = False
    return fetch_a_share_yahoo_daily(symbol, start, end, cache_dir, use_cache)


def merge_latest_a_share_quote(prices: pd.DataFrame, symbol: str, end: date) -> pd.DataFrame:
    quote = fetch_latest_a_share_quote(symbol, end)
    if quote is None:
        return prices
    return normalize_ohlc(pd.concat([prices, pd.DataFrame([quote])], ignore_index=True))


def fetch_prices(
    instrument: Instrument,
    start: date,
    end: date,
    cache_dir: Path,
    recent_cache_days: int,
) -> pd.DataFrame:
    cached = recent_cached_prices(instrument, start, end, cache_dir, recent_cache_days)
    if cached is not None:
        prices = cached
    elif instrument.market == "CN":
        prices = fetch_primary_a_share_history(instrument.symbol, start, end, cache_dir, True)
    elif instrument.market == "GOLD":
        prices = fetch_yahoo_daily(instrument.symbol, start, end, cache_dir, True, cache_market="GOLD")
    else:
        prices = fetch_yahoo_daily(instrument.symbol, start, end, cache_dir, True)
    if instrument.market == "CN":
        return merge_latest_a_share_quote(prices, instrument.symbol, end)
    return prices


def build_pool(args: argparse.Namespace) -> list[Instrument]:
    instruments: list[Instrument] = []
    if args.pool_metadata and Path(args.pool_metadata).exists():
        instruments.extend(load_pool_from_metadata(Path(args.pool_metadata), args.a_top_n, args.us_top_n))
    else:
        if args.a_top_n:
            instruments.extend(load_a_share_pool(args.a_top_n))
        if args.us_top_n:
            instruments.extend(load_us_pool(args.us_top_n, args.refresh_us_pool))
    instruments.extend(
        [
            Instrument("GOLD", "GC=F", "COMEX黄金期货", "Yahoo Finance"),
            Instrument("GOLD", "GLD", "SPDR Gold Shares", "Yahoo Finance"),
        ]
    )
    return instruments


def display(df: pd.DataFrame, limit_per_market: int) -> pd.DataFrame:
    rows = []
    for market, group in df.sort_values("score", ascending=False).groupby("market", sort=False):
        rows.append(group.head(limit_per_market))
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if out.empty:
        return out
    out = out[
        [
            "market",
            "symbol",
            "name",
            "pattern",
            "score",
            "last_close",
            "retracement",
            "support",
            "invalid_below",
            "target_1",
            "target_2",
            "pivot_low_date",
            "pivot_high_date",
        ]
    ].copy()
    out["retracement"] = out["retracement"].map(lambda value: pct(value))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan wave-like setups.")
    parser.add_argument("--start", default="2021-08-02")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--a-top-n", type=int, default=300)
    parser.add_argument("--us-top-n", type=int, default=len(SP100_FALLBACK))
    parser.add_argument("--pool-metadata", default=None)
    parser.add_argument("--refresh-us-pool", action="store_true")
    parser.add_argument("--cache-dir", default=str(DEFAULT_DATA_ROOT / "scan_cache"))
    parser.add_argument("--out-dir", default=str(DEFAULT_DATA_ROOT / "results_wave_scan"))
    parser.add_argument("--limit-per-market", type=int, default=15)
    parser.add_argument("--recent-cache-days", type=int, default=0, help="Reuse cached histories ending within this many days.")
    parser.add_argument("--protect-existing", action="store_true", help="Do not overwrite existing candidates when failure rate is high.")
    parser.add_argument("--max-failure-rate", type=float, default=0.35)
    args = parser.parse_args()

    start = parse_date(args.start)
    end = parse_date(args.end)
    cache_dir = Path(args.cache_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    instruments = build_pool(args)
    scan_started_at = pd.Timestamp.now()
    index_snapshots, index_failures = scan_market_indices(start, end, cache_dir)
    context_scores = market_context_scores(index_snapshots)

    results = []
    failures = []
    print(f"Scanning {len(instruments)} instruments from {start} to {end}")
    print(f"Market indices: {len(index_snapshots)} ready, {len(index_failures)} failed")
    for snapshot in index_snapshots:
        print(
            f"  {snapshot['symbol']} {snapshot['status']} "
            f"score={snapshot['score']:.0f} close={snapshot['last_close']}"
        )
    print("A-share sources: Eastmoney history/realtime -> Sina realtime -> Yahoo history fallback")
    for idx, instrument in enumerate(instruments, start=1):
        try:
            prices = fetch_prices(instrument, start, end, cache_dir, args.recent_cache_days)
            rows = scan_instrument(instrument, prices, context_scores.get(instrument.market))
            results.extend(rows)
            print(f"[{idx:03d}/{len(instruments)}] {instrument.market} {instrument.symbol}: {len(rows)} setups")
        except Exception as exc:
            failures.append({**asdict(instrument), "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{idx:03d}/{len(instruments)}] {instrument.market} {instrument.symbol}: FAILED {exc}")

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values(["market", "score"], ascending=[True, False])
    failure_rate = len(failures) / len(instruments) if instruments else 0
    candidate_path = out_dir / "wave_scan_candidates.csv"
    failure_path = out_dir / "wave_scan_failures.csv"
    protected_existing = bool(args.protect_existing and candidate_path.exists() and failure_rate > args.max_failure_rate)
    if protected_existing:
        stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
        df.to_csv(out_dir / f"wave_scan_candidates_rejected_{stamp}.csv", index=False)
        pd.DataFrame(failures).to_csv(out_dir / f"wave_scan_failures_rejected_{stamp}.csv", index=False)
        print(f"\nSkipped overwriting existing candidates: failure rate {failure_rate:.1%}")
    else:
        df.to_csv(candidate_path, index=False)
        pd.DataFrame(failures).to_csv(failure_path, index=False)
    scan_finished_at = pd.Timestamp.now()
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "duration_seconds": round((scan_finished_at - scan_started_at).total_seconds(), 1),
        "a_share_source_priority": "Eastmoney -> Sina -> Yahoo",
        "a_share_fallback_active": not (EASTMONEY_HISTORY_ENABLED and EASTMONEY_QUOTE_ENABLED),
        "args": vars(args),
        "instrument_count": len(instruments),
        "candidate_count": len(df),
        "failure_count": len(failures),
        "failure_rate": failure_rate,
        "index_count": len(index_snapshots),
        "index_failure_count": len(index_failures),
        "index_snapshots": index_snapshots,
        "index_failures": index_failures,
        "market_context_scores": context_scores,
        "protected_existing": protected_existing,
        "failures": failures,
    }
    metadata_path = out_dir / ("wave_scan_metadata_rejected.json" if protected_existing else "wave_scan_metadata.json")
    metadata_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nTop candidates:")
    if df.empty:
        print("No candidates.")
    else:
        print(display(df, args.limit_per_market).to_string(index=False))
    print(f"\nSaved: {out_dir / 'wave_scan_candidates.csv'}")
    print(f"Failures: {len(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
