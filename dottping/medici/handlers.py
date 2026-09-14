"""Comandi del bot.

    /medico <cognome>       controllo a richiesta, non tocca la sorveglianza
    /medico_on <cognome>    aggiunge il medico alla lista di QUESTA chat
    /medico_off             elenco con i bottoni per togliere
    /medico_lista           chi sto sorvegliando in questa chat
    /medico_check           forza subito il controllo di tutti

Quali medici sorvegliare si decide dalla chat, non dal .env: la lista sta nella
tabella `medico_watch`, una riga per (chat, medico). Nel `.env` restano solo le
impostazioni globali (orari dei controlli, quale numero guardare) e un cognome
predefinito facoltativo per `/medico` scritto da solo.
"""
from __future__ import annotations

import datetime as dt
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
)

from ..net import FetchError
from ..textfmt import esc
from ..guard import guarded
from .jobs import (
    LEGENDA, aggiorna_stato, controlla_tutti, formatta_scheda, job_periodico,
)
from .source import Medico, MedicoNonTrovato, cerca_disponibilita

log = logging.getLogger(__name__)


def _bottoni_scelta(medici: list[Medico], cognome: str) -> InlineKeyboardMarkup:
    """Un bottone per medico. Nel callback stanno idLuogo e cognome: servono
    entrambi, perché ogni controllo rifà la ricerca dal cognome."""
    righe = [
        [InlineKeyboardButton(
            f"{m.nome} — {m.indirizzo[:30]}" if m.indirizzo else m.nome,
            callback_data=f"med:add:{m.id_luogo}:{cognome[:24]}",
        )]
        for m in medici[:8]
    ]
    righe.append([InlineKeyboardButton("✖️ Annulla", callback_data="med:no")])
    return InlineKeyboardMarkup(righe)


async def _aggiungi(app_ctx, chat_id: int, cognome: str, medico: Medico) -> str:
    """Registra il medico e restituisce il messaggio da mostrare."""
    massimo = app_ctx.settings.max_sorvegliati
    gia = await app_ctx.storage.medico_watch_list(chat_id)
    if len(gia) >= massimo and medico.id_luogo not in {r["id_luogo"] for r in gia}:
        return (f"⚠️ Sorvegli già {massimo} medici in questa chat. "
                f"Togline uno con /medico_off.")

    creata = await app_ctx.storage.medico_watch_add(
        chat_id, medico.id_luogo, cognome, "", medico.nome
    )
    if not creata:
        return f"ℹ️ <b>{esc(medico.nome)}</b> era già sorvegliato in questa chat."

    # Primo controllo subito: registra lo stato di partenza ed evita che la
    # prima esecuzione del job annunci uno zero che c'era già.
    try:
        _, d = await cerca_disponibilita(cognome, None, medico.id_luogo)
        await aggiorna_stato(app_ctx, d, app_ctx.settings.medico_campo)
        stato = formatta_scheda(d, app_ctx.settings.medico_campo)
    except (FetchError, MedicoNonTrovato):
        stato = f"🩺 <b>{esc(medico.nome)}</b>\n<i>Stato attuale non recuperato, riproverò al prossimo controllo.</i>"

    return f"✅ <b>Aggiunto alla sorveglianza.</b>\n\n{stato}"


@guarded
async def medico_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Controllo a richiesta. Non iscrive niente."""
    app_ctx = context.application.bot_data["ctx"]
    s = app_ctx.settings

    cognome = (context.args[0] if context.args else s.medico_cognome).strip()
    nome = (context.args[1] if len(context.args) > 1 else
            (s.medico_nome if not context.args else "")).strip()

    if not cognome:
        await update.message.reply_text(
            "Scrivi il cognome del medico: <code>/medico rossi</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    msg = await update.message.reply_text("⏳ Interrogo il portale della Regione Veneto…")
    try:
        medici, d = await cerca_disponibilita(cognome, nome or None)
    except MedicoNonTrovato as exc:
        await msg.edit_text(f"🔍 {esc(exc)}", parse_mode=ParseMode.HTML)
        return
    except FetchError as exc:
        await msg.edit_text(f"❌ Portale non raggiungibile.\n\n<code>{esc(exc)}</code>",
                            parse_mode=ParseMode.HTML)
        return

    testo = formatta_scheda(d, s.medico_campo)
    if len(medici) > 1:
        altri = ", ".join(m.nome for m in medici if m.id_luogo != d.id_luogo)
        testo += f"\n\n<i>Altri con questo cognome: {esc(altri)}</i>"
    testo += f"\n\n<i>Per essere avvisato quando si liberano posti: " \
             f"/medico_on {esc(cognome)}</i>"
    await msg.edit_text(testo, parse_mode=ParseMode.HTML)


@guarded
async def medico_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Aggiunge un medico alla sorveglianza di questa chat."""
    app_ctx = context.application.bot_data["ctx"]
    cognome = " ".join(context.args).strip() if context.args else ""

    if not cognome:
        await update.message.reply_text(
            "Dimmi chi sorvegliare: <code>/medico_on rossi</code>\n"
            "Puoi seguirne più di uno; /medico_lista mostra quelli attivi.",
            parse_mode=ParseMode.HTML,
        )
        return

    msg = await update.message.reply_text("⏳ Cerco il medico…")
    try:
        medici, _ = await cerca_disponibilita(cognome)
    except MedicoNonTrovato as exc:
        await msg.edit_text(f"🔍 {esc(exc)}", parse_mode=ParseMode.HTML)
        return
    except FetchError as exc:
        await msg.edit_text(f"❌ Portale non raggiungibile.\n\n<code>{esc(exc)}</code>",
                            parse_mode=ParseMode.HTML)
        return

    if len(medici) > 1:
        await msg.edit_text(
            f"Ho trovato {len(medici)} medici con il cognome «{esc(cognome)}».\n"
            "Quale vuoi sorvegliare?",
            parse_mode=ParseMode.HTML,
            reply_markup=_bottoni_scelta(medici, cognome),
        )
        return

    testo = await _aggiungi(app_ctx, update.effective_chat.id, cognome, medici[0])
    ore = ", ".join(f"{h:02d}:05" for h in app_ctx.settings.medico_ore)
    await msg.edit_text(
        f"{testo}\n\n🕐 Controlli alle {esc(ore)} ({esc(app_ctx.settings.timezone)}). "
        f"Ti scrivo solo quando lo stato cambia.",
        parse_mode=ParseMode.HTML,
    )


@guarded
async def medico_lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app_ctx = context.application.bot_data["ctx"]
    righe = await app_ctx.storage.medico_watch_list(update.effective_chat.id)
    if not righe:
        await update.message.reply_text(
            "Nessun medico sorvegliato in questa chat.\n"
            "Aggiungine uno con <code>/medico_on cognome</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = ["🩺 <b>Medici sorvegliati in questa chat</b>", ""]
    for r in righe:
        stato = await app_ctx.storage.kv_get(f"medico:stato:{r['id_luogo']}")
        if stato:
            icona = "🟢" if int(stato.get("valore", 0)) > 0 else "🔴"
            dettaglio = (f"illimitati {stato.get('illimitati')}, "
                         f"a termine {stato.get('a_termine')} "
                         f"— {stato.get('data') or 'n/d'}")
        else:
            icona, dettaglio = "▫️", "mai controllato"
        lines.append(f"{icona} <b>{esc(r['nome_medico'] or r['cognome'])}</b>")
        lines.append(f"    <i>{esc(dettaglio)}</i>")
    lines.append("")
    lines.append(LEGENDA)
    lines.append("")
    lines.append("<i>/medico_off per togliere · /medico_check per controllare adesso</i>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@guarded
async def medico_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Senza argomenti mostra i bottoni; con un cognome toglie direttamente."""
    app_ctx = context.application.bot_data["ctx"]
    chat_id = update.effective_chat.id
    righe = await app_ctx.storage.medico_watch_list(chat_id)

    if not righe:
        await update.message.reply_text("ℹ️ In questa chat non sorvegli nessun medico.")
        return

    if context.args:
        cercato = " ".join(context.args).strip().lower()
        colpiti = [r for r in righe
                   if cercato in (r["nome_medico"] or "").lower()
                   or cercato in r["cognome"].lower()]
        if not colpiti:
            await update.message.reply_text(f"🔍 Nessun medico sorvegliato corrisponde a «{cercato}».")
            return
        for r in colpiti:
            await app_ctx.storage.medico_watch_remove(chat_id, r["id_luogo"])
        nomi = ", ".join(r["nome_medico"] or r["cognome"] for r in colpiti)
        await update.message.reply_text(f"🔕 Tolto dalla sorveglianza: {esc(nomi)}",
                                        parse_mode=ParseMode.HTML)
        return

    bottoni = [[InlineKeyboardButton(
        f"🔕 {r['nome_medico'] or r['cognome']}",
        callback_data=f"med:del:{r['id_luogo']}",
    )] for r in righe]
    bottoni.append([InlineKeyboardButton("✖️ Annulla", callback_data="med:no")])
    await update.message.reply_text(
        "Quale medico tolgo dalla sorveglianza?",
        reply_markup=InlineKeyboardMarkup(bottoni),
    )


@guarded
async def medico_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app_ctx = context.application.bot_data["ctx"]
    righe = await app_ctx.storage.medico_watch_list(update.effective_chat.id)
    if not righe:
        await update.message.reply_text(
            "Nessun medico sorvegliato. Aggiungine uno con <code>/medico_on cognome</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    msg = await update.message.reply_text("⏳ Controllo in corso…")
    controllati, errori = await controlla_tutti(app_ctx, notifica=True, bot=context.bot)

    testo = f"✅ Controllati {controllati} medici."
    if errori:
        testo += "\n\n⚠️ Problemi:\n" + "\n".join(f"• {esc(e)}" for e in errori[:5])
    testo += "\n\n<i>Gli iscritti ricevono un messaggio solo se lo stato è cambiato. " \
             "Lo stato attuale è in /medico_lista.</i>"
    await msg.edit_text(testo, parse_mode=ParseMode.HTML)


async def bottone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce i bottoni di scelta e rimozione."""
    query = update.callback_query
    await query.answer()

    app_ctx = context.application.bot_data["ctx"]
    s = app_ctx.settings
    if s.restricted and (query.from_user is None or query.from_user.id not in s.allowed_user_ids):
        await query.edit_message_text("⛔ Non sei autorizzato.")
        return

    dati = (query.data or "").split(":")
    chat_id = query.message.chat_id

    if len(dati) >= 2 and dati[1] == "no":
        await query.edit_message_text("Annullato.")
        return

    if len(dati) >= 4 and dati[1] == "add":
        id_luogo, cognome = dati[2], dati[3]
        await query.edit_message_text("⏳ Aggiungo…")
        try:
            medici, _ = await cerca_disponibilita(cognome, None, id_luogo)
        except (FetchError, MedicoNonTrovato) as exc:
            await query.edit_message_text(f"❌ {esc(exc)}", parse_mode=ParseMode.HTML)
            return
        scelto = next((m for m in medici if m.id_luogo == id_luogo), None)
        if scelto is None:
            await query.edit_message_text("❌ Quel medico non è più nell'elenco del portale.")
            return
        testo = await _aggiungi(app_ctx, chat_id, cognome, scelto)
        await query.edit_message_text(testo, parse_mode=ParseMode.HTML)
        return

    if len(dati) >= 3 and dati[1] == "del":
        righe = await app_ctx.storage.medico_watch_list(chat_id)
        nome = next((r["nome_medico"] or r["cognome"]
                     for r in righe if r["id_luogo"] == dati[2]), dati[2])
        rimosso = await app_ctx.storage.medico_watch_remove(chat_id, dati[2])
        await query.edit_message_text(
            f"🔕 Tolto dalla sorveglianza: <b>{esc(nome)}</b>" if rimosso
            else "ℹ️ Non era più sorvegliato.",
            parse_mode=ParseMode.HTML,
        )


def register(app: Application, app_ctx) -> None:
    app.add_handler(CommandHandler("medico", medico_command))
    app.add_handler(CommandHandler("disponibilita", medico_command))
    app.add_handler(CommandHandler("medico_on", medico_on))
    app.add_handler(CommandHandler("medico_off", medico_off))
    app.add_handler(CommandHandler("medico_lista", medico_lista))
    app.add_handler(CommandHandler("medico_check", medico_check))
    app.add_handler(CallbackQueryHandler(bottone, pattern=r"^med:"))

    s = app_ctx.settings
    if app.job_queue is None:
        log.warning("JobQueue assente: sorveglianza medico disattivata.")
        return

    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(s.timezone)
    except Exception:  # noqa: BLE001
        tz = dt.timezone.utc

    for ora in s.medico_ore:
        app.job_queue.run_daily(
            job_periodico,
            time=dt.time(hour=ora, minute=5, tzinfo=tz),
            name=f"check_{ora:02d}",
        )
    if s.medico_ore:
        log.info("Controlli pianificati alle %s (%s)",
                 ", ".join(f"{h:02d}:05" for h in s.medico_ore), s.timezone)
