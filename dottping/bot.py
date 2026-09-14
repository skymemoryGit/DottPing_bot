"""Assemblaggio dell'applicazione: contesto condiviso + registrazione moduli.

Il bot fa una cosa sola, ma la struttura resta quella a moduli: aggiungere
domani i pediatri o un'altra regione significa creare un pacchetto con la sua
`register(app, ctx)` e aggiungerlo a MODULES, senza toccare nient'altro.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib import import_module
from typing import Sequence

from telegram import BotCommand
from telegram.ext import Application, ContextTypes

from .config import Settings
from .storage import Storage

log = logging.getLogger(__name__)

MODULES: Sequence[str] = (
    "dottping.core",
    "dottping.medici.handlers",
)

COMMANDS = [
    BotCommand("medico", "Posti liberi di un medico"),
    BotCommand("medico_on", "Sorveglia un medico e avvisami"),
    BotCommand("medico_lista", "Medici sorvegliati in questa chat"),
    BotCommand("medico_off", "Togli un medico dalla sorveglianza"),
    BotCommand("medico_check", "Controlla adesso"),
    BotCommand("status", "Stato del bot"),
    BotCommand("id", "Mostra il tuo id Telegram"),
    BotCommand("help", "Lista comandi"),
]


@dataclass
class BotContext:
    """Stato condiviso, raggiungibile dagli handler via application.bot_data['ctx']."""
    settings: Settings
    storage: Storage


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Errore non gestito", exc_info=context.error)


async def _post_init(app: Application) -> None:
    ctx: BotContext = app.bot_data["ctx"]
    await ctx.storage.init()
    try:
        await app.bot.set_my_commands(COMMANDS)
    except Exception as exc:  # noqa: BLE001 - il menu è cosmetico
        log.warning("Menu comandi non impostato: %s", exc)
    me = await app.bot.get_me()
    log.info("DottPing online come @%s", me.username)


def build_application(settings: Settings) -> Application:
    ctx = BotContext(settings=settings, storage=Storage(settings.db_path))

    app = Application.builder().token(settings.token).post_init(_post_init).build()
    app.bot_data["ctx"] = ctx

    for dotted in MODULES:
        module = import_module(dotted)
        module.register(app, ctx)
        log.info("Modulo registrato: %s", dotted)

    app.add_error_handler(_on_error)

    if settings.restricted:
        log.info("Accesso limitato a %d utenti.", len(settings.allowed_user_ids))
    else:
        log.info("Bot pubblico: ogni chat ha la propria lista di medici sorvegliati.")
    return app
