"""Utility di formattazione per i messaggi Telegram (parse_mode HTML)."""
from __future__ import annotations

import html
from typing import Iterable

TELEGRAM_LIMIT = 4096


def esc(text: object) -> str:
    """Escape HTML: obbligatorio su qualsiasi testo di terze parti (titoli, descrizioni)."""
    return html.escape(str(text), quote=False)


def link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>'


def chunks(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Spezza un messaggio lungo sui confini di riga, senza rompere i tag."""
    if len(text) <= limit:
        return [text]

    out: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                out.append(current)
                current = ""
            while len(line) > limit:
                out.append(line[:limit])
                line = line[limit:]
        current += line
    if current:
        out.append(current)
    return out


def bullet_list(items: Iterable[str]) -> str:
    return "\n".join(f"• {it}" for it in items)
