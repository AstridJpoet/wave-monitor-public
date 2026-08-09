from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from scanner.wave_scan import score_candidate_v2


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
        self.assertEqual(result["stage_label"], "今日触发")
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


if __name__ == "__main__":
    unittest.main()
