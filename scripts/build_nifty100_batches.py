#!/usr/bin/env python3
"""
Build ready-to-use Nifty 100 batch lists.

Creates:
  trades/nifty100_batches/watchlist_batch_{1..4}.txt
  trades/nifty100_batches/available_batch_{1..N}.txt
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import NIFTY100_WATCHLIST


BATCH_DIR = ROOT / "trades" / "nifty100_batches"
REPLAY_DIR = ROOT / "trades" / "nifty100_replay"


def chunked(items, size):
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def write_batches(prefix: str, items: list[str], size: int = 25) -> int:
    count = 0
    for count, batch in enumerate(chunked(items, size), start=1):
        path = BATCH_DIR / f"{prefix}_batch_{count}.txt"
        path.write_text("\n".join(batch) + "\n", encoding="utf-8")
    return count


def main() -> None:
    BATCH_DIR.mkdir(parents=True, exist_ok=True)

    watchlist = list(NIFTY100_WATCHLIST)
    available = {
        path.name.replace("_5m_replay.csv", "").upper()
        for path in REPLAY_DIR.glob("*_5m_replay.csv")
    }
    available_watchlist = [symbol for symbol in watchlist if symbol in available]

    watch_count = write_batches("watchlist", watchlist, size=25)
    available_count = write_batches("available", available_watchlist, size=25)

    print(f"Wrote {watch_count} full-watchlist batch files to {BATCH_DIR}")
    print(f"Wrote {available_count} available-replay batch files to {BATCH_DIR}")
    print(f"Available Nifty 100 symbols with replay files: {len(available_watchlist)}")


if __name__ == "__main__":
    main()
