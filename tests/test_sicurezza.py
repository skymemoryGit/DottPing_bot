"""Test dei freni anti-abuso e della validazione degli input. Tutto offline."""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN-NON-VALIDO")
os.environ["DOTTPING_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "sicurezza.db")

from dottping import freni as mod_freni  # noqa: E402
from dottping.config import load_settings  # noqa: E402
from dottping.medici.source import (  # noqa: E402
    NomeNonValido, controlla_id_luogo, normalizza_nome,
)

errors: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        errors.append(msg)


def alza(funzione, *args) -> bool:
    try:
        funzione(*args)
    except NomeNonValido:
        return True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eccezione inattesa {type(exc).__name__}: {exc}")
    return False


def validazione_checks() -> None:
    check(normalizza_nome("  gironda  ") == "gironda", "lo spazio attorno va tolto")
    check(normalizza_nome("de  luca") == "de luca", "gli spazi interni vanno normalizzati")
    check(normalizza_nome("D'Amico-Rossi") == "D'Amico-Rossi", "apostrofi e trattini sono leciti")
    check(normalizza_nome("Müller") == "Müller", "le lettere accentate sono lecite")

    # Quello che NON deve arrivare al portale
    check(alza(normalizza_nome, "a"), "un solo carattere non è un cognome")
    check(alza(normalizza_nome, ""), "il vuoto non è un cognome")
    check(alza(normalizza_nome, "x" * 41), "oltre 40 caratteri va rifiutato")
    check(alza(normalizza_nome, "rossi<script>"), "i tag HTML vanno rifiutati")
    check(alza(normalizza_nome, "rossi' OR 1=1--"), "le stringhe da injection vanno rifiutate")
    check(alza(normalizza_nome, "rossi\nSet-Cookie: x=1"), "gli a capo vanno rifiutati")
    check(alza(normalizza_nome, "rossi;ls"), "i separatori di comando vanno rifiutati")
    check(alza(normalizza_nome, "http://evil.example/x"), "gli URL vanno rifiutati")
    check(alza(normalizza_nome, "rossi123"), "le cifre non stanno in un cognome")

    check(controlla_id_luogo("009249") == "009249", "un idLuogo normale deve passare")
    check(alza(controlla_id_luogo, "../../etc/passwd"), "il path traversal va rifiutato")
    check(alza(controlla_id_luogo, "0092 49"), "gli spazi nell'idLuogo vanno rifiutati")
    check(alza(controlla_id_luogo, ""), "un idLuogo vuoto va rifiutato")


def freni_checks() -> None:
    f = mod_freni.Freni(comandi_min=3, ricerche_min=2, ricerche_ora=3, portale_min=2)

    for _ in range(3):
        check(f.attesa_comando(1) == 0, "i primi comandi devono passare")
        f.segna_comando(1)
    check(f.attesa_comando(1) > 0, "il quarto comando nello stesso minuto va frenato")
    check(f.attesa_comando(2) == 0, "il freno di un utente non deve toccare gli altri")

    check(f.deve_avvisare(1) is True, "il primo avviso di flood va dato")
    check(f.deve_avvisare(1) is False, "il secondo avviso no: sarebbe floodare a nostra volta")

    for _ in range(2):
        check(f.attesa_ricerca(7) == 0, "le prime ricerche devono passare")
        f.segna_ricerca(7)
    check(f.attesa_ricerca(7) > 0, "la terza ricerca nel minuto va frenata")

    for _ in range(2):
        check(f.attesa_portale() == 0, "le prime richieste globali devono passare")
        f.segna_portale()
    check(f.attesa_portale() > 0, "il tetto globale al portale non scatta")

    # Finestra scaduta: il permesso torna
    scorsa = mod_freni.Finestra(1, 0.05)
    scorsa.segna("x")
    check(scorsa.attesa("x") > 0, "subito dopo deve frenare")
    import time
    time.sleep(0.08)
    check(scorsa.attesa("x") == 0, "passata la finestra deve ripassare")

    # La memoria dei contatori non deve crescere all'infinito
    tanti = mod_freni.Finestra(1, 0.01)
    for i in range(mod_freni.MAX_CHIAVI + 100):
        tanti.segna(i)
    check(len(tanti._eventi) <= mod_freni.MAX_CHIAVI + 1,
          f"le chiavi scadute non vengono sfoltite: {len(tanti._eventi)}")


def sanzioni_checks() -> None:
    """La pausa deve crescere a ogni ricaduta, non restare sempre di un minuto."""
    s = mod_freni.Sanzioni(scala=(60, 300, 900, 3600), decadenza_s=3600)

    check(s.residuo(1) == 0, "un utente nuovo non è in pausa")
    check(s.infrazione(1) == 60, "la prima pausa è la più corta")
    check(s.residuo(1) > 0, "dopo l'infrazione deve esserci una pausa in corso")

    # Insistere DURANTE la pausa non la allunga: sta già fermo.
    prima = s.residuo(1)
    check(s.infrazione(1) <= prima + 1, "bussare durante la pausa non deve aggravarla")
    check(s.stato(1)["gradini"] == 1, "i gradini non devono salire mentre è in pausa")

    # Ricaduta dopo la pausa: si sale di gradino
    s._stato[1]["fino_a"] = time.time() - 1          # pausa finita
    check(s.infrazione(1) == 300, "la seconda infrazione deve costare di più")
    s._stato[1]["fino_a"] = time.time() - 1
    check(s.infrazione(1) == 900, "la terza ancora di più")
    s._stato[1]["fino_a"] = time.time() - 1
    check(s.infrazione(1) == 3600, "la quarta arriva in cima alla scala")
    s._stato[1]["fino_a"] = time.time() - 1
    check(s.infrazione(1) == 3600, "oltre la cima la pausa resta al massimo, non esplode")

    # Chi si comporta bene per un po' torna pulito
    s._stato[1]["fino_a"] = time.time() - 1
    s._stato[1]["ultima"] = time.time() - 7200      # due ore fa, oltre la decadenza
    check(s.infrazione(1) == 60, "dopo la decadenza si riparte dalla pausa più corta")

    check(s.residuo(2) == 0, "la pausa di uno non tocca gli altri")

    # Ripristino dopo un riavvio
    nuova = mod_freni.Sanzioni(scala=(60, 300), decadenza_s=3600)
    quante = nuova.ripristina([
        {"utente": 5, "gradini": 2, "fino_a": time.time() + 500, "ultima": time.time()},
        {"utente": 6, "gradini": 1, "fino_a": time.time() - 500, "ultima": time.time() - 9999},
        {"rotta": True},
    ])
    check(quante == 1, f"doveva ripristinare una sola pausa attiva, non {quante}")
    check(nuova.residuo(5) > 0, "la pausa attiva non è sopravvissuta al riavvio")
    check(nuova.residuo(6) == 0, "una pausa scaduta non va ripristinata")

    check(mod_freni.Freni().residuo(99) == 0, "i freni nuovi non hanno pause attive")


def cache_checks() -> None:
    cache = mod_freni.CacheBreve(ttl_s=0.05, massimo=4)
    cache.set("k", {"a": [1, 2]})
    letto = cache.get("k")
    check(letto == {"a": [1, 2]}, "la cache non rilegge quello che ha scritto")
    letto["a"].append(3)
    check(cache.get("k") == {"a": [1, 2]},
          "la cache restituisce l'originale: chi lo modifica rovina gli altri")

    import time
    time.sleep(0.08)
    check(cache.get("k") is None, "la cache non rispetta la scadenza")

    for i in range(20):
        cache.set(f"k{i}", i)
    check(len(cache._voci) <= 20, "la cache cresce oltre il massimo")


def settings_checks() -> None:
    os.environ["MAX_RICERCHE_MIN"] = "0"       # un limite a 0 spegnerebbe il bot
    os.environ["MAX_MEDICI_TOTALI"] = "ciao"   # valore non numerico
    s = load_settings()
    check(s.max_ricerche_min >= 1, "un limite a 0 va riportato al minimo")
    check(s.max_medici_totali == 50, "un valore non numerico deve ricadere sul default")
    os.environ.pop("MAX_RICERCHE_MIN")
    os.environ.pop("MAX_MEDICI_TOTALI")


def main() -> int:
    validazione_checks()
    freni_checks()
    sanzioni_checks()
    cache_checks()
    settings_checks()
    for e in errors:
        print(f"FAIL: {e}")
    if not errors:
        print("OK: sicurezza - validazione input, freni, pause progressive, cache, limiti dal .env")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
