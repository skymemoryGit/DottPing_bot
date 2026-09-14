"""Verifica dei parser su markup reale del portale della Regione Veneto."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dottping.medici.source import (  # noqa: E402
    _scegli, parse_dettaglio, parse_risultati,
)

DIR = Path(__file__).parent
errors: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        errors.append(msg)


def test_risultati() -> None:
    medici = parse_risultati((DIR / "fixture_risultati.html").read_text(encoding="utf-8"))
    check(len(medici) == 2, f"attesi 2 medici, trovati {len(medici)}")
    if len(medici) < 2:
        return

    primo = medici[0]
    check(primo.id_luogo == "009249", f"idLuogo errato: {primo.id_luogo}")
    check(primo.nome == "GIULIA CARLOTTA GIRONDA", f"nome errato: {primo.nome!r}")
    check("marker" not in primo.nome.lower(), "lo span del marker è finito dentro il nome")
    check(primo.tipo == "Medico di assistenza primaria", f"tipo errato: {primo.tipo!r}")
    check("VIA CIRO FERRARI 9" in primo.indirizzo, f"indirizzo errato: {primo.indirizzo!r}")

    # disambiguazione per nome, il caso di due medici con lo stesso cognome
    check(_scegli(medici, "gironda", "MARCO").id_luogo == "012345",
          "il filtro per nome non disambigua")
    check(_scegli(medici, "gironda", None).id_luogo == "009249",
          "senza nome deve prendere il primo")
    check(_scegli(medici, "gironda", "INESISTENTE").id_luogo == "009249",
          "con un nome che non c'è deve ripiegare sul primo, non esplodere")


def test_dettaglio_pieno() -> None:
    d = parse_dettaglio((DIR / "fixture_dettaglio.html").read_text(encoding="utf-8"), "009249")
    check(d.illimitati == 0, f"illimitati errato: {d.illimitati}")
    check(d.a_termine == 7, f"a termine errato: {d.a_termine}")
    check(d.data_rilevazione == "14/09/2026", f"data errata: {d.data_rilevazione!r}")
    check(d.nome == "GIULIA CARLOTTA GIRONDA", f"nome errato: {d.nome!r}")
    check(d.telefono == "0458200555", f"telefono errato: {d.telefono!r}")
    check("VIA CIRO FERRARI 9" in d.indirizzo, f"indirizzo errato: {d.indirizzo!r}")
    check("Telefono" not in d.indirizzo, "il telefono è finito dentro l'indirizzo")
    check(d.posti_liberi is False, "con 0 illimitati posti_liberi deve essere False")
    # la tabella degli orari non deve essere scambiata per quella delle disponibilità
    check(len(d.voci) == 2, f"voci inattese: {d.voci}")


def test_dettaglio_disponibile() -> None:
    d = parse_dettaglio((DIR / "fixture_disponibile.html").read_text(encoding="utf-8"))
    check(d.illimitati == 3, f"illimitati errato: {d.illimitati}")
    check(d.a_termine == 12, f"a termine errato: {d.a_termine}")
    check(d.posti_liberi is True, "con 3 illimitati posti_liberi deve essere True")


def test_pagina_rotta() -> None:
    from dottping.net import FetchError
    try:
        parse_dettaglio("<html><body><p>manutenzione</p></body></html>")
    except FetchError:
        pass
    else:
        errors.append("una pagina senza tabella deve alzare FetchError, non restituire zeri")


def main() -> int:
    test_risultati()
    test_dettaglio_pieno()
    test_dettaglio_disponibile()
    test_pagina_rotta()
    for e in errors:
        print(f"FAIL: {e}")
    if not errors:
        print("OK: parser medico - elenco, disambiguazione, scheda, posti liberi, pagina rotta")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
