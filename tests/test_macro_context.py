from __future__ import annotations

import unittest

from scanner.macro_context import adjustment, normalize_fred_csv, score_us


def observation(value: float, change: float) -> dict:
    return {"value": value, "change_20d": change, "as_of": "2026-09-03"}


class MacroContextTests(unittest.TestCase):
    def test_normalizes_current_fred_csv_shape(self) -> None:
        frame = normalize_fred_csv("observation_date,DGS10\n2026-09-02,4.79\n2026-09-03,4.77\n", "DGS10")
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.iloc[-1]["value"], 4.77)

    def test_supportive_us_context_receives_small_bonus(self) -> None:
        result = score_us(
            {
                "DFII10": observation(1.8, -0.2),
                "DGS10": observation(4.2, -0.3),
                "DGS2": observation(3.9, -0.2),
                "DTWEXBGS": observation(115, -0.02),
            }
        )
        self.assertGreaterEqual(result["score"], 65)
        self.assertEqual(result["regime"], "宏观顺风")
        self.assertEqual(result["adjustment"], 2.0)

    def test_stressed_us_context_is_a_hard_headwind(self) -> None:
        result = score_us(
            {
                "DFII10": observation(2.8, 0.3),
                "DGS10": observation(5.2, 0.5),
                "DGS2": observation(5.6, 0.4),
                "DTWEXBGS": observation(125, 0.03),
            }
        )
        self.assertLess(result["score"], 30)
        self.assertEqual(result["regime"], "宏观压力")
        self.assertEqual(adjustment(result["score"]), -12.0)


if __name__ == "__main__":
    unittest.main()
