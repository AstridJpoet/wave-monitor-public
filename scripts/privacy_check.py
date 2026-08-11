#!/usr/bin/env python3
"""Block private files and sensitive strings before static publication."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__"}
PRIVATE_NAMES = {
    ".env",
    "watchlist.yaml",
    "alert_state.json",
    "chat-id",
    "telegram-bot.token",
}
TEXT_SUFFIXES = {"", ".css", ".csv", ".html", ".js", ".json", ".md", ".py", ".txt", ".yaml", ".yml"}


def forbidden_patterns() -> list[tuple[str, re.Pattern[str]]]:
    return [
        ("macOS home path", re.compile(r"/Users/[A-Za-z0-9._-]+(?:/|$)", re.IGNORECASE)),
        ("Windows home path", re.compile(r"[A-Z]:\\Users\\[A-Za-z0-9._-]+(?:\\|$)", re.IGNORECASE)),
        ("Telegram API", re.compile("api\\." + "telegram\\.org", re.IGNORECASE)),
        ("Telegram token variable", re.compile("TELEGRAM_" + "BOT_TOKEN", re.IGNORECASE)),
        ("bot token shape", re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")),
        ("private key", re.compile("BEGIN " + "(?:RSA |OPENSSH )?PRIVATE KEY")),
        ("local app address", re.compile("127\\.0\\.0\\.1:" + "8787")),
    ]


def iter_public_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file() or relative.parts[0] == "data" or any(part in SKIP_DIRS for part in relative.parts):
            continue
        files.append(path)
    return files


def main() -> int:
    errors: list[str] = []
    publish_root = ROOT / "site"
    for path in publish_root.rglob("*"):
        if path.is_file() and path.name.lower() in PRIVATE_NAMES:
            errors.append(f"private file in publish directory: {path.relative_to(ROOT)}")

    checker_path = Path(__file__).resolve()
    patterns = forbidden_patterns()
    for path in iter_public_files():
        if path.resolve() == checker_path or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in patterns:
            if pattern.search(content):
                errors.append(f"{label}: {path.relative_to(ROOT)}")

    if errors:
        print("Privacy check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Privacy check passed for {len(iter_public_files())} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
