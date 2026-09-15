"""Smoke test offline: costruisce l'app ed esercita storage e messaggi, senza rete."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN-NON-VALIDO")
os.environ["DOTTPING_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["CHECK_HOURS"] = "8, 20, 99, ciao"      # 99 e "ciao" vanno scartati
os.environ["ALLOWED_USER_IDS"] = ""                 # bot pubblico

from dottping.bot import build_application  # noqa: E402
from dottping.config import load_settings  # noqa: E402
from dottping.storage import Storage  # noqa: E402
from dottping.textfmt import chunks, esc  # noqa: E402

errors: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        errors.append(msg)


async def storage_checks() -> None:
    st = Storage(Path(os.environ["DOTTPING_DB_PATH"]))
    await st.init()

    check(await st.medico_watch_add(42, "009249", "gironda", "", "GIULIA CARLOTTA GIRONDA") is True,
          "primo medico_watch_add deve tornare True")
    check(await st.medico_watch_add(42, "009249", "gironda", "", "GIULIA CARLOTTA GIRONDA") is False,
          "lo stesso medico non va aggiunto due volte nella stessa chat")
    await st.medico_watch_add(42, "012345", "martelli", "", "LUCA MARTELLI")
    await st.medico_watch_add(99, "009249", "gironda", "", "GIULIA CARLOTTA GIRONDA")

    miei = await st.medico_watch_list(42)
    check(len(miei) == 2, f"la chat 42 deve vedere 2 medici, ne vede {len(miei)}")
    check(len(await st.medico_watch_list(99)) == 1, "le chat non devono vedersi le liste")
    check(len(await st.medico_watch_list()) == 3, "la lista globale deve avere 3 righe")

    check(await st.medico_watch_remove(42, "012345") is True, "rimozione fallita")
    check(await st.medico_watch_remove(42, "non-esiste") is False,
          "rimuovere qualcosa che non c'è deve tornare False")
    check(len(await st.medico_watch_list(99)) == 1,
          "la rimozione di una chat non deve toccare le altre")

    await st.kv_set("medico:stato:009249", {"valore": 0, "illimitati": 0, "a_termine": 7})
    check((await st.kv_get("medico:stato:009249"))["a_termine"] == 7, "kv_get non rilegge")
    check(await st.kv_get("medico:stato:009249", max_age_s=0) is None,
          "la cache non rispetta la scadenza")
    check(len(await st.kv_prefix("medico:stato:")) == 1, "kv_prefix sbagliato")


def app_checks() -> None:
    settings = load_settings()
    check(settings.medico_ore == [8, 20], f"parsing CHECK_HOURS errato: {settings.medico_ore}")
    check(settings.restricted is False, "senza ALLOWED_USER_IDS il bot deve essere pubblico")

    app = build_application(settings)
    registered = {
        cmd
        for handlers in app.handlers.values()
        for h in handlers
        for cmd in getattr(h, "commands", set()) or set()
    }
    attesi = {"start", "help", "id", "status", "medico", "disponibilita",
              "medico_on", "medico_off", "medico_lista", "medico_check",
              "supporta", "dona"}
    mancanti = attesi - registered
    check(not mancanti, f"comandi non registrati: {sorted(mancanti)}")

    job_names = {j.name for j in app.job_queue.jobs()} if app.job_queue else set()
    check({"check_08", "check_20"} <= job_names, f"job non pianificati: {job_names}")
    check("check_99" not in job_names, "un'ora non valida è stata accettata")

    from telegram.ext import CallbackQueryHandler
    callback = [h for hs in app.handlers.values() for h in hs
                if isinstance(h, CallbackQueryHandler)]
    check(bool(callback), "manca il gestore dei bottoni inline")


def supporto_checks() -> None:
    """Il bottone delle offerte esiste solo se l'indirizzo è configurato e https."""
    from dottping.supporto import tastiera

    class FintoSettings:
        supporto_url = "https://ko-fi.com/esempio"

    bottoni = tastiera(FintoSettings())
    check(bottoni is not None, "con un indirizzo valido il bottone deve esserci")
    if bottoni is not None:
        primo = bottoni.inline_keyboard[0][0]
        check(primo.url == "https://ko-fi.com/esempio", "il bottone punta altrove")
        check(primo.callback_data is None, "il bottone deve aprire un link, non un callback")

    FintoSettings.supporto_url = ""
    check(tastiera(FintoSettings()) is None,
          "senza indirizzo non deve comparire nessun bottone")

    # Un indirizzo non https non deve arrivare fino al bottone
    os.environ["SUPPORTO_URL"] = "http://esempio.invalido/paga"
    check(load_settings().supporto_url == "", "un indirizzo non https va scartato")
    os.environ["SUPPORTO_URL"] = "javascript:alert(1)"
    check(load_settings().supporto_url == "", "uno schema strano va scartato")
    os.environ.pop("SUPPORTO_URL")


def messaggi_checks() -> None:
    from dottping.medici.jobs import formatta_notifica, formatta_scheda
    from dottping.medici.source import Disponibilita

    pieno = Disponibilita(id_luogo="009249", nome="GIULIA CARLOTTA GIRONDA",
                          tipo="Medico di assistenza primaria",
                          indirizzo="VIA CIRO FERRARI 9, 37135, VERONA (VR)",
                          telefono="0458200555", data_rilevazione="14/09/2026",
                          illimitati=0, a_termine=7)
    scheda = formatta_scheda(pieno)
    check("🔴" in scheda, "con 0 posti la scheda deve mostrare il pallino rosso")
    check("Assistiti illimitati: 0" in scheda, "scheda senza il numero")
    check("Illimitati</b>: posti" in scheda, "manca la legenda dei due numeri")
    check(scheda.count("<b>") == scheda.count("</b>"), "tag <b> sbilanciati")
    check("\n\n\n" not in scheda, "righe vuote doppie nella scheda")

    libero = Disponibilita(id_luogo="009249", nome="GIULIA CARLOTTA GIRONDA",
                           illimitati=3, a_termine=12, data_rilevazione="20/10/2026")
    check("🟢" in formatta_scheda(libero), "con 3 posti serve il pallino verde")
    check("\n\n\n" not in formatta_scheda(libero), "righe vuote doppie con dati mancanti")

    # Col posto già libero non c'è niente da aspettare: si dice di andare.
    check("domanda di cambio medico adesso" in formatta_scheda(libero),
          "con i posti liberi manca l'invito a fare domanda")
    check("adesso" not in formatta_scheda(pieno),
          "col medico pieno non si deve dire di fare domanda")

    from dottping.medici.handlers import _scheda_e_bottoni
    _, tastiera_pieno = _scheda_e_bottoni(pieno, [], "gironda", "illimitati")
    check(tastiera_pieno is not None
          and "Avvisami" in tastiera_pieno.inline_keyboard[0][0].text,
          "col medico pieno serve la campanella")
    _, tastiera_libero = _scheda_e_bottoni(libero, [], "gironda", "illimitati")
    check(tastiera_libero is None,
          "col posto già libero la campanella non deve comparire")

    apertura = formatta_notifica(libero, "illimitati", 0, 3)
    check("POSTI DISPONIBILI" in apertura, "notifica di apertura sbagliata")
    check("domanda di cambio" in apertura, "manca l'indicazione sul da farsi")

    chiusura = formatta_notifica(pieno, "illimitati", 3, 0)
    check("esauriti" in chiusura, "notifica di chiusura sbagliata")
    check("erano 3" in chiusura, "la chiusura non riporta il valore precedente")

    strano = Disponibilita(id_luogo="x", nome="ROSSI <&> BIANCHI", illimitati=1)
    check("&lt;&amp;&gt;" in formatta_scheda(strano), "nome non messo al sicuro con esc()")

    check(esc("<script>&") == "&lt;script&gt;&amp;", "escape HTML non funziona")
    lungo = "riga di prova\n" * 900
    parti = chunks(lungo)
    check(all(len(p) <= 4096 for p in parti), "chunks supera il limite Telegram")
    check("".join(parti) == lungo, "chunks perde contenuto")


def main() -> int:
    asyncio.run(storage_checks())
    app_checks()
    supporto_checks()
    messaggi_checks()
    for e in errors:
        print(f"FAIL: {e}")
    if not errors:
        print("OK: smoke test - storage, comandi, job, bottoni, supporto, messaggi, legenda, escaping")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
