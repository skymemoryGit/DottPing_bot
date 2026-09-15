"""Controllo periodico dei medici sorvegliati e notifica ai cambi di stato.

Due regole guidano questo file:

1. **Si notifica quando lo stato cambia, non a ogni controllo.** Un medico può
   restare a 0 posti per settimane: interessa il momento della transizione.
   Lo stato precedente vive nella tabella `kv`.
2. **Una richiesta per medico, non per chat.** Se tre chat sorvegliano la stessa
   dottoressa, il portale viene interrogato una volta sola e il messaggio parte
   verso le tre chat.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict

from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from ..net import FetchError
from ..supporto import tastiera as bottone_supporto
from ..textfmt import esc
from .source import Disponibilita, MedicoNonTrovato, cerca_disponibilita

log = logging.getLogger(__name__)


LEGENDA = (
    "ℹ️ <i><b>Illimitati</b>: posti per l'iscrizione normale, senza scadenza. "
    "È il numero che conta: se è diverso da 0 puoi fare domanda di cambio medico.\n"
    "<b>A termine</b>: posti temporanei (domicilio provvisorio, studenti fuori sede), "
    "hanno una data di scadenza.</i>"
)


def _chiave(id_luogo: str) -> str:
    return f"medico:stato:{id_luogo}"


def _valore_osservato(d: Disponibilita, campo: str) -> int:
    if campo == "termine":
        return d.a_termine or 0
    if campo == "entrambi":
        return (d.illimitati or 0) + (d.a_termine or 0)
    return d.illimitati or 0


def posti_liberi(d: Disponibilita, campo: str = "illimitati") -> bool:
    """Se c'è qualcosa da prendere adesso, sul numero che stiamo sorvegliando."""
    return _valore_osservato(d, campo) > 0


def formatta_scheda(d: Disponibilita, campo: str = "illimitati") -> str:
    """Il messaggio di /medico: stato completo, senza enfasi.

    `None` = riga da saltare (dato assente), `""` = riga vuota voluta.
    """
    icona = "🟢" if _valore_osservato(d, campo) > 0 else "🔴"
    righe: list[str | None] = [
        f"🩺 <b>{esc(d.nome)}</b>",
        f"<i>{esc(d.tipo)}</i>" if d.tipo else None,
        "",
        f"{icona} <b>Assistiti illimitati: {d.illimitati if d.illimitati is not None else 'n/d'}</b>",
        f"▫️ Assistiti a termine: {d.a_termine if d.a_termine is not None else 'n/d'}",
        f"🗓 Rilevazione del {esc(d.data_rilevazione)}" if d.data_rilevazione else None,
        "",
        # Se il posto c'è già, la cosa utile da dire non è "ti avviso": è "vai".
        "👉 <b>Puoi presentare domanda di cambio medico adesso.</b>"
        if posti_liberi(d, campo) else None,
        "",
        f"📍 {esc(d.indirizzo)}" if d.indirizzo else None,
        f"☎️ {esc(d.telefono)}" if d.telefono else None,
        "",
        LEGENDA,
    ]
    testo = "\n".join(r for r in righe if r is not None)
    # se mancano indirizzo o telefono restano due righe vuote di fila: si collassano
    return re.sub(r"\n{3,}", "\n\n", testo)


def formatta_notifica(d: Disponibilita, campo: str, prima: int, adesso: int) -> str:
    if adesso > 0:
        testa = [
            "🟢 <b>POSTI DISPONIBILI</b>",
            "",
            f"🩺 <b>{esc(d.nome)}</b>",
            f"Assistiti illimitati: <b>{d.illimitati}</b>"
            + (f" (prima erano {prima})" if prima != adesso else ""),
            f"Assistiti a termine: {d.a_termine}",
        ]
        coda = ["", "👉 Puoi presentare la domanda di cambio medico.", "", LEGENDA]
    else:
        testa = [
            "🔴 <b>Posti esauriti</b>",
            "",
            f"🩺 <b>{esc(d.nome)}</b>",
            f"Assistiti illimitati tornati a <b>0</b> (erano {prima}).",
        ]
        coda = ["", "<i>Continuo a controllare.</i>"]

    if d.data_rilevazione:
        testa.append(f"🗓 Rilevazione del {esc(d.data_rilevazione)}")
    return "\n".join(testa + coda)


async def aggiorna_stato(app_ctx, d: Disponibilita, campo: str) -> tuple[bool, int]:
    """Salva lo stato e dice se è cambiato rispetto al controllo precedente.

    Ritorna (cambiato, valore_precedente). Al primo controllo in assoluto
    considera "cambiato" solo se i posti ci sono già: non ha senso annunciare
    uno zero che c'era anche prima.
    """
    adesso = _valore_osservato(d, campo)
    precedente = await app_ctx.storage.kv_get(_chiave(d.id_luogo))
    prima = None if precedente is None else int(precedente.get("valore", 0))

    await app_ctx.storage.kv_set(_chiave(d.id_luogo), {
        "valore": adesso,
        "illimitati": d.illimitati,
        "a_termine": d.a_termine,
        "data": d.data_rilevazione,
        "nome": d.nome,
    })

    cambiato = (prima is None and adesso > 0) or (
        prima is not None and (prima > 0) != (adesso > 0)
    )
    return cambiato, prima or 0


async def _avvisa(app_ctx, bot, chat_ids: list[int], testo: str, tastiera=None) -> None:
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=testo, parse_mode=ParseMode.HTML,
                                   reply_markup=tastiera)
        except Forbidden:
            log.info("Chat %s ha bloccato il bot: tolgo i suoi medici sorvegliati.", chat_id)
            for riga in await app_ctx.storage.medico_watch_list(chat_id):
                await app_ctx.storage.medico_watch_remove(chat_id, riga["id_luogo"])
        except TelegramError as exc:
            log.warning("Invio a %s fallito: %s", chat_id, exc)


async def controlla_tutti(app_ctx, *, notifica: bool, bot=None,
                          solo_chat: int | None = None,
                          sorgente: str = "job") -> tuple[int, list[str]]:
    """Controlla i medici sorvegliati. Ritorna (quanti controllati, nomi in errore).

    `solo_chat` limita il giro ai medici di una chat: è quello che usa
    /medico_check, così un utente non può far ricontrollare a comando la lista
    di tutti gli altri. `sorgente` distingue il giro automatico (nostro, non
    passa dal tetto globale) da quello chiesto a mano.
    """
    righe = await app_ctx.storage.medico_watch_list(solo_chat)
    if not righe:
        return 0, []

    # Stesso medico seguito da più chat = una sola interrogazione al portale.
    gruppi: dict[str, list[dict]] = defaultdict(list)
    for r in righe:
        gruppi[r["id_luogo"]].append(r)

    campo = app_ctx.settings.medico_campo
    errori: list[str] = []
    controllati = 0

    for id_luogo, gruppo in gruppi.items():
        riferimento = gruppo[0]
        try:
            _, d = await cerca_disponibilita(
                riferimento["cognome"], riferimento.get("nome") or None, id_luogo,
                sorgente=sorgente,
            )
        except Exception as exc:  # noqa: BLE001 - un medico che salta non ferma gli altri
            nome = riferimento.get("nome_medico") or riferimento["cognome"]
            log.warning("Controllo di %s fallito: %s", nome, exc)
            errori.append(str(nome))   # il dettaglio resta nel log
            continue

        controllati += 1
        cambiato, prima = await aggiorna_stato(app_ctx, d, campo)
        log.info(
            "Medico %s: illimitati=%s a_termine=%s (prima=%s, cambio=%s)",
            d.nome, d.illimitati, d.a_termine, prima, cambiato,
        )

        if cambiato and notifica and bot is not None:
            adesso = _valore_osservato(d, campo)
            testo = formatta_notifica(d, campo, prima, adesso)
            # Il cappello si passa solo alla buona notizia: se i posti si sono
            # esauriti, nessuno ha voglia di offrire caffè.
            tastiera = bottone_supporto(app_ctx.settings) if adesso > 0 else None
            await _avvisa(app_ctx, bot, [r["chat_id"] for r in gruppo], testo, tastiera)

    return controllati, errori


async def job_periodico(context: ContextTypes.DEFAULT_TYPE) -> None:
    app_ctx = context.application.bot_data["ctx"]
    controllati, errori = await controlla_tutti(app_ctx, notifica=True, bot=context.bot)
    if errori:
        log.warning("Job medico: %d controllati, errori: %s", controllati, errori)
