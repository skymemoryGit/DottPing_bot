"""Il cappello passato con garbo.

Il bot è gratis e deve restarlo: questo modulo aggiunge solo un comando
`/supporta` e un bottone, che compare in due posti soli — quando qualcuno lo
chiede, e sulla notifica «posti disponibili», cioè l'unico momento in cui il
bot ha davvero risolto un problema a chi lo usa. Mai in mezzo a una ricerca,
mai due volte di fila.

L'indirizzo sta nel `.env` (`SUPPORTO_URL`): se è vuoto o non è https, il
comando risponde che non c'è niente da offrire e il bottone non compare — così
chi riusa il codice non si ritrova a raccogliere offerte per qualcun altro.
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .guard import solo_freno
from .wait import clear_wait

log = logging.getLogger(__name__)

# Due righe: chi legge deve arrivare al bottone, non a fine poesia.
TESTO = (
    "☕ DottPing è gratis.\n"
    "Se ti è stato utile, offrigli un caffè ❤️"
)


def tastiera(settings) -> InlineKeyboardMarkup | None:
    """Il bottone verso la pagina delle offerte, o None se non è configurata."""
    url = getattr(settings, "supporto_url", "")
    if not url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("☕ Offri un caffè", url=url)]])


@solo_freno
async def supporta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_wait(context)
    settings = context.application.bot_data["ctx"].settings
    bottoni = tastiera(settings)
    if bottoni is None:
        await update.message.reply_text(
            "Non c'è niente da offrire: questo bot non ha una pagina di supporto. "
            "Usalo e basta 🙂"
        )
        return
    await update.message.reply_text(TESTO, parse_mode=ParseMode.HTML,
                                    reply_markup=bottoni, disable_web_page_preview=True)


def register(app: Application, app_ctx) -> None:
    app.add_handler(CommandHandler("supporta", supporta_command))
    app.add_handler(CommandHandler("dona", supporta_command))
