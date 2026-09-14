"""Controllo accessi: se ALLOWED_USER_IDS è valorizzato, gli altri vengono ignorati."""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


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
                if update.message:
                    await update.message.reply_text(
                        "⛔ Non sei autorizzato a usare questo bot.\n"
                        "Se sei il proprietario, aggiungi il tuo id a ALLOWED_USER_IDS nel .env "
                        "(scoprilo con /id)."
                    )
                return
        await func(update, context)

    return wrapper
