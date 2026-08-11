from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scripts.update_history import eligible_recommendations, update_history


def candidate(symbol: str, score: float, stage: str, close: float, last_date: str) -> dict:
    return {
        "market": "US",
        "symbol": symbol,
        "monitor_symbol": symbol,
        "name": symbol,
        "signal_stage": stage,
        "stage_label": "今日触发",
        "recommend_score": score,
        "recommend_label": "优先",
        "last_date": last_date,
        "last_close": close,
        "pattern": "疑似3浪突破",
        "wave_level": "中级别",
        "support": close * 0.95,
        "invalid_below": close * 0.9,
        "target_1": close * 1.2,
        "risk_reward": 2,
    }


class UpdateHistoryTests(unittest.TestCase):
    def test_only_high_score_entry_alerts_are_snapshotted(self) -> None:
        payload = {
            "candidates": [
                candidate("HIGH", 88, "trigger", 100, "2026-01-02"),
                candidate("WATCH", 92, "watch", 100, "2026-01-02"),
                candidate("LOW", 84.9, "probe", 100, "2026-01-02"),
            ]
        }
        rows = eligible_recommendations(payload)
        self.assertEqual([row["symbol"] for row in rows], ["HIGH"])

    def test_history_deduplicates_an_episode_and_calculates_trading_day_returns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload_path = root / "candidates.json"
            history_dir = root / "history"
            cache_dir = root / "cache"
            entry_date = date(2026, 1, 2)
            dates = [entry_date + timedelta(days=offset) for offset in range(0, 190)]
            prices = pd.DataFrame(
                {
                    "date": dates,
                    "close": [100 + offset for offset in range(len(dates))],
                }
            )
            cache_path = cache_dir / "US" / "HIGH_20260102_20260711.csv"
            cache_path.parent.mkdir(parents=True)
            prices.to_csv(cache_path, index=False)

            payload_path.write_text(
                json.dumps(
                    {
                        "published_at": "2026-01-02T16:00:00+08:00",
                        "metadata": {"scan_generated_at": "2026-01-02T15:59:00"},
                        "candidates": [candidate("HIGH", 88, "probe", 100, entry_date.isoformat())],
                    }
                ),
                encoding="utf-8",
            )
            first = update_history(payload_path, history_dir, cache_dir)
            self.assertEqual(first["signal_count"], 1)
            self.assertEqual(first["horizons"][0]["sample_count"], 1)
            self.assertAlmostEqual(first["horizons"][0]["average_return"], 0.05)
            self.assertEqual(first["horizons"][0]["win_rate"], 1.0)

            payload_path.write_text(
                json.dumps(
                    {
                        "published_at": "2026-01-03T06:30:00+08:00",
                        "metadata": {},
                        "candidates": [candidate("HIGH", 90, "trigger", 102, "2026-01-03")],
                    }
                ),
                encoding="utf-8",
            )
            second = update_history(payload_path, history_dir, cache_dir)
            self.assertEqual(second["signal_count"], 1)
            self.assertEqual(second["snapshot_day_count"], 2)
            signal = second["signals"][0]
            self.assertEqual(signal["entry_stage"], "probe")
            self.assertEqual(signal["latest_stage"], "trigger")

            snapshot = json.loads((history_dir / "snapshots" / "2026-01-02.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["runs"][0]["recommendation_count"], 1)


if __name__ == "__main__":
    unittest.main()
