#!/usr/bin/env python3
"""Guarda dentro data/dottping.db senza installare un client SQL.

    python tools/db_peek.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        from dottping.config import load_settings
        db = load_settings().db_path
    except RuntimeError:
        db = Path(__file__).resolve().parent.parent / "data" / "dottping.db"

    if not db.exists():
        print(f"Database non ancora creato: {db}")
        print("Viene creato al primo avvio del bot.")
        return 1

    print(f"📁 {db}  ({db.stat().st_size:,} byte)\n")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    tabelle = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]

    for t in tabelle:
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        colonne = [c["name"] for c in conn.execute(f"PRAGMA table_info({t})")]
        print(f"── {t}  ({n} righe)")
        print(f"   colonne: {', '.join(colonne)}")
        if n:
            ordine = "created_at" if "created_at" in colonne else (
                "updated_at" if "updated_at" in colonne else colonne[0])
            for r in conn.execute(f"SELECT * FROM {t} ORDER BY {ordine} DESC LIMIT 5"):
                pezzi = []
                for c in colonne:
                    v = str(r[c])
                    pezzi.append(f"{c}={v[:60]}{'…' if len(v) > 60 else ''}")
                print(f"   · {'  '.join(pezzi)}")
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
