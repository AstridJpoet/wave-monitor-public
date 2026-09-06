#!/usr/bin/env python3
"""Analyze verified High-Flyer public footprints without look-ahead bias."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scanner.ma_touch_backtest import (
    fetch_a_share_daily,
    fetch_yahoo_daily,
    normalize_ohlc,
    read_cached_csv,
    request_get_with_retries,
    write_cache,
)


BENCHMARKS = {
    "CSI300": ("510300.SS", "沪深300ETF"),
    "CSI500": ("510500.SS", "中证500ETF"),
    "CSI1000": ("512100.SS", "中证1000ETF"),
}
HORIZONS = (21, 63)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def benchmark_for_product(product_names: str) -> tuple[str, str, str]:
    text = product_names or ""
    if "1000" in text or "一千" in text:
        symbol, name = BENCHMARKS["CSI1000"]
        return symbol, name, "product_name"
    if "500" in text or "五百" in text:
        symbol, name = BENCHMARKS["CSI500"]
        return symbol, name, "product_name"
    if "300" in text or "三百" in text:
        symbol, name = BENCHMARKS["CSI300"]
        return symbol, name, "product_name"
    symbol, name = BENCHMARKS["CSI300"]
    return symbol, name, "default"


def value_on_or_before(prices: pd.DataFrame, target: date) -> tuple[int, pd.Series] | None:
    available = prices[prices["date"].dt.date <= target]
    if available.empty:
        return None
    index = int(available.index[-1])
    return index, prices.loc[index]


def features_at(prices: pd.DataFrame, target: date) -> dict[str, float | str | None]:
    located = value_on_or_before(prices, target)
    if located is None:
        return {"feature_as_of": None}
    index, row = located
    history = prices.iloc[: index + 1]
    close = history["close"].astype(float)
    returns = close.pct_change()
    result: dict[str, float | str | None] = {
        "feature_as_of": pd.Timestamp(row["date"]).date().isoformat(),
        "price_at_report": finite(row["close"]),
    }
    for window in (20, 60, 120, 252):
        result[f"ret_{window}d"] = (
            finite(close.iloc[-1] / close.iloc[-window - 1] - 1) if len(close) > window else None
        )
    result["volatility_60d"] = (
        finite(returns.tail(60).std(ddof=1) * math.sqrt(252)) if returns.tail(60).count() >= 40 else None
    )
    peak = close.tail(252).max()
    result["drawdown_252d"] = finite(close.iloc[-1] / peak - 1) if peak else None
    for window in (50, 200):
        average = close.tail(window).mean() if len(close) >= window else np.nan
        result[f"ma_{window}_gap"] = finite(close.iloc[-1] / average - 1)
    delta = close.diff()
    gain = delta.clip(lower=0).tail(14).mean()
    loss = -delta.clip(upper=0).tail(14).mean()
    if len(close) >= 15 and loss == 0:
        result["rsi_14"] = 100.0
    elif len(close) >= 15 and loss > 0:
        result["rsi_14"] = finite(100 - 100 / (1 + gain / loss))
    else:
        result["rsi_14"] = None
    volume = history["volume"].astype(float).replace(0, np.nan)
    recent_volume = volume.tail(20).mean()
    baseline_volume = volume.tail(120).mean()
    result["volume_ratio_20_120"] = finite(recent_volume / baseline_volume) if baseline_volume else None
    true_range = pd.concat(
        [
            history["high"] - history["low"],
            (history["high"] - close.shift()).abs(),
            (history["low"] - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr_20_pct"] = finite(true_range.tail(20).mean() / close.iloc[-1])
    return result


def forward_path(prices: pd.DataFrame, publication_date: date, horizon: int) -> dict[str, Any]:
    eligible = prices[prices["date"].dt.date > publication_date]
    if eligible.empty:
        return {}
    entry_index = int(eligible.index[0])
    exit_index = entry_index + horizon - 1
    if exit_index >= len(prices):
        return {}
    entry = prices.loc[entry_index]
    exit_row = prices.loc[exit_index]
    entry_price = finite(entry["open"])
    exit_price = finite(exit_row["close"])
    if not entry_price or exit_price is None:
        return {}
    path = prices.iloc[entry_index : exit_index + 1]
    return {
        "entry_date": pd.Timestamp(entry["date"]).date().isoformat(),
        "entry_price": entry_price,
        "exit_date": pd.Timestamp(exit_row["date"]).date().isoformat(),
        "exit_price": exit_price,
        "return": exit_price / entry_price - 1,
        "mfe": finite(path["high"].max() / entry_price - 1),
        "mae": finite(path["low"].min() / entry_price - 1),
    }


def interval_return(prices: pd.DataFrame, start: date, end: date) -> float | None:
    eligible = prices[(prices["date"].dt.date >= start) & (prices["date"].dt.date <= end)]
    if eligible.empty:
        return None
    entry = finite(eligible.iloc[0]["open"])
    exit_price = finite(eligible.iloc[-1]["close"])
    if not entry or exit_price is None:
        return None
    return exit_price / entry - 1


def parse_sina_history(text: str) -> pd.DataFrame:
    match = re.search(r"(\[\{.*\}\])", text, re.DOTALL)
    if not match:
        raise RuntimeError("Sina returned an unexpected response")
    payload = json.loads(match.group(1))
    frame = pd.DataFrame(payload).rename(columns={"day": "date"})
    if frame.empty:
        raise RuntimeError("empty Sina history")
    return normalize_ohlc(frame)


def fetch_sina_history(
    symbol: str,
    sec_code: str,
    start: date,
    end: date,
    cache_dir: Path,
) -> pd.DataFrame:
    cache_path = cache_dir / "HF_SINA" / f"{symbol}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    cached = read_cached_csv(cache_path)
    if cached is not None:
        return normalize_ohlc(cached)
    prefix = "sh" if sec_code.startswith(("5", "6", "9")) else "sz"
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
    response = request_get_with_retries(
        url,
        params={"symbol": f"{prefix}{sec_code}", "scale": 240, "ma": "no", "datalen": 1023},
        timeout=25,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
    )
    frame = parse_sina_history(response.text)
    frame = frame[(frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)].reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("Sina history does not cover the requested range")
    write_cache(frame, cache_path)
    return frame


def fetch_history(
    symbol: str,
    sec_code: str,
    start: date,
    end: date,
    cache_dir: Path,
) -> tuple[pd.DataFrame, str]:
    try:
        frame = fetch_yahoo_daily(symbol, start, end, cache_dir, True, cache_market="HF_YAHOO")
        return frame.reset_index(drop=True), "yahoo"
    except Exception as yahoo_error:
        try:
            frame = fetch_a_share_daily(sec_code, start, end, cache_dir, True)
            return frame.reset_index(drop=True), "eastmoney"
        except Exception as eastmoney_error:
            try:
                frame = fetch_sina_history(symbol, sec_code, start, end, cache_dir)
                return frame.reset_index(drop=True), "sina_unadjusted"
            except Exception as sina_error:
                raise RuntimeError(
                    f"Yahoo: {yahoo_error}; Eastmoney: {eastmoney_error}; Sina: {sina_error}"
                ) from sina_error


def summarize_metric(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    values = pd.to_numeric(frame.get(column), errors="coerce").dropna()
    if values.empty:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "win_rate": None,
            "mean_ex_best": None,
            "mean_ex_top2": None,
            "trimmed_mean": None,
        }
    ordered = values.sort_values()
    trimmed = ordered.iloc[1:-1] if len(ordered) >= 10 else ordered
    ex_best = ordered.iloc[:-1] if len(ordered) >= 2 else ordered
    ex_top2 = ordered.iloc[:-2] if len(ordered) >= 3 else ordered
    return {
        "n": int(len(values)),
        "mean": round(float(values.mean()), 8),
        "median": round(float(values.median()), 8),
        "win_rate": round(float((values > 0).mean()), 8),
        "mean_ex_best": round(float(ex_best.mean()), 8),
        "mean_ex_top2": round(float(ex_top2.mean()), 8),
        "trimmed_mean": round(float(trimmed.mean()), 8),
    }


def render_report(frame: pd.DataFrame, summary: dict[str, Any]) -> str:
    lines = [
        "# 幻方公开持仓足迹研究",
        "",
        f"生成时间：{summary['generated_at']}",
        "",
        "## 结论边界",
        "",
        "本报告只使用巨潮资讯公开定期报告中经 PDF 股东表核验的记录。它不是幻方完整交易流水，也无法看到未进前十大股东的小仓位、对冲和实际买卖日。",
        "",
        "报告期特征用于研究其季末持仓偏好；可执行的跟随回测则从公告后的第一个交易日开盘开始，避免前视偏差。",
        "",
        "## 样本",
        "",
        f"- 已核验足迹：{summary['verified_rows']} 条，{summary['unique_companies']} 家公司。",
        f"- 成功取得行情：{summary['priced_rows']} 条；失败：{summary['price_error_rows']} 条。",
        f"- 行情来源：{json.dumps(summary['price_sources'], ensure_ascii=False)}。新浪回退数据未复权，明细中单独标记。",
        f"- 报告期覆盖：{summary.get('period_start') or 'N/A'} 至 {summary.get('period_end') or 'N/A'}。",
        "",
        "## 公告后跟随表现",
        "",
        "| 持有期 | 样本 | 胜率 | 平均收益 | 中位收益 | 跑赢指数比例 | 平均超额 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for horizon in HORIZONS:
        returns = summary["outcomes"][f"return_{horizon}d"]
        excess = summary["outcomes"][f"excess_{horizon}d"]
        lines.append(
            f"| {horizon}个交易日 | {returns['n']} | {pct(returns['win_rate'])} | "
            f"{pct(returns['mean'])} | {pct(returns['median'])} | {pct(excess['win_rate'])} | {pct(excess['mean'])} |"
        )
    lines.extend(
        [
            "",
            f"稳健性检查：剔除收益最高的两条记录后，21日平均收益为 {pct(summary['outcomes']['return_21d']['mean_ex_top2'])}，63日为 {pct(summary['outcomes']['return_63d']['mean_ex_top2'])}。这能观察结果是否依赖少数极端赢家。",
            "",
            "指数优先按产品名中的中证300/500/1000识别；无法识别时使用沪深300ETF，并在明细中标为默认基准。ETF用于取得完整的同期可交易价格。",
            "",
            "## 披露延迟",
            "",
            f"报告期末到公告日的中位间隔为 {summary['disclosure_lag_days']['median']:.0f} 天。"
            if summary["disclosure_lag_days"]["median"] is not None
            else "报告期末到公告日的间隔无法计算。",
            f"从报告期末价格到公告后可买入开盘价，样本平均已变动 {pct(summary['report_to_publication_return']['mean'])}，中位变动 {pct(summary['report_to_publication_return']['median'])}。",
            "",
            "下表是报告期末后的表现，只用于判断季末持仓画像是否曾有延续性，投资者当时尚未看到报告，不能把它当成可执行回测：",
            "",
            "| 观察期 | 样本 | 胜率 | 平均收益 | 中位收益 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for horizon in HORIZONS:
        metric = summary["outcomes"][f"snapshot_return_{horizon}d"]
        lines.append(
            f"| 报告期后{horizon}个交易日 | {metric['n']} | {pct(metric['win_rate'])} | "
            f"{pct(metric['mean'])} | {pct(metric['median'])} |"
        )
    lines.extend(
        [
            "",
            "## 季末持仓技术画像",
            "",
            "| 指标 | 样本 | 中位数 | 平均数 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    labels = {
        "ret_20d": "前20日收益",
        "ret_60d": "前60日收益",
        "ret_120d": "前120日收益",
        "ret_252d": "前252日收益",
        "volatility_60d": "60日年化波动",
        "drawdown_252d": "距252日高点回撤",
        "ma_50_gap": "相对MA50",
        "ma_200_gap": "相对MA200",
        "atr_20_pct": "ATR20/价格",
        "volume_ratio_20_120": "20/120日量比",
    }
    for column, label in labels.items():
        metric = summary["features"][column]
        formatter = pct if column != "volume_ratio_20_120" else lambda value: "N/A" if value is None else f"{value:.2f}x"
        lines.append(
            f"| {label} | {metric['n']} | {formatter(metric['median'])} | {formatter(metric['mean'])} |"
        )
    lines.extend(
        [
            "",
            "## 怎么用于自建模型",
            "",
            "### 这批数据揭示了什么",
            "",
            "- 公开足迹有明显的中期动量特征：60日收益中位数为 "
            f"{pct(summary['features']['ret_60d']['median'])}，120日收益中位数为 {pct(summary['features']['ret_120d']['median'])}。",
            "- 同时风险暴露很高：60日年化波动中位数为 "
            f"{pct(summary['features']['volatility_60d']['median'])}，ATR20/价格中位数为 {pct(summary['features']['atr_20_pct']['median'])}。",
            "- 报告期后的负收益说明这组已进入前十大股东的强势股票随后出现明显均值回归；它不能证明幻方整体策略亏损，因为这里不知道其入场价、仓位、卖出日和对冲。",
            "- 小市值公司更容易让同等金额的基金仓位进入前十大股东，公开样本因此天然偏向小票和集中持仓，不能代表完整投资组合。",
            "- 没有公开证据表明幻方使用艾略特波浪理论；将两者直接等同会制造无法验证的故事。",
            "",
            "### 可执行的自建路线",
            "",
            "这批正样本只能描述公开足迹，不能直接训练出幻方模型。下一步应为每个报告期加入同市场、同规模、同流动性的未入选股票作为对照，使用当时可得数据构建横截面训练集，并严格按时间切分训练和验证。",
            "",
            "建议首版只用可解释因子：20/60/120日相对强弱、波动率、回撤、MA50/MA200位置、ATR和成交量变化；基本面与估值因子必须采用可追溯的点时数据后再加入。模型输出只作为 Wave Monitor 的辅助排序，必须继续经过波浪结构、右侧价格确认、大盘与宏观环境过滤，并对过热动量和高波动扣分。",
            "",
            "在完成匹配对照组和样本外检验以前，不把“像幻方历史公开持仓”转换成买入提醒。这是研究分数，不是收益预测。",
            "",
            "## 明细",
            "",
            "完整逐条结果见 `enriched_footprints.csv`，包含公告原文链接、证据页、特征日期、公告后入场与退出日期。",
        ]
    )
    return "\n".join(lines) + "\n"


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(input_path.open(encoding="utf-8", newline="")))
    start = date.fromisoformat(args.price_start)
    end = date.fromisoformat(args.end)
    cache_dir = Path(args.cache_dir)

    histories: dict[str, pd.DataFrame] = {}
    history_sources: dict[str, str] = {}
    errors: dict[str, str] = {}
    benchmark_histories: dict[str, pd.DataFrame] = {}

    for symbol, _name in BENCHMARKS.values():
        try:
            benchmark_histories[symbol] = fetch_yahoo_daily(
                symbol, start, end, cache_dir, True, cache_market="HF_BENCHMARK"
            ).reset_index(drop=True)
        except Exception as exc:
            errors[f"benchmark:{symbol}"] = str(exc)

    enriched: list[dict[str, Any]] = []
    for number, row in enumerate(rows, 1):
        symbol = row["symbol"]
        if symbol not in histories and symbol not in errors:
            try:
                histories[symbol], history_sources[symbol] = fetch_history(
                    symbol, row["sec_code"], start, end, cache_dir
                )
            except Exception as exc:
                errors[symbol] = str(exc)
        result: dict[str, Any] = dict(row)
        result["price_source"] = history_sources.get(symbol, "")
        result["price_error"] = errors.get(symbol, "")
        product_text = row.get("verified_product_names") or row.get("product_names") or ""
        benchmark_symbol, benchmark_name, benchmark_rule = benchmark_for_product(product_text)
        result.update(
            {
                "benchmark_symbol": benchmark_symbol,
                "benchmark_name": benchmark_name,
                "benchmark_rule": benchmark_rule,
            }
        )
        prices = histories.get(symbol)
        if prices is not None:
            report_date = date.fromisoformat(row["report_period"])
            publication_date = date.fromisoformat(
                row.get("first_publication_date") or row["publication_date"]
            )
            feature = features_at(prices, report_date)
            result.update(feature)
            result["disclosure_lag_days"] = (publication_date - report_date).days
            first_public_entry = forward_path(prices, publication_date, 1)
            report_price = finite(feature.get("price_at_report"))
            public_entry_price = finite(first_public_entry.get("entry_price"))
            result["report_to_publication_return"] = (
                public_entry_price / report_price - 1
                if report_price and public_entry_price is not None
                else None
            )
            for horizon in HORIZONS:
                snapshot_path = forward_path(prices, report_date, horizon)
                result[f"snapshot_return_{horizon}d"] = snapshot_path.get("return")
                path = forward_path(prices, publication_date, horizon)
                for key, value in path.items():
                    result[f"{key}_{horizon}d"] = value
                benchmark = benchmark_histories.get(benchmark_symbol)
                benchmark_return = None
                if benchmark is not None and path:
                    benchmark_return = interval_return(
                        benchmark,
                        date.fromisoformat(path["entry_date"]),
                        date.fromisoformat(path["exit_date"]),
                    )
                result[f"benchmark_return_{horizon}d"] = benchmark_return
                result[f"excess_{horizon}d"] = (
                    path["return"] - benchmark_return
                    if path and benchmark_return is not None
                    else None
                )
        enriched.append(result)
        print(f"[{number}/{len(rows)}] {symbol} {'ok' if prices is not None else 'price_error'}")

    frame = pd.DataFrame(enriched)
    frame.to_csv(output_dir / "enriched_footprints.csv", index=False)
    outcomes: dict[str, Any] = {}
    for horizon in HORIZONS:
        outcomes[f"snapshot_return_{horizon}d"] = summarize_metric(
            frame, f"snapshot_return_{horizon}d"
        )
        outcomes[f"return_{horizon}d"] = summarize_metric(frame, f"return_{horizon}d")
        outcomes[f"excess_{horizon}d"] = summarize_metric(frame, f"excess_{horizon}d")
        outcomes[f"mfe_{horizon}d"] = summarize_metric(frame, f"mfe_{horizon}d")
        outcomes[f"mae_{horizon}d"] = summarize_metric(frame, f"mae_{horizon}d")
    feature_columns = (
        "ret_20d",
        "ret_60d",
        "ret_120d",
        "ret_252d",
        "volatility_60d",
        "drawdown_252d",
        "ma_50_gap",
        "ma_200_gap",
        "atr_20_pct",
        "volume_ratio_20_120",
    )
    features = {column: summarize_metric(frame, column) for column in feature_columns}
    periods = pd.to_datetime(frame.get("report_period"), errors="coerce").dropna()
    price_sources = {
        str(key): int(value)
        for key, value in frame.get("price_source", pd.Series(dtype=str)).value_counts().items()
        if key
    }
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verified_rows": len(frame),
        "unique_companies": int(frame["symbol"].nunique()) if not frame.empty else 0,
        "priced_rows": int((frame.get("price_error", "") == "").sum()) if not frame.empty else 0,
        "price_error_rows": int((frame.get("price_error", "") != "").sum()) if not frame.empty else 0,
        "price_sources": price_sources,
        "period_start": periods.min().date().isoformat() if not periods.empty else None,
        "period_end": periods.max().date().isoformat() if not periods.empty else None,
        "outcomes": outcomes,
        "disclosure_lag_days": summarize_metric(frame, "disclosure_lag_days"),
        "report_to_publication_return": summarize_metric(frame, "report_to_publication_return"),
        "features": features,
        "errors": errors,
        "methodology": {
            "feature_date": "last trading session on or before report period end",
            "entry": "first trading session open after first public filing date",
            "exit": "adjusted close after 21 or 63 trading sessions",
            "price_adjustment": "Yahoo and Eastmoney are adjusted; Sina delisted-stock fallback is unadjusted",
            "default_benchmark": "CSI300 ETF proxy when product benchmark cannot be identified",
        },
    }
    (output_dir / "analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(render_report(frame, summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Analyze verified public High-Flyer footprints.")
    parser.add_argument("--input", default=str(root / "output" / "verified_footprints.csv"))
    parser.add_argument("--output-dir", default=str(root / "output"))
    parser.add_argument("--cache-dir", default=str(root / "cache" / "prices"))
    parser.add_argument("--price-start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    return parser


def main() -> int:
    analyze(build_parser().parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
