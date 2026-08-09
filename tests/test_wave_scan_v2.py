from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from scanner.wave_scan import market_index_snapshot, score_candidate_v2


def confirmed_pullback_prices() -> pd.DataFrame:
    count = 300
    closes = np.linspace(80.0, 99.0, count)
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=count, freq="B"),
            "open": closes - 0.2,
            "high": closes + 0.5,
            "low": closes - 0.6,
            "close": closes,
            "volume": np.full(count, 1_000_000.0),
        }
    )
    frame.loc[count - 3, ["open", "high", "low", "close"]] = [98.4, 100.0, 98.2, 99.0]
    frame.loc[count - 2, ["open", "high", "low", "close"]] = [99.0, 100.5, 99.0, 99.6]
    frame.loc[count - 1, ["open", "high", "low", "close", "volume"]] = [100.0, 102.5, 99.8, 102.0, 1_500_000]
    return frame


def left_probe_prices() -> pd.DataFrame:
    frame = confirmed_pullback_prices()
    count = len(frame)
    frame.loc[count - 2, ["open", "high", "low", "close"]] = [101.0, 101.4, 100.8, 101.1]
    frame.loc[count - 1, ["open", "high", "low", "close", "volume"]] = [101.0, 101.1, 100.3, 100.5, 900_000]
    return frame


class WaveScanV2Tests(unittest.TestCase):
    def test_confirmed_pullback_becomes_trigger_with_component_scores(self) -> None:
        row = {
            "pattern": "4浪回踩候选",
            "last_close": 102.0,
            "support": 100.0,
            "invalid_below": 95.0,
            "target_1": 120.0,
            "structure_fit": 1.0,
        }

        result = score_candidate_v2(row, confirmed_pullback_prices(), True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["signal_stage"], "trigger")
        self.assertEqual(result["stage_label"], "右侧触发")
        self.assertGreaterEqual(result["score"], 85)
        self.assertEqual(result["structure_score"], 35.0)
        self.assertIn("支撑收复", result["confirmation_detail"])
        self.assertGreaterEqual(result["risk_reward"], 1.5)

    def test_candidate_with_weak_risk_reward_is_rejected(self) -> None:
        row = {
            "pattern": "2浪回撤候选",
            "last_close": 102.0,
            "support": 100.0,
            "invalid_below": 90.0,
            "target_1": 110.0,
            "structure_fit": 1.0,
        }

        self.assertIsNone(score_candidate_v2(row, confirmed_pullback_prices(), False))

    def test_near_support_high_quality_setup_becomes_left_probe(self) -> None:
        row = {
            "pattern": "4浪回踩候选",
            "last_close": 100.5,
            "support": 100.0,
            "invalid_below": 95.0,
            "target_1": 120.0,
            "structure_fit": 1.0,
        }

        result = score_candidate_v2(row, left_probe_prices(), True, 80.0)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["signal_stage"], "probe")
        self.assertEqual(result["stage_label"], "左侧试错")
        self.assertGreaterEqual(result["risk_reward"], 2.0)

    def test_weak_market_reduces_candidate_score(self) -> None:
        row = {
            "pattern": "4浪回踩候选",
            "last_close": 102.0,
            "support": 100.0,
            "invalid_below": 95.0,
            "target_1": 120.0,
            "structure_fit": 1.0,
        }

        strong = score_candidate_v2(dict(row), confirmed_pullback_prices(), True, 80.0)
        weak = score_candidate_v2(dict(row), confirmed_pullback_prices(), True, 20.0)

        self.assertIsNotNone(strong)
        self.assertIsNotNone(weak)
        assert strong is not None and weak is not None
        self.assertEqual(strong["score"] - weak["score"], 8.0)
        self.assertEqual(weak["market_context_label"], "大盘偏弱")

    def test_market_index_snapshot_classifies_uptrend(self) -> None:
        closes = np.linspace(100.0, 180.0, 220)
        prices = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=220, freq="B"),
                "open": closes - 0.2,
                "high": closes + 0.5,
                "low": closes - 0.5,
                "close": closes,
                "volume": np.full(220, 1_000_000.0),
            }
        )

        snapshot = market_index_snapshot(
            {"market": "US", "symbol": "^GSPC", "name": "标普500"},
            prices,
        )

        self.assertEqual(snapshot["status"], "强势")
        self.assertEqual(snapshot["score"], 100.0)


if __name__ == "__main__":
    unittest.main()
