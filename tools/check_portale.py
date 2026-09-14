#!/usr/bin/env python3
"""Diagnostica — verifica il flusso a 3 passaggi sul portale della Regione Veneto.

    python tools/check_portale.py rossi

Da lanciare su ogni macchina nuova PRIMA di dare il deploy per funzionante:
alcuni IP (tipicamente quelli dei VPS) vengono filtrati dal portale.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dottping.net import FetchError  # noqa: E402
from dottping.medici.source import MedicoNonTrovato, cerca_disponibilita  # noqa: E402


async def main() -> int:
    cognome, nome = "", ""
    if len(sys.argv) > 1:
        cognome = sys.argv[1]
        nome = sys.argv[2] if len(sys.argv) > 2 else ""
    else:
        try:
            from dottping.config import load_settings
            s = load_settings()
            cognome, nome = s.medico_cognome, s.medico_nome
        except RuntimeError:
            pass

    if not cognome:
        print("Uso: python tools/check_portale.py <cognome> [nome]")
        return 1

    print(f"→ Cerco «{(nome + ' ' + cognome).strip()}» sul portale della Regione Veneto\n")
    try:
        medici, d = await cerca_disponibilita(cognome, nome or None)
    except MedicoNonTrovato as exc:
        print(f"🔍 {exc}")
        print("   Controlla il cognome: la ricerca del portale è per corrispondenza esatta.")
        return 1
    except FetchError as exc:
        print(f"❌ Flusso fallito: {exc}")
        print("   Se il messaggio è un 403, il portale filtra le richieste da questo IP.")
        print("   Installa il ripiego con:  pip install curl-cffi")
        return 1

    print(f"1) Elenco: {len(medici)} medico/i trovato/i")
    for m in medici:
        print(f"   · [{m.id_luogo}] {m.nome} — {m.tipo}")

    print(f"\n2) Scheda di {d.nome} (idLuogo {d.id_luogo})")
    print(f"   Rilevazione:            {d.data_rilevazione or 'n/d'}")
    print(f"   Assistiti illimitati:   {d.illimitati}")
    print(f"   Assistiti a termine:    {d.a_termine}")
    print(f"   Ambulatorio:            {d.indirizzo}")
    print(f"   Telefono:               {d.telefono}")

    if d.illimitati is None:
        print("\n⚠️  Il numero degli illimitati non è stato letto: la pagina potrebbe")
        print("    essere cambiata. Controlla parse_dettaglio in dottping/medici/source.py.")
        return 1

    print()
    if d.posti_liberi:
        print(f"🟢 Ci sono {d.illimitati} posti: si può fare domanda di cambio.")
    else:
        print("🔴 Nessun posto illimitato ora. È lo stato normale: il bot avvisa quando cambia.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
