from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_public_data import PUBLIC_FIELDS, build_payload


class PublicBuildTests(unittest.TestCase):
    def test_payload_is_ranked_and_metadata_is_curated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.csv"
            metadata = root / "metadata.json"
            rows = [
                {
                    "market": "US",
                    "symbol": "AAA",
                    "name": "Alpha",
                    "pattern": "疑似3浪突破",
                    "score": "70",
                    "last_date": "2026-08-08",
                    "last_close": "105",
                    "retracement": "0.5",
                    "support": "100",
                    "invalid_below": "90",
                    "target_1": "120",
                    "target_2": "135",
                },
                {
                    "market": "CN",
                    "symbol": "600000",
                    "name": "示例公司",
                    "pattern": "4浪回踩候选",
                    "score": "82",
                    "last_date": "2026-08-08",
                    "last_close": "10.1",
                    "retracement": "0.382",
                    "support": "10",
                    "invalid_below": "9",
                    "target_1": "12",
                    "target_2": "13",
                },
            ]
            with candidates.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            metadata.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-08-08T08:00:00",
                        "instrument_count": 262,
                        "failure_count": 2,
                        "failure_rate": 0.01,
                        "duration_seconds": 30,
                        "failures": [{"error": "must stay private"}],
                        "args": {"cache_dir": "/private/path"},
                    }
                ),
                encoding="utf-8",
            )

            payload = build_payload(candidates, metadata)

            self.assertEqual(payload["candidates"][0]["monitor_symbol"], "600000.SS")
            self.assertEqual(set(payload["candidates"][0]), PUBLIC_FIELDS)
            self.assertNotIn("failures", payload["metadata"])
            self.assertNotIn("args", payload["metadata"])
            self.assertEqual(payload["metadata"]["instrument_count"], 262)

    def test_high_failure_rate_stops_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.csv"
            candidates.write_text("market,symbol\n", encoding="utf-8")
            metadata = root / "metadata.json"
            metadata.write_text('{"failure_rate": 0.8}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                build_payload(candidates, metadata)

    def test_trigger_wins_symbol_deduplication_over_higher_scored_watch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.csv"
            metadata = root / "metadata.json"
            rows = [
                {
                    "market": "US",
                    "symbol": "AAA",
                    "name": "Alpha",
                    "pattern": "4浪回踩候选",
                    "wave_level": "大级别",
                    "signal_stage": "watch",
                    "stage_label": "观察候选",
                    "score": "90",
                    "last_close": "105",
                    "support": "100",
                    "invalid_below": "95",
                    "target_1": "125",
                },
                {
                    "market": "US",
                    "symbol": "AAA",
                    "name": "Alpha",
                    "pattern": "疑似3浪突破",
                    "wave_level": "中级别",
                    "signal_stage": "trigger",
                    "stage_label": "今日触发",
                    "score": "80",
                    "last_close": "105",
                    "support": "103",
                    "invalid_below": "98",
                    "target_1": "125",
                },
            ]
            with candidates.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            metadata.write_text('{"failure_rate": 0}', encoding="utf-8")

            payload = build_payload(candidates, metadata)

            self.assertEqual(len(payload["candidates"]), 1)
            self.assertEqual(payload["candidates"][0]["signal_stage"], "trigger")
            self.assertEqual(payload["metadata"]["trigger_count"], 1)
            self.assertEqual(payload["metadata"]["deduped_candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
