"""«Sto aspettando una risposta»: lo stato che rende i comandi conversazionali.

Quando un comando ha bisogno di un dato che l'utente non ha scritto (il cognome
del medico, di solito), il bot lo chiede con una domanda normale invece di
pretendere la sintassi "/comando parametro". Il prossimo messaggio di testo di
quella chat viene letto come risposta.

Lo stato vive in `chat_data`: è per chat, non per utente. Una domanda in sospeso
per volta basta per questo bot, e un comando nuovo la annulla.
"""
from __future__ import annotations

from typing import Optional

from telegram.ext import ContextTypes

_KEY = "dottping_wait"


def set_wait(context: ContextTypes.DEFAULT_TYPE, azione: str) -> None:
    """Segna che il bot ha fatto una domanda e ne aspetta la risposta."""
    context.chat_data[_KEY] = azione


def pop_wait(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """Legge e consuma l'attesa, se c'era."""
    return context.chat_data.pop(_KEY, None)


def clear_wait(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Annulla la domanda in sospeso: un comando nuovo ha la precedenza."""
    context.chat_data.pop(_KEY, None)
