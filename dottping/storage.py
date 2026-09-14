"""Persistenza SQLite: medici sorvegliati e ultimo stato letto.

Due tabelle sole:
  · `medico_watch` — chi sorveglia cosa, una riga per (chat, medico)
  · `kv`           — l'ultimo stato letto per ogni medico, per capire se è cambiato

Tutto sincrono dentro, esposto async (asyncio.to_thread) per non bloccare il bot.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS medico_watch (
    chat_id     INTEGER NOT NULL,
    id_luogo    TEXT NOT NULL,
    cognome     TEXT NOT NULL,
    nome        TEXT,
    nome_medico TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (chat_id, id_luogo)
);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    # ---------- medici sorvegliati ----------

    def _watch_add_sync(self, chat_id: int, id_luogo: str, cognome: str,
                        nome: str, nome_medico: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO medico_watch "
                "(chat_id, id_luogo, cognome, nome, nome_medico, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, id_luogo, cognome, nome, nome_medico, _now()),
            )
            return cur.rowcount > 0

    async def medico_watch_add(self, chat_id: int, id_luogo: str, cognome: str,
                               nome: str = "", nome_medico: str = "") -> bool:
        """Aggiunge un medico alla lista di questa chat. False se c'era già."""
        return await asyncio.to_thread(
            self._watch_add_sync, chat_id, id_luogo, cognome, nome, nome_medico
        )

    def _watch_remove_sync(self, chat_id: int, id_luogo: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM medico_watch WHERE chat_id = ? AND id_luogo = ?",
                (chat_id, id_luogo),
            )
            return cur.rowcount > 0

    async def medico_watch_remove(self, chat_id: int, id_luogo: str) -> bool:
        return await asyncio.to_thread(self._watch_remove_sync, chat_id, id_luogo)

    def _watch_list_sync(self, chat_id: int | None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM medico_watch"
        params: tuple = ()
        if chat_id is not None:
            sql += " WHERE chat_id = ?"
            params = (chat_id,)
        sql += " ORDER BY created_at"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    async def medico_watch_list(self, chat_id: int | None = None) -> list[dict[str, Any]]:
        """I medici sorvegliati da una chat, o tutti se chat_id è None."""
        return await asyncio.to_thread(self._watch_list_sync, chat_id)

    # ---------- ultimo stato letto ----------

    def _kv_get_sync(self, key: str, max_age_s: int | None) -> Any | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value, updated_at FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        if max_age_s is not None:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"])).total_seconds()
            except ValueError:
                return None
            if age > max_age_s:
                return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return None

    async def kv_get(self, key: str, max_age_s: int | None = None) -> Any | None:
        return await asyncio.to_thread(self._kv_get_sync, key, max_age_s)

    def _kv_set_sync(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value, default=str), _now()),
            )

    async def kv_set(self, key: str, value: Any) -> None:
        await asyncio.to_thread(self._kv_set_sync, key, value)

    def _kv_prefix_sync(self, prefix: str, limit: int) -> list[Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT value FROM kv WHERE key LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (prefix + "%", limit),
            ).fetchall()
        out: list[Any] = []
        for row in rows:
            try:
                out.append(json.loads(row["value"]))
            except json.JSONDecodeError:
                continue
        return out

    async def kv_prefix(self, prefix: str, limit: int = 20) -> list[Any]:
        return await asyncio.to_thread(self._kv_prefix_sync, prefix, limit)
