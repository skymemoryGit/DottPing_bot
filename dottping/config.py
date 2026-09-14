"""Configurazione, letta da .env / variabili d'ambiente.

Regola del progetto: qui stanno solo le impostazioni **globali** (token, orari,
quale numero guardare). *Quali* medici sorvegliare è una scelta dell'utente e
vive nel database, decisa dalla chat con /medico_on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _ore(name: str, default: list[int]) -> list[int]:
    """Legge "8,20" e restituisce [8, 20], scartando ciò che non è un'ora valida."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return list(default)
    ore: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            h = int(chunk)
        except ValueError:
            continue
        if 0 <= h <= 23 and h not in ore:
            ore.append(h)
    return sorted(ore) or list(default)


def _ids(name: str) -> set[int]:
    raw = os.getenv(name, "") or ""
    out: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_user_ids: set[int] = field(default_factory=set)
    db_path: Path = ROOT / "data" / "dottping.db"
    log_level: str = "INFO"

    timezone: str = "Europe/Rome"
    medico_ore: list[int] = field(default_factory=lambda: [8, 20])
    medico_campo: str = "illimitati"   # illimitati | termine | entrambi
    medico_cognome: str = ""           # default per /medico senza argomenti
    medico_nome: str = ""
    max_sorvegliati: int = 10

    @property
    def restricted(self) -> bool:
        return bool(self.allowed_user_ids)


def load_settings() -> Settings:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN mancante. Copia .env.example in .env e inserisci "
            "il token del bot (quello di DottPing, non quello di un altro bot)."
        )

    db_raw = (os.getenv("DOTTPING_DB_PATH") or "data/dottping.db").strip()
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    try:
        massimo = int((os.getenv("MAX_SORVEGLIATI") or "10").strip())
    except ValueError:
        massimo = 10

    return Settings(
        token=token,
        allowed_user_ids=_ids("ALLOWED_USER_IDS"),
        db_path=db_path,
        log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
        timezone=(os.getenv("TIMEZONE") or "Europe/Rome").strip(),
        medico_ore=_ore("CHECK_HOURS", [8, 20]),
        medico_campo=(os.getenv("CAMPO_SORVEGLIATO") or "illimitati").strip().lower(),
        medico_cognome=(os.getenv("COGNOME_DEFAULT") or "").strip(),
        medico_nome=(os.getenv("NOME_DEFAULT") or "").strip(),
        max_sorvegliati=max(1, massimo),
    )
