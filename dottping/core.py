"""Comandi di base: /start, /help, /id, /status."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .guard import guarded
from .textfmt import esc

log = logging.getLogger(__name__)

HELP = """🩺 <b>DottPing</b> — comandi disponibili

<b>Controllo al volo</b>
/medico &lt;cognome&gt; — posti liberi di un medico, subito
  esempio: <code>/medico rossi</code>

<b>Sorveglianza</b>
/medico_on &lt;cognome&gt; — sorveglia quel medico in questa chat
/medico_lista — chi sto sorvegliando, con l'ultimo stato letto
/medico_off — togli un medico dalla sorveglianza
/medico_check — forza subito il controllo di tutti

<b>Utilità</b>
/id — il tuo id Telegram e quello della chat
/status — stato del bot e dei controlli
/help — questo messaggio

<i>Fonte dei dati: portale della Regione Veneto,
"Trova Medici di Medicina Generale e Pediatri di Libera Scelta".</i>"""


@guarded
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Sono <b>DottPing</b>.\n\n"
        "Controllo i posti liberi dai medici di base della Regione Veneto e ti avviso "
        "quando se ne libera uno, così puoi fare domanda di cambio.\n\n"
        "Prova: <code>/medico rossi</code>\n"
        "Poi /help per il resto.",
        parse_mode=ParseMode.HTML,
    )


@guarded
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True)


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Volutamente NON protetto: serve a scoprire l'id da mettere in ALLOWED_USER_IDS."""
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

    await update.message.reply_text(
        "⚙️ <b>Stato di DottPing</b>\n\n"
        f"🩺 <b>Questa chat</b>\n"
        f"• Sorvegliati: {esc(elenco)}\n"
        f"• Dettaglio: /medico_lista\n\n"
        f"🕐 <b>Controlli</b>\n"
        f"• Orari: {esc(orari)} ({esc(s.timezone)})\n"
        f"• Prossimo: {prossimo.strftime('%d/%m/%Y %H:%M') if prossimo else 'non pianificato'}\n"
        f"• Numero sorvegliato: {esc(s.medico_campo)}\n"
        f"• Medici seguiti in totale: {len({r['id_luogo'] for r in tutti})} "
        f"({len(tutti)} righe su {len({r['chat_id'] for r in tutti})} chat)\n\n"
        f"🔓 <b>Accesso</b>: "
        f"{'limitato a ' + str(len(s.allowed_user_ids)) + ' utenti' if s.restricted else 'aperto a tutti'}",
        parse_mode=ParseMode.HTML,
    )


def register(app: Application, app_ctx) -> None:
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("status", status_command))
