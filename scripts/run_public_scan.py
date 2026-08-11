#!/usr/bin/env python3
"""Run a five-year market scan and build the static public payload."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def years_ago(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, month=2, day=28)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the public wave candidate site.")
    parser.add_argument("--a-top-n", type=int, default=800)
    parser.add_argument("--us-top-n", type=int, default=300)
    parser.add_argument("--limit-per-market", type=int, default=30)
    args = parser.parse_args()

    today = date.today()
    output_dir = ROOT / "data" / "results_wave_scan"
    scan_command = [
        sys.executable,
        str(ROOT / "scanner" / "wave_scan.py"),
        "--start",
        years_ago(today, 5).isoformat(),
        "--end",
        today.isoformat(),
        "--a-top-n",
        str(args.a_top_n),
        "--us-top-n",
        str(args.us_top_n),
        "--cache-dir",
        str(ROOT / "data" / "scan_cache"),
        "--out-dir",
        str(output_dir),
        "--recent-cache-days",
        "0",
        "--protect-existing",
        "--max-failure-rate",
        "0.35",
    ]
    subprocess.run(scan_command, cwd=ROOT, check=True)

    build_command = [
        sys.executable,
        str(ROOT / "scripts" / "build_public_data.py"),
        "--candidates",
        str(output_dir / "wave_scan_candidates.csv"),
        "--metadata",
        str(output_dir / "wave_scan_metadata.json"),
        "--output",
        str(ROOT / "site" / "data" / "candidates.json"),
        "--limit-per-market",
        str(args.limit_per_market),
    ]
    subprocess.run(build_command, cwd=ROOT, check=True)

    history_command = [
        sys.executable,
        str(ROOT / "scripts" / "update_history.py"),
        "--payload",
        str(ROOT / "site" / "data" / "candidates.json"),
        "--history-dir",
        str(ROOT / "site" / "data" / "history"),
        "--cache-dir",
        str(ROOT / "data" / "scan_cache"),
    ]
    subprocess.run(history_command, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
