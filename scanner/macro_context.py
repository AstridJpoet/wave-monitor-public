#!/usr/bin/env python3
"""Build market-specific macro context from public daily series."""

from __future__ import annotations

import io
import math
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import requests


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SERIES = {
    "DGS10": {"name": "美国10年期国债", "name_en": "US 10Y yield", "unit": "%"},
    "DGS2": {"name": "美国2年期国债", "name_en": "US 2Y yield", "unit": "%"},
    "DFII10": {"name": "美国10年实际利率", "name_en": "US 10Y real yield", "unit": "%"},
    "DTWEXBGS": {"name": "广义美元指数", "name_en": "Broad USD index", "unit": "index"},
    "DEXCHUS": {"name": "美元兑人民币", "name_en": "USD/CNY", "unit": "CNY"},
}


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_fred_csv(text: str, series_id: str) -> pd.DataFrame:
    frame = pd.read_csv(io.StringIO(text))
    if frame.empty:
        raise ValueError(f"empty FRED series: {series_id}")
    date_column = next(
        (column for column in frame.columns if str(column).strip().lower() in {"date", "observation_date"}),
        frame.columns[0],
    )
    value_column = next(
        (column for column in frame.columns if str(column).strip().upper() == series_id.upper()),
        frame.columns[-1],
    )
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_column], errors="coerce"),
            "value": pd.to_numeric(frame[value_column], errors="coerce"),
        }
    )
    out = out.dropna().sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if out.empty:
        raise ValueError(f"no numeric observations in FRED series: {series_id}")
    return out


def read_cached_series(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError):
        return None
    if not {"date", "value"}.issubset(frame.columns):
        return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna().sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return frame if not frame.empty else None


def fetch_fred_series(series_id: str, cache_dir: Path, attempts: int = 2) -> tuple[pd.DataFrame, str]:
    macro_dir = cache_dir / "MACRO"
    cache_path = macro_dir / f"{series_id}.csv"
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                FRED_CSV_URL,
                params={"id": series_id},
                timeout=15,
                headers={"User-Agent": "WaveMonitor/1.0 public research dashboard"},
            )
            response.raise_for_status()
            frame = normalize_fred_csv(response.text, series_id)
            macro_dir.mkdir(parents=True, exist_ok=True)
            frame.to_csv(cache_path, index=False)
            return frame, "live"
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(0.5 * attempt)
    cached = read_cached_series(cache_path)
    if cached is not None:
        return cached, "cache"
    assert last_error is not None
    raise last_error


def point(frame: pd.DataFrame, end: date, lookback: int = 20, percent_change: bool = False) -> dict[str, Any]:
    available = frame[frame["date"].dt.date <= end].reset_index(drop=True)
    if available.empty:
        return {"value": None, "change_20d": None, "as_of": None}
    latest = available.iloc[-1]
    previous = available.iloc[max(0, len(available) - lookback - 1)]
    value = float(latest["value"])
    previous_value = float(previous["value"])
    if percent_change and previous_value != 0:
        change = value / previous_value - 1
    else:
        change = value - previous_value
    return {
        "value": round(value, 6),
        "change_20d": round(change, 8),
        "as_of": pd.Timestamp(latest["date"]).date().isoformat(),
    }


def component(
    series_id: str,
    observation: dict[str, Any],
    impact: float,
    status: str,
    status_en: str,
) -> dict[str, Any]:
    definition = SERIES[series_id]
    return {
        "key": series_id,
        "name": definition["name"],
        "name_en": definition["name_en"],
        "value": observation.get("value"),
        "unit": definition["unit"],
        "change_20d": observation.get("change_20d"),
        "impact": round(impact, 1),
        "status": status,
        "status_en": status_en,
        "as_of": observation.get("as_of"),
    }


def band(
    value: float | None,
    positive: tuple[float, float, str, str],
    negative: tuple[float, float, str, str],
    neutral: tuple[str, str],
) -> tuple[float, str, str]:
    if value is None:
        return 0.0, "数据缺失", "Unavailable"
    positive_cutoff, positive_impact, positive_zh, positive_en = positive
    negative_cutoff, negative_impact, negative_zh, negative_en = negative
    if value <= positive_cutoff:
        return positive_impact, positive_zh, positive_en
    if value >= negative_cutoff:
        return negative_impact, negative_zh, negative_en
    return 0.0, neutral[0], neutral[1]


def regime(score: float) -> tuple[str, str]:
    if score >= 65:
        return "宏观顺风", "Supportive"
    if score >= 45:
        return "宏观中性", "Neutral"
    if score >= 30:
        return "宏观逆风", "Headwind"
    return "宏观压力", "Stress"


def adjustment(score: float | None) -> float:
    if score is None:
        return 0.0
    if score >= 70:
        return 2.0
    if score >= 45:
        return 0.0
    if score >= 30:
        return -5.0
    return -12.0


def snapshot(
    market: str,
    components: list[dict[str, Any]],
    base_score: float = 50.0,
) -> dict[str, Any]:
    available = [item for item in components if item.get("value") is not None]
    score = round(min(100.0, max(0.0, base_score + sum(float(item["impact"]) for item in available))), 1)
    label, label_en = regime(score)
    if len(available) == len(components):
        data_status = "ready"
    elif available:
        data_status = "partial"
    else:
        data_status = "unavailable"
    dates = [str(item.get("as_of")) for item in available if item.get("as_of")]
    if len(available) < max(2, len(components) - 1):
        score_value: float | None = None
        label, label_en = "宏观数据缺失", "Macro data unavailable"
    else:
        score_value = score
    return {
        "market": market,
        "score": score_value,
        "regime": label,
        "regime_en": label_en,
        "adjustment": adjustment(score_value),
        "as_of": min(dates) if dates else None,
        "data_status": data_status,
        "components": components,
    }


def score_us(observations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    real = observations["DFII10"]
    ten = observations["DGS10"]
    two = observations["DGS2"]
    dollar = observations["DTWEXBGS"]
    components = []
    impact, zh, en = band(real.get("change_20d"), (-0.10, 8, "实际利率回落", "Real yield falling"), (0.15, -10, "实际利率上行", "Real yield rising"), ("实际利率平稳", "Real yield stable"))
    components.append(component("DFII10", real, impact, zh, en))
    impact, zh, en = band(ten.get("change_20d"), (-0.20, 5, "长端利率回落", "Long yield falling"), (0.35, -8, "长端利率快速上行", "Long yield rising fast"), ("长端利率平稳", "Long yield stable"))
    components.append(component("DGS10", ten, impact, zh, en))
    curve = None
    if finite(ten.get("value")) is not None and finite(two.get("value")) is not None:
        curve = float(ten["value"]) - float(two["value"])
    curve_observation = {"value": round(curve, 6) if curve is not None else None, "change_20d": None, "as_of": min(filter(None, [ten.get("as_of"), two.get("as_of")]), default=None)}
    curve_impact = 3.0 if curve is not None and curve >= 0 else -3.0 if curve is not None and curve < -0.25 else 0.0
    curve_status = "曲线正常" if curve is not None and curve >= 0 else "曲线倒挂" if curve is not None and curve < -0.25 else "曲线接近平坦"
    curve_status_en = "Positive curve" if curve is not None and curve >= 0 else "Inverted curve" if curve is not None and curve < -0.25 else "Flat curve"
    components.append({**component("DGS2", curve_observation, curve_impact, curve_status, curve_status_en), "key": "T10Y2Y", "name": "10年-2年期限利差", "name_en": "10Y-2Y curve"})
    impact, zh, en = band(dollar.get("change_20d"), (-0.01, 6, "美元走弱", "USD weakening"), (0.02, -8, "美元快速走强", "USD strengthening fast"), ("美元平稳", "USD stable"))
    components.append(component("DTWEXBGS", dollar, impact, zh, en))
    return snapshot("US", components)


def score_cn(observations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    yuan = observations["DEXCHUS"]
    dollar = observations["DTWEXBGS"]
    real = observations["DFII10"]
    ten = observations["DGS10"]
    components = []
    impact, zh, en = band(yuan.get("change_20d"), (-0.005, 12, "人民币走强", "CNY strengthening"), (0.01, -12, "人民币明显走弱", "CNY weakening"), ("人民币平稳", "CNY stable"))
    components.append(component("DEXCHUS", yuan, impact, zh, en))
    impact, zh, en = band(dollar.get("change_20d"), (-0.01, 8, "美元走弱", "USD weakening"), (0.02, -8, "美元快速走强", "USD strengthening fast"), ("美元平稳", "USD stable"))
    components.append(component("DTWEXBGS", dollar, impact, zh, en))
    impact, zh, en = band(real.get("change_20d"), (-0.10, 5, "全球实际利率回落", "Global real yield falling"), (0.20, -5, "全球实际利率上行", "Global real yield rising"), ("全球实际利率平稳", "Global real yield stable"))
    components.append(component("DFII10", real, impact, zh, en))
    impact, zh, en = band(ten.get("change_20d"), (-0.20, 3, "美债利率回落", "US yield falling"), (0.35, -3, "美债利率快速上行", "US yield rising fast"), ("美债利率平稳", "US yield stable"))
    components.append(component("DGS10", ten, impact, zh, en))
    return snapshot("CN", components)


def score_gold(observations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    real = observations["DFII10"]
    dollar = observations["DTWEXBGS"]
    ten = observations["DGS10"]
    components = []
    impact, zh, en = band(real.get("change_20d"), (-0.10, 15, "实际利率回落", "Real yield falling"), (0.10, -15, "实际利率上行", "Real yield rising"), ("实际利率平稳", "Real yield stable"))
    components.append(component("DFII10", real, impact, zh, en))
    impact, zh, en = band(dollar.get("change_20d"), (-0.01, 12, "美元走弱", "USD weakening"), (0.01, -12, "美元走强", "USD strengthening"), ("美元平稳", "USD stable"))
    components.append(component("DTWEXBGS", dollar, impact, zh, en))
    impact, zh, en = band(ten.get("change_20d"), (-0.20, 7, "名义利率回落", "Nominal yield falling"), (0.20, -7, "名义利率上行", "Nominal yield rising"), ("名义利率平稳", "Nominal yield stable"))
    components.append(component("DGS10", ten, impact, zh, en))
    breakeven = None
    breakeven_change = None
    if finite(ten.get("value")) is not None and finite(real.get("value")) is not None:
        breakeven = float(ten["value"]) - float(real["value"])
    if finite(ten.get("change_20d")) is not None and finite(real.get("change_20d")) is not None:
        breakeven_change = float(ten["change_20d"]) - float(real["change_20d"])
    breakeven_observation = {"value": round(breakeven, 6) if breakeven is not None else None, "change_20d": round(breakeven_change, 8) if breakeven_change is not None else None, "as_of": min(filter(None, [ten.get("as_of"), real.get("as_of")]), default=None)}
    impact, zh, en = band(breakeven_change, (-0.10, -6, "通胀预期回落", "Inflation expectations falling"), (0.10, 6, "通胀预期上行", "Inflation expectations rising"), ("通胀预期平稳", "Inflation expectations stable"))
    components.append({**component("DGS10", breakeven_observation, impact, zh, en), "key": "BREAKEVEN10", "name": "10年通胀预期", "name_en": "10Y breakeven inflation"})
    return snapshot("GOLD", components)


def build_macro_context(cache_dir: Path, end: date) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    observations: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for series_id in SERIES:
        try:
            frame, source = fetch_fred_series(series_id, cache_dir)
            observations[series_id] = {**point(frame, end, percent_change=series_id in {"DTWEXBGS", "DEXCHUS"}), "source": source}
        except Exception as exc:
            observations[series_id] = {"value": None, "change_20d": None, "as_of": None, "source": "unavailable"}
            failures.append({"series": series_id, "error": f"{type(exc).__name__}: {exc}"})
    return [score_cn(observations), score_us(observations), score_gold(observations)], failures


def context_by_market(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("market") or ""): item for item in items}
