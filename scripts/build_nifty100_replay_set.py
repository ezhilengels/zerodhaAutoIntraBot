#!/usr/bin/env python3
"""
Create a clean Nifty 100 replay folder from the downloaded Yahoo replay universe.

Outputs:
  - trades/nifty100_replay/           only exact NIFTY100_WATCHLIST matches
  - trades/nifty100_replay_report.txt summary of present/missing/extra symbols
"""

from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import NIFTY100_WATCHLIST
SOURCE_DIR = ROOT / "trades" / "yahoo_replay"
TARGET_DIR = ROOT / "trades" / "nifty100_replay"
REPORT_FILE = ROOT / "trades" / "nifty100_replay_report.txt"


def main() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    source_files = {
        path.name.replace("_5m_replay.csv", "").upper(): path
        for path in SOURCE_DIR.glob("*_5m_replay.csv")
    }
    watchlist = set(NIFTY100_WATCHLIST)

    present = sorted(watchlist & set(source_files))
    missing = sorted(watchlist - set(source_files))
    extra = sorted(set(source_files) - watchlist)

    for symbol in present:
        src = source_files[symbol]
        dst = TARGET_DIR / src.name
        shutil.copy2(src, dst)

    report_lines = [
        f"NIFTY100 watchlist count: {len(watchlist)}",
        f"Present in downloads: {len(present)}",
        f"Missing from downloads: {len(missing)}",
        f"Extra downloaded symbols: {len(extra)}",
        "",
        "Present symbols:",
        ", ".join(present) if present else "None",
        "",
        "Missing symbols:",
        ", ".join(missing) if missing else "None",
        "",
        "Extra symbols:",
        ", ".join(extra) if extra else "None",
        "",
    ]
    REPORT_FILE.write_text("\n".join(report_lines), encoding="utf-8")

    print(f"Created clean replay folder: {TARGET_DIR}")
    print(f"Copied {len(present)} Nifty 100 files")
    print(f"Missing {len(missing)} symbols")
    print(f"Wrote report: {REPORT_FILE}")


if __name__ == "__main__":
    main()
