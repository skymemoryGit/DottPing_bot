"""Chi può parlare al bot e quanto in fretta.

Due filtri, indipendenti:

  · `guarded`     — whitelist (se ALLOWED_USER_IDS è valorizzato) + freno anti-flood
  · `solo_freno`  — solo il freno, per i comandi che devono restare aperti a tutti

Il freno serve perché il bot è pubblico: senza, un utente che tiene premuto
invio occupa il processo e, comando dopo comando, fa partire richieste verso il
portale della Regione dall'IP di questo server. Chi supera il limite viene
avvisato una volta e poi ignorato in silenzio, finché non rallenta: rispondere
a ogni messaggio di un flood significa floodare insieme a lui.
"""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from .freni import freni

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


async def _passa_il_freno(update: Update) -> bool:
    """True se l'utente può procedere; altrimenti lo avvisa (una volta) e blocca."""
    utente = update.effective_user
    if utente is None:
        return False

    f = freni()
    attesa = f.attesa_comando(utente.id)
    if attesa > 0:
        log.info("Freno: utente %s oltre il limite di comandi (%.0fs)", utente.id, attesa)
        if update.message is not None and f.deve_avvisare(utente.id):
            await update.message.reply_text(
                "⏱ Stai andando troppo veloce. Riprendo ad ascoltarti tra poco."
            )
        return False

    f.segna_comando(utente.id)
    return True


def solo_freno(func: Handler) -> Handler:
    """Solo il limite di velocità, nessun controllo di whitelist."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _passa_il_freno(update):
            return
        await func(update, context)

    return wrapper


def guarded(func: Handler) -> Handler:
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        app_ctx = context.application.bot_data.get("ctx")
        user = update.effective_user
        if app_ctx is not None and app_ctx.settings.restricted:
            if user is None or user.id not in app_ctx.settings.allowed_user_ids:
                log.info(
                    "Comando ignorato da utente non autorizzato: %s (%s)",
                    getattr(user, "id", "?"), getattr(user, "username", "?"),
                )
                # Nessun dettaglio su come è configurato il bot: a chi non è
                # autorizzato non si spiega dove sta la porta.
                if update.message:
                    await update.message.reply_text("⛔ Questo bot non è aperto al pubblico.")
                return

        if not await _passa_il_freno(update):
            return
        await func(update, context)

    return wrapper
