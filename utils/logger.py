"""
utils/logger.py
───────────────
Centralised logging. Every module calls get_logger(__name__).

File + console handlers are set up once.  Subsequent calls to get_logger()
just return a child of the root logger — no duplicate handlers.
"""

import logging
import os
from config.settings import paths_cfg


def _setup_root_logger() -> None:
    """Configure root logger once at import time."""
    os.makedirs(os.path.dirname(paths_cfg.log_file), exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        return  # Already configured (e.g. re-import guard)

    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    fh = logging.FileHandler(paths_cfg.log_file)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    root.addHandler(fh)
    root.addHandler(ch)

    # Silence noisy dependency logs that can leak request URLs or overwhelm the app logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


_setup_root_logger()


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call as: log = get_logger(__name__)"""
    return logging.getLogger(name)
