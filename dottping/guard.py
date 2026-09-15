"""Chi può parlare al bot e quanto in fretta.

Due filtri, indipendenti:

  · `guarded`     — whitelist (se ALLOWED_USER_IDS è valorizzato) + freno anti-flood
  · `solo_freno`  — solo il freno, per i comandi che devono restare aperti a tutti

Il freno serve perché il bot è pubblico: senza, un utente che tiene premuto
invio occupa il processo e, comando dopo comando, fa partire richieste verso il
portale della Regione dall'IP di questo server.

Chi supera un limite non si prende sempre la stessa pausa: la prima volta è un
minuto, poi cinque, un quarto d'ora, un'ora, sei ore, un giorno (vedi
`freni.SCALA_PAUSE`). Una pausa fissa sarebbe inutile contro uno script, che
aspetterebbe il minuto e ricomincerebbe all'infinito. Chi invece ha solo
cliccato troppo in fretta una volta torna a zero dopo sei ore tranquille.

Durante la pausa il bot tace: rispondere a ogni messaggio di chi martella
significa martellare insieme a lui.
"""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from .freni import freni
from .textfmt import durata_leggibile

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


async def registra_infrazione(context: ContextTypes.DEFAULT_TYPE, utente: int) -> float:
    """Fa scattare (o allungare) la pausa e la salva. Ritorna i secondi."""
    durata = freni().infrazione(utente)
    app_ctx = context.application.bot_data.get("ctx")
    if app_ctx is not None:
        try:
            # Su disco, altrimenti basterebbe un riavvio del bot per
            # cancellare una pausa da ventiquattr'ore.
            await app_ctx.storage.kv_set(f"ban:{utente}", freni().sanzioni.stato(utente))
        except Exception as exc:  # noqa: BLE001 - la pausa vale comunque, in memoria
            log.warning("Pausa di %s non salvata: %s", utente, exc)
    log.warning("Pausa anti-abuso per %s: %s", utente, durata_leggibile(durata))
    return durata


async def _passa_il_freno(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True se l'utente può procedere; altrimenti lo ferma."""
    utente = update.effective_user
    if utente is None:
        return False

    f = freni()

    # Già in pausa: silenzio totale finché non scade.
    if f.residuo(utente.id) > 0:
        return False

    if f.attesa_comando(utente.id) > 0:
        durata = await registra_infrazione(context, utente.id)
        if update.message is not None:
            await update.message.reply_text(
                f"⏱ Troppi messaggi di fila. Riprendo ad ascoltarti tra "
                f"{durata_leggibile(durata)}.\n"
                "Se insisti la pausa si allunga."
            )
        return False

    f.segna_comando(utente.id)
    return True


def solo_freno(func: Handler) -> Handler:
    """Solo il limite di velocità, nessun controllo di whitelist."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _passa_il_freno(update, context):
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

        if not await _passa_il_freno(update, context):
            return
        await func(update, context)

    return wrapper
