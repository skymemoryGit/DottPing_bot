"""Comandi del bot.

    /medico         controllo a richiesta, non tocca la sorveglianza
    /medico_on      aggiunge il medico alla lista di QUESTA chat
    /medico_off     elenco con i bottoni per togliere
    /medico_lista   chi sto sorvegliando in questa chat
    /medico_check   forza subito il controllo di tutti

Nessun comando pretende un parametro: se il cognome serve e non c'è, il bot lo
chiede e legge la risposta libera (vedi `dottping/wait.py` e `risposta_libera`).
Scriverlo comunque sulla stessa riga continua a funzionare, per chi lo preferisce.

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
    MessageHandler, filters,
)

from ..freni import freni
from ..net import FetchError
from ..textfmt import esc
from ..guard import guarded
from ..wait import clear_wait, pop_wait, set_wait
from .jobs import (
    LEGENDA, aggiorna_stato, controlla_tutti, formatta_scheda, job_periodico,
)
from .source import (
    Medico, MedicoNonTrovato, NomeNonValido, TroppeRichieste,
    cerca_disponibilita, controlla_id_luogo, filtra_candidati, normalizza_nome,
)

log = logging.getLogger(__name__)

# Un messaggio solo per tutti i guasti del portale: all'utente non serve sapere
# quale URL ha risposto cosa, e a un curioso non si regala la mappa di casa.
PORTALE_KO = ("❌ Il portale della Regione non risponde in questo momento.\n"
              "<i>Riprova tra qualche minuto: i controlli automatici continuano lo stesso.</i>")


async def _freno_ricerca(update: Update) -> bool:
    """True se questo utente può far partire un'altra interrogazione del portale."""
    utente = update.effective_user
    if utente is None:
        return False
    f = freni()
    attesa = f.attesa_ricerca(utente.id)
    if attesa > 0:
        log.info("Freno ricerche: utente %s deve aspettare %.0fs", utente.id, attesa)
        await update.message.reply_text(
            f"⏱ Hai fatto parecchie ricerche di fila. Riprova tra {int(attesa) + 1} secondi.\n"
            "Il portale è un servizio pubblico: meglio non pestarlo."
        )
        return False
    f.segna_ricerca(utente.id)
    return True


def _freno_bottone(query) -> bool:
    """Come sopra, ma per i bottoni: un tocco che interroga il portale conta
    come una ricerca (il messaggio di rifiuto lo scrive il chiamante)."""
    utente = query.from_user
    if utente is None:
        return True
    f = freni()
    if f.attesa_ricerca(utente.id) > 0:
        log.info("Freno bottoni: utente %s oltre il limite", utente.id)
        return False
    f.segna_ricerca(utente.id)
    return True


def _bottoni_medici(medici: list[Medico], cognome: str,
                    azione: str) -> InlineKeyboardMarkup:
    """Un bottone per medico. `azione` dice cosa succede al tocco:
    `see` apre la scheda, `add` lo mette sotto sorveglianza.

    Nel callback stanno idLuogo e cognome: servono entrambi, perché ogni
    controllo rifà la ricerca dal cognome. Il cognome si taglia a 20 caratteri:
    il callback_data di Telegram sta in 64 byte e le lettere accentate ne
    pesano due.
    """
    righe = [
        [InlineKeyboardButton(
            f"{m.nome} — {m.indirizzo[:30]}" if m.indirizzo else m.nome,
            callback_data=f"med:{azione}:{m.id_luogo}:{cognome[:20]}",
        )]
        for m in medici[:8]
    ]
    righe.append([InlineKeyboardButton("✖️ Annulla", callback_data="med:no")])
    return InlineKeyboardMarkup(righe)


def _scheda_e_bottoni(d, candidati: list[Medico], cognome: str,
                      campo: str) -> tuple[str, InlineKeyboardMarkup]:
    """La scheda di un medico, con sotto cosa si può fare."""
    righe = [[InlineKeyboardButton(
        "🔔 Avvisami quando si libera un posto",
        callback_data=f"med:add:{d.id_luogo}:{cognome[:20]}",
    )]]
    if len(candidati) > 1:
        # Gli omonimi non si elencano nel testo: ci si torna con un bottone.
        righe.append([InlineKeyboardButton(
            f"↩️ Gli altri con questo cognome ({len(candidati) - 1})",
            callback_data=f"med:list:{cognome[:20]}",
        )])
    return formatta_scheda(d, campo), InlineKeyboardMarkup(righe)


def _domanda_scelta(candidati: list[Medico], cognome: str) -> str:
    return (f"Ho trovato {len(candidati)} medici con il cognome "
            f"«{esc(cognome)}». Quale vuoi vedere?")


def _bottone_aggiungi() -> InlineKeyboardMarkup:
    """Scorciatoia per iscriversi senza dover scrivere un comando con parametro."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "➕ Sorveglia un medico", callback_data="med:ask",
    )]])


async def _aggiungi(app_ctx, chat_id: int, cognome: str, medico: Medico) -> str:
    """Registra il medico e restituisce il messaggio da mostrare."""
    massimo = app_ctx.settings.max_sorvegliati
    gia = await app_ctx.storage.medico_watch_list(chat_id)
    if len(gia) >= massimo and medico.id_luogo not in {r["id_luogo"] for r in gia}:
        return (f"⚠️ Sorvegli già {massimo} medici in questa chat. "
                f"Togline uno con /medico_off.")

    # Tetto globale: ogni medico distinto è una richiesta al portale a ogni
    # giro di controlli. Senza un limite, abbastanza chat trasformerebbero il
    # bot in uno scraper — e il portale bloccherebbe questo IP, giustamente.
    tutti = await app_ctx.storage.medico_watch_list()
    distinti = {r["id_luogo"] for r in tutti}
    if medico.id_luogo not in distinti and len(distinti) >= app_ctx.settings.max_medici_totali:
        log.warning("Tetto globale raggiunto: %d medici distinti sorvegliati.", len(distinti))
        return ("⚠️ Sto già sorvegliando il massimo di medici che questo bot può seguire.\n"
                "Riprova più tardi: si libera quando qualcuno toglie i suoi.")

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


async def _cerca_e_mostra(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          cognome: str, nome: str = "") -> None:
    """Controllo a richiesta: mostra la scheda e offre il bottone per iscriversi."""
    app_ctx = context.application.bot_data["ctx"]
    s = app_ctx.settings

    if not await _freno_ricerca(update):
        return

    msg = await update.message.reply_text("⏳ Interrogo il portale della Regione Veneto…")
    try:
        medici, d = await cerca_disponibilita(cognome, nome or None)
    except NomeNonValido as exc:
        await msg.edit_text(f"✋ {esc(exc)}")
        return
    except TroppeRichieste as exc:
        await msg.edit_text(f"⏱ Troppe richieste in corso. Riprova tra {int(exc.secondi) + 1} secondi.")
        return
    except MedicoNonTrovato as exc:
        await msg.edit_text(f"🔍 {esc(exc)}", parse_mode=ParseMode.HTML)
        return
    except FetchError as exc:
        log.warning("Ricerca fallita per '%s': %s", cognome, exc)
        await msg.edit_text(PORTALE_KO, parse_mode=ParseMode.HTML)
        return

    candidati = filtra_candidati(medici, cognome, nome or None)

    if len(candidati) > 1:
        # Più omonimi: sceglie l'utente, non il bot. La scheda che abbiamo già
        # letto la teniamo da parte con la stessa chiave che userà il bottone,
        # così se sceglie proprio quel medico non si ripassa dal portale.
        freni().cache.set((normalizza_nome(cognome).casefold(), "", d.id_luogo), (medici, d))
        await msg.edit_text(
            _domanda_scelta(candidati, cognome),
            parse_mode=ParseMode.HTML,
            reply_markup=_bottoni_medici(candidati, cognome, "see"),
        )
        return

    testo, tastiera = _scheda_e_bottoni(d, candidati, cognome, s.medico_campo)
    await msg.edit_text(testo, parse_mode=ParseMode.HTML, reply_markup=tastiera)


@guarded
async def medico_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Controllo a richiesta. Non iscrive niente."""
    clear_wait(context)
    app_ctx = context.application.bot_data["ctx"]
    s = app_ctx.settings

    cognome = (context.args[0] if context.args else s.medico_cognome).strip()
    nome = (context.args[1] if len(context.args) > 1 else
            (s.medico_nome if not context.args else "")).strip()

    if not cognome:
        set_wait(context, "medico")
        await update.message.reply_text(
            "🩺 Dimmi il cognome del medico che vuoi controllare.\n"
            "<i>Rispondi qui sotto, basta il cognome.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    await _cerca_e_mostra(update, context, cognome, nome)


async def _sorveglia(update: Update, context: ContextTypes.DEFAULT_TYPE, cognome: str) -> None:
    """Cerca il medico indicato e lo mette sotto sorveglianza in questa chat."""
    app_ctx = context.application.bot_data["ctx"]

    if not await _freno_ricerca(update):
        return

    msg = await update.message.reply_text("⏳ Cerco il medico…")
    try:
        medici, _ = await cerca_disponibilita(cognome)
    except NomeNonValido as exc:
        await msg.edit_text(f"✋ {esc(exc)}")
        return
    except TroppeRichieste as exc:
        await msg.edit_text(f"⏱ Troppe richieste in corso. Riprova tra {int(exc.secondi) + 1} secondi.")
        return
    except MedicoNonTrovato as exc:
        await msg.edit_text(f"🔍 {esc(exc)}", parse_mode=ParseMode.HTML)
        return
    except FetchError as exc:
        log.warning("Sorveglianza fallita per '%s': %s", cognome, exc)
        await msg.edit_text(PORTALE_KO, parse_mode=ParseMode.HTML)
        return

    candidati = filtra_candidati(medici, cognome)
    if len(candidati) > 1:
        await msg.edit_text(
            f"Ho trovato {len(candidati)} medici con il cognome «{esc(cognome)}».\n"
            "Quale vuoi sorvegliare?",
            parse_mode=ParseMode.HTML,
            reply_markup=_bottoni_medici(candidati, cognome, "add"),
        )
        return

    testo = await _aggiungi(app_ctx, update.effective_chat.id, cognome, candidati[0])
    ore = ", ".join(f"{h:02d}:05" for h in app_ctx.settings.medico_ore)
    await msg.edit_text(
        f"{testo}\n\n🕐 Controlli alle {esc(ore)} ({esc(app_ctx.settings.timezone)}). "
        f"Ti scrivo solo quando lo stato cambia.",
        parse_mode=ParseMode.HTML,
    )


@guarded
async def medico_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Aggiunge un medico alla sorveglianza di questa chat."""
    clear_wait(context)
    cognome = " ".join(context.args).strip() if context.args else ""

    if not cognome:
        set_wait(context, "medico_on")
        await update.message.reply_text(
            "🔔 Dimmi il nome del medico da sorvegliare.\n"
            "<i>Rispondi qui sotto; puoi seguirne più di uno.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    await _sorveglia(update, context, cognome)


@guarded
async def medico_lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_wait(context)
    app_ctx = context.application.bot_data["ctx"]
    righe = await app_ctx.storage.medico_watch_list(update.effective_chat.id)
    if not righe:
        await update.message.reply_text(
            "In questa chat non stai sorvegliando nessun medico.",
            reply_markup=_bottone_aggiungi(),
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
    clear_wait(context)
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
    """Controllo forzato dei medici DI QUESTA CHAT.

    Prima controllava quelli di tutte le chat: bastava premerlo in sequenza per
    far partire decine di richieste al portale a spese dell'IP del server.
    """
    clear_wait(context)
    app_ctx = context.application.bot_data["ctx"]
    chat_id = update.effective_chat.id
    righe = await app_ctx.storage.medico_watch_list(chat_id)
    if not righe:
        await update.message.reply_text(
            "Non c'è niente da controllare: non stai sorvegliando nessun medico.",
            reply_markup=_bottone_aggiungi(),
        )
        return

    if not await _freno_ricerca(update):
        return

    msg = await update.message.reply_text("⏳ Controllo in corso…")
    controllati, errori = await controlla_tutti(
        app_ctx, notifica=True, bot=context.bot, solo_chat=chat_id, sorgente="utente",
    )

    testo = f"✅ Controllati {controllati} medici."
    if errori:
        # Solo i nomi: il perché sta nel log, non in chat.
        testo += "\n\n⚠️ Non sono riuscito a leggere: " + esc(", ".join(errori[:5]))
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

    # Il callback_data lo compone il bot e Telegram lo restituisce intatto, ma
    # ci passa comunque roba scritta da noi a partire da testo dell'utente:
    # si valida prima di rimandarla al portale. maxsplit=3 lascia intero il
    # cognome anche se contenesse un ':'.
    dati = (query.data or "").split(":", 3)
    chat_id = query.message.chat_id

    if len(dati) >= 2 and dati[1] == "no":
        await query.edit_message_text("Annullato.")
        return

    if len(dati) >= 2 and dati[1] == "ask":
        set_wait(context, "medico_on")
        await query.edit_message_text(
            "🔔 Dimmi il nome del medico da sorvegliare.\n"
            "<i>Rispondi qui sotto; puoi seguirne più di uno.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    if len(dati) >= 4 and dati[1] == "see":
        # Scelta fatta dall'elenco degli omonimi: apre la scheda di quello.
        id_luogo, cognome = dati[2], dati[3]
        if not _freno_bottone(query):
            await query.edit_message_text("⏱ Troppe richieste di fila. Riprova tra poco.")
            return

        await query.edit_message_text("⏳ Apro la scheda…")
        try:
            medici, d = await cerca_disponibilita(cognome, None, id_luogo)
        except (NomeNonValido, TroppeRichieste) as exc:
            await query.edit_message_text(f"✋ {esc(exc)}")
            return
        except MedicoNonTrovato as exc:
            await query.edit_message_text(f"🔍 {esc(exc)}", parse_mode=ParseMode.HTML)
            return
        except FetchError as exc:
            log.warning("Apertura scheda fallita (%s): %s", id_luogo, exc)
            await query.edit_message_text(PORTALE_KO, parse_mode=ParseMode.HTML)
            return

        candidati = filtra_candidati(medici, cognome)
        testo, tastiera = _scheda_e_bottoni(d, candidati, cognome, s.medico_campo)
        await query.edit_message_text(testo, parse_mode=ParseMode.HTML, reply_markup=tastiera)
        return

    if len(dati) >= 3 and dati[1] == "list":
        # "Gli altri con questo cognome": si torna all'elenco.
        cognome = dati[2]
        if not _freno_bottone(query):
            await query.edit_message_text("⏱ Troppe richieste di fila. Riprova tra poco.")
            return

        await query.edit_message_text("⏳ Cerco gli altri…")
        try:
            medici, d = await cerca_disponibilita(cognome)
        except (NomeNonValido, TroppeRichieste) as exc:
            await query.edit_message_text(f"✋ {esc(exc)}")
            return
        except MedicoNonTrovato as exc:
            await query.edit_message_text(f"🔍 {esc(exc)}", parse_mode=ParseMode.HTML)
            return
        except FetchError as exc:
            log.warning("Elenco omonimi fallito per '%s': %s", cognome, exc)
            await query.edit_message_text(PORTALE_KO, parse_mode=ParseMode.HTML)
            return

        candidati = filtra_candidati(medici, cognome)
        if len(candidati) <= 1:
            testo, tastiera = _scheda_e_bottoni(d, candidati, cognome, s.medico_campo)
            await query.edit_message_text(testo, parse_mode=ParseMode.HTML, reply_markup=tastiera)
            return
        await query.edit_message_text(
            _domanda_scelta(candidati, cognome),
            parse_mode=ParseMode.HTML,
            reply_markup=_bottoni_medici(candidati, cognome, "see"),
        )
        return

    if len(dati) >= 4 and dati[1] == "add":
        id_luogo, cognome = dati[2], dati[3]
        if not _freno_bottone(query):
            await query.edit_message_text("⏱ Troppe richieste di fila. Riprova tra poco.")
            return

        await query.edit_message_text("⏳ Aggiungo…")
        try:
            medici, _ = await cerca_disponibilita(cognome, None, id_luogo)
        except (NomeNonValido, TroppeRichieste) as exc:
            await query.edit_message_text(f"✋ {esc(exc)}")
            return
        except MedicoNonTrovato as exc:
            await query.edit_message_text(f"🔍 {esc(exc)}", parse_mode=ParseMode.HTML)
            return
        except FetchError as exc:
            log.warning("Iscrizione da bottone fallita (%s): %s", id_luogo, exc)
            await query.edit_message_text(PORTALE_KO, parse_mode=ParseMode.HTML)
            return
        scelto = next((m for m in medici if m.id_luogo == id_luogo), None)
        if scelto is None:
            await query.edit_message_text("❌ Quel medico non è più nell'elenco del portale.")
            return
        testo = await _aggiungi(app_ctx, chat_id, cognome, scelto)
        await query.edit_message_text(testo, parse_mode=ParseMode.HTML)
        return

    if len(dati) >= 3 and dati[1] == "del":
        try:
            controlla_id_luogo(dati[2])
        except NomeNonValido:
            await query.edit_message_text("Bottone non più valido.")
            return
        righe = await app_ctx.storage.medico_watch_list(chat_id)
        nome = next((r["nome_medico"] or r["cognome"]
                     for r in righe if r["id_luogo"] == dati[2]), dati[2])
        rimosso = await app_ctx.storage.medico_watch_remove(chat_id, dati[2])
        await query.edit_message_text(
            f"🔕 Tolto dalla sorveglianza: <b>{esc(nome)}</b>" if rimosso
            else "ℹ️ Non era più sorvegliato.",
            parse_mode=ParseMode.HTML,
        )


@guarded
async def risposta_libera(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Legge un messaggio normale come risposta alla domanda appena fatta dal bot.

    Serve a evitare la sintassi "/comando parametro": il bot chiede il nome del
    medico e l'utente risponde come parlerebbe a una persona. Se non era stata
    fatta nessuna domanda il messaggio viene ignorato, così il bot non
    interviene nelle chiacchiere di un gruppo.
    """
    azione = pop_wait(context)
    if azione is None:
        return

    testo = (update.message.text or "").strip()
    if not testo:
        return

    if azione == "medico":
        parole = testo.split()
        await _cerca_e_mostra(update, context, parole[0], " ".join(parole[1:]))
    elif azione == "medico_on":
        await _sorveglia(update, context, testo)


def register(app: Application, app_ctx) -> None:
    app.add_handler(CommandHandler("medico", medico_command))
    app.add_handler(CommandHandler("disponibilita", medico_command))
    app.add_handler(CommandHandler("medico_on", medico_on))
    app.add_handler(CommandHandler("medico_off", medico_off))
    app.add_handler(CommandHandler("medico_lista", medico_lista))
    app.add_handler(CommandHandler("medico_check", medico_check))
    app.add_handler(CallbackQueryHandler(bottone, pattern=r"^med:"))
    # Ultimo: intercetta solo i messaggi normali, e solo se il bot ha appena
    # fatto una domanda (vedi risposta_libera).
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, risposta_libera))

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
