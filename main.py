#!/usr/bin/env python3
"""Avvio di DottPing.

    python main.py
"""
from __future__ import annotations

import logging
import sys

from dottping.bot import build_application
from dottping.config import load_settings


def main() -> int:
    try:
        settings = load_settings()
    except RuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    app = build_application(settings)
    logging.getLogger("dottping").info("Avvio polling…")
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
