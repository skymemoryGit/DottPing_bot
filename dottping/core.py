"""Comandi di base: /start, /help, /id, /status."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .guard import guarded, solo_freno
from .textfmt import esc
from .wait import clear_wait

log = logging.getLogger(__name__)

# Un elenco piatto, nell'ordine in cui uno li usa. Niente categorie, niente
# comandi di servizio: /id serve solo a chi configura il bot, non a chi lo usa
# (funziona ancora, semplicemente non si annuncia).
HELP = """🩺 <b>DottPing</b> — comandi

/medico — posti liberi di un medico, subito
/medico_on — sorveglia un medico e avvisami quando cambia
/medico_lista — chi sto sorvegliando, con l'ultimo stato letto
/medico_off — togli un medico dalla sorveglianza
/medico_check — controlla adesso
/status — stato del bot e dei controlli
/supporta — offri un caffè a DottPing ☕"""


@guarded
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_wait(context)
    # I comandi restano testo semplice, non <code>: così Telegram li rende
    # toccabili e chi arriva parte con un dito, non copiandoli a mano.
    await update.message.reply_text(
        "👋 Sono <b>DottPing</b>.\n\n"
        "Cerchi un medico di base in Veneto?\n"
        "Controllo per te quando il medico desiderato ha un posto libero e ti avviso, "
        "così puoi fare domanda di cambio.\n\n"
        "/medico → cerca un medico\n"
        "/medico_on → attiva l'avviso\n"
        "/help → tutto il resto",
        parse_mode=ParseMode.HTML,
    )


@guarded
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_wait(context)
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True)


@solo_freno
async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Volutamente fuori dalla whitelist (serve a scoprire il proprio id per
    ALLOWED_USER_IDS), ma non fuori dal freno anti-flood."""
    user = update.effective_user
    chat = update.effective_chat
    await update.message.reply_text(
        f"👤 Il tuo user id: <code>{user.id}</code>\n"
        f"💬 Id di questa chat: <code>{chat.id}</code>\n"
        f"📋 Tipo chat: {esc(chat.type)}",
        parse_mode=ParseMode.HTML,
    )


@guarded
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_wait(context)
    app_ctx = context.application.bot_data["ctx"]
    s = app_ctx.settings

    miei = await app_ctx.storage.medico_watch_list(update.effective_chat.id)
    tutti = await app_ctx.storage.medico_watch_list()
    elenco = ", ".join(r["nome_medico"] or r["cognome"] for r in miei) or "nessuno"
    orari = ", ".join(f"{h:02d}:05" for h in s.medico_ore) or "nessuno"

    jobs = context.application.job_queue.jobs() if context.application.job_queue else ()
    prossimo = min(
        (j.next_t for j in jobs if j.name and j.name.startswith("check_") and j.next_t),
        default=None,
    )

    # "campo" è QUALE numero guardiamo sulla scheda (assistiti illimitati o a
    # termine): scritto per esteso, così non si legge come un conteggio.
    campi = {
        "illimitati": "assistiti illimitati",
        "termine": "assistiti a termine",
        "entrambi": "assistiti illimitati e a termine",
    }
    campo = campi.get(s.medico_campo, s.medico_campo)

    await update.message.reply_text(
        "⚙️ <b>Stato di DottPing</b>\n\n"
        f"🩺 <b>Questa chat</b>\n"
        f"• Sorvegliati: {esc(elenco)} ({len(miei)} su {s.max_sorvegliati} posti)\n"
        f"• Dettaglio: /medico_lista\n\n"
        f"🕐 <b>Controlli</b>\n"
        f"• Orari: {esc(orari)} ({esc(s.timezone)})\n"
        f"• Prossimo: {prossimo.strftime('%d/%m/%Y %H:%M') if prossimo else 'non pianificato'}\n"
        f"• Guardo i posti: {esc(campo)}\n"
        f"• Medici seguiti in totale: {len({r['id_luogo'] for r in tutti})} "
        f"({len(tutti)} righe su {len({r['chat_id'] for r in tutti})} chat)",
        parse_mode=ParseMode.HTML,
    )


def register(app: Application, app_ctx) -> None:
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("status", status_command))
