"""Interroga il portale "Trova Medici" della Regione Veneto.

Il servizio è una portlet Liferay che tiene lo stato in sessione: non esiste un
URL diretto alla scheda di un medico. Vanno rifatti i tre passaggi che farebbe
una persona, riusando gli stessi cookie:

    1. GET  della pagina di ricerca            → apre la sessione (JSESSIONID)
    2. POST del form `formRicerca` (cognome)   → pagina con l'elenco dei medici
    3. POST del form `formDettaglio` (idLuogo) → scheda con i posti disponibili

`idLuogo` (es. '009249') si legge nell'elenco, dentro `onclick="submitLuogo(...)"`.
Non va messo nel codice: è un identificativo interno che può cambiare, e comunque
l'elenco serve già per trovare il medico giusto.

Il flusso è sequenziale e sincrono per natura, quindi è scritto una volta sola e
il chiamante lo esegue in un thread (`asyncio.to_thread`) per non bloccare il bot.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

from ..freni import freni
from ..net import BROWSER_HEADERS, FetchError

log = logging.getLogger(__name__)

BASE = "https://salute.regione.veneto.it/servizi/cerca-medici-e-pediatri"
PORTLET = "MEDICI_WAR_portalgeoreferenziazione_INSTANCE_F5Pm"
COMMON = (
    f"?p_p_id={PORTLET}&p_p_lifecycle={{lifecycle}}&p_p_state=normal&p_p_mode=view"
    f"&p_p_col_id=column-1&p_p_col_count=1&_{PORTLET}_action={{action}}"
)

URL_RICERCA = BASE + COMMON.format(lifecycle=0, action="ricerca")   # passo 1
URL_POST_RICERCA = BASE + COMMON.format(lifecycle=1, action="ricerca")  # passo 2
URL_POST_DETTAGLIO = BASE + COMMON.format(lifecycle=1, action="result")  # passo 3

_ID_LUOGO_RE = re.compile(r"submitLuogo\(\s*'([^']+)'\s*\)")
_DATA_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
_TEL_RE = re.compile(r"Telefono:\s*([0-9 ./+-]{5,})")


class MedicoNonTrovato(Exception):
    """La ricerca non ha prodotto nessun medico corrispondente."""


class NomeNonValido(ValueError):
    """Quello che è arrivato dalla chat non è un nome di persona."""


class TroppeRichieste(RuntimeError):
    """Il tetto globale di richieste al portale è stato raggiunto: si aspetta."""

    def __init__(self, secondi: float) -> None:
        super().__init__(f"Troppe richieste al portale: riprova tra {int(secondi) + 1}s.")
        self.secondi = secondi


# Nomi di persona: lettere (accentate comprese), spazi, apostrofi, punti, trattini.
# Tutto il resto — cifre, tag, a capo, punti e virgola, stringhe chilometriche —
# non arriva mai al portale: non è roba che un medico possa avere nel nome, e
# spedirla significa solo far sembrare strano il nostro traffico.
_NOME_OK = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'’ .\-]{2,40}$")
_ID_LUOGO_OK = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


def normalizza_nome(testo: str, *, campo: str = "cognome") -> str:
    """Ripulisce e controlla quello che arriva dalla chat. Alza NomeNonValido."""
    pulito = re.sub(r"\s+", " ", testo or "").strip()
    if not _NOME_OK.match(pulito):
        raise NomeNonValido(
            f"Il {campo} può contenere solo lettere, spazi, apostrofi e trattini "
            "(da 2 a 40 caratteri)."
        )
    return pulito


def controlla_id_luogo(id_luogo: str) -> str:
    """L'identificativo interno del portale: corto e alfanumerico, o niente."""
    if not _ID_LUOGO_OK.match(id_luogo or ""):
        raise NomeNonValido("Identificativo del medico non valido.")
    return id_luogo


@dataclass
class Medico:
    id_luogo: str
    nome: str
    tipo: str = ""
    indirizzo: str = ""


@dataclass
class Disponibilita:
    id_luogo: str
    nome: str
    tipo: str = ""
    indirizzo: str = ""
    telefono: str = ""
    data_rilevazione: str = ""
    illimitati: int | None = None
    a_termine: int | None = None
    voci: dict[str, int] = field(default_factory=dict)

    @property
    def posti_liberi(self) -> bool:
        return bool(self.illimitati)

    def as_dict(self) -> dict:
        return asdict(self)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ----------------------------------------------------------------- parser

def parse_risultati(html: str) -> list[Medico]:
    """Estrae i medici dall'elenco (passo 2).

    Markup di riferimento:
        <tr><td><span onclick="submitLuogo('009249')" class="poi">
              <span class="marker linked"></span>
              <span class="link">GIULIA CARLOTTA GIRONDA</span>
            </span><br><em>Medico di assistenza primaria</em></td>
            <td>VIA CIRO FERRARI 9, 37135, VERONA (VR) - <i>(ambulatorio principale)</i></td></tr>
    """
    soup = BeautifulSoup(html, "html.parser")
    medici: list[Medico] = []

    for span in soup.select("span.poi[onclick]"):
        m = _ID_LUOGO_RE.search(span.get("onclick", ""))
        if not m:
            continue

        etichetta = span.select_one("span.link")
        nome = _clean(etichetta.get_text(" ") if etichetta else span.get_text(" "))
        if not nome:
            continue

        cella = span.find_parent("td")
        riga = span.find_parent("tr")
        tipo = ""
        if cella and cella.find("em"):
            tipo = _clean(cella.find("em").get_text(" "))
        indirizzo = ""
        if riga:
            celle = riga.find_all("td")
            if len(celle) > 1:
                indirizzo = _clean(celle[1].get_text(" "))

        medici.append(Medico(id_luogo=m.group(1), nome=nome, tipo=tipo, indirizzo=indirizzo))

    return medici


def parse_dettaglio(html: str, id_luogo: str = "") -> Disponibilita:
    """Estrae i posti dalla scheda del medico (passo 3).

    Markup di riferimento:
        <strong>GIULIA CARLOTTA GIRONDA</strong><br><em>Medico di assistenza primaria</em>
        <table class="results">
          <thead><tr><th>Disponibilità rilevata alla data 14/09/2026</th><th>N.ro posti</th></tr></thead>
          <tbody>
            <tr><td>Disponibilità assistiti illimitati</td><td>0</td></tr>
            <tr><td>Disponibilità assistiti a termine</td><td>7</td></tr>
          </tbody>
        </table>
    """
    soup = BeautifulSoup(html, "html.parser")

    tabella = None
    for t in soup.select("table.results"):
        if "disponibilit" in t.get_text(" ").lower():
            tabella = t
            break
    if tabella is None:
        raise FetchError(
            "Tabella delle disponibilità non trovata: il portale ha probabilmente "
            "cambiato struttura (controlla parse_dettaglio in medico/source.py)."
        )

    intestazione = _clean(tabella.get_text(" "))
    data = _DATA_RE.search(intestazione)

    voci: dict[str, int] = {}
    for tr in tabella.select("tbody tr"):
        celle = tr.find_all("td")
        if len(celle) < 2:
            continue
        etichetta = _clean(celle[0].get_text(" ")).lower()
        valore = _clean(celle[1].get_text(" "))
        try:
            voci[etichetta] = int(re.sub(r"[^\d-]", "", valore) or 0)
        except ValueError:
            continue

    def trova(*parole: str) -> int | None:
        for chiave, valore in voci.items():
            if all(p in chiave for p in parole):
                return valore
        return None

    nome, tipo = "", ""
    strong = soup.find("strong")
    if strong:
        nome = _clean(strong.get_text(" "))
        em = strong.find_next("em")
        if em:
            tipo = _clean(em.get_text(" "))

    indirizzo, telefono = "", ""
    for fs in soup.find_all("fieldset"):
        legend = fs.find("legend")
        if legend and "ambulatorio" in legend.get_text(" ").lower():
            testo = _clean(fs.get_text(" "))
            testo = testo.replace(_clean(legend.get_text(" ")), "", 1).strip()
            tel = _TEL_RE.search(testo)
            if tel:
                telefono = _clean(tel.group(1))
                testo = testo[: tel.start()]
            indirizzo = _clean(testo.split("Telefono")[0])[:150]
            break

    return Disponibilita(
        id_luogo=id_luogo,
        nome=nome,
        tipo=tipo,
        indirizzo=indirizzo,
        telefono=telefono,
        data_rilevazione=data.group(1) if data else "",
        illimitati=trova("illimitat"),
        a_termine=trova("termine"),
        voci=voci,
    )


# ----------------------------------------------------------------- flusso HTTP

def _apri_sessione(motore: str):
    """Restituisce un client con la stessa interfaccia minima (get/post/.text)."""
    if motore == "curl_cffi":
        from curl_cffi import requests as cffi  # dipendenza opzionale
        return cffi.Session(impersonate="chrome", timeout=40)

    import httpx
    return httpx.Client(
        headers=BROWSER_HEADERS, timeout=40, follow_redirects=True
    )


def _flusso(cognome: str, nome: str | None, motore: str,
            id_luogo: str | None = None) -> tuple[list[Medico], Disponibilita | None]:
    sessione = _apri_sessione(motore)
    try:
        # passo 1 — apre la sessione e prende i cookie
        r = sessione.get(URL_RICERCA)
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code} sulla pagina di ricerca")

        # passo 2 — invia il form. I campi vuoti/'000' equivalgono a "non filtrare".
        dati = {
            "cambioProvincia": "false",
            "provincia": "000",
            "comune": "000",
            "nome": (nome or "").strip(),
            "cognome": cognome.strip(),
            "indirizzoMedico": "",
            "tipologia": "000",
            "indirizzo": "",
            "distanza": "000",
            "invia": "CERCA",
        }
        r = sessione.post(URL_POST_RICERCA, data=dati)
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code} sulla ricerca")

        medici = parse_risultati(r.text)
        if not medici:
            return [], None

        scelto = _scegli(medici, cognome, nome, id_luogo)

        # passo 3 — apre la scheda del medico scelto
        r = sessione.post(URL_POST_DETTAGLIO, data={"idLuogo": scelto.id_luogo})
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code} sulla scheda del medico")

        dettaglio = parse_dettaglio(r.text, scelto.id_luogo)
        if not dettaglio.nome:
            dettaglio.nome = scelto.nome
        if not dettaglio.indirizzo:
            dettaglio.indirizzo = scelto.indirizzo
        return medici, dettaglio
    finally:
        chiudi = getattr(sessione, "close", None)
        if callable(chiudi):
            chiudi()


def _scegli(medici: list[Medico], cognome: str, nome: str | None,
            id_luogo: str | None = None) -> Medico:
    """Sceglie il medico nell'elenco.

    Ordine di precedenza: idLuogo esatto (usato dalla sorveglianza, che sa già
    quale medico segue) → nome → primo col cognome cercato.
    """
    if id_luogo:
        esatto = [m for m in medici if m.id_luogo == id_luogo]
        if esatto:
            return esatto[0]
    if nome:
        atteso = nome.strip().lower()
        esatti = [m for m in medici if atteso in m.nome.lower()]
        if esatti:
            return esatti[0]
    cog = cognome.strip().lower()
    per_cognome = [m for m in medici if cog in m.nome.lower()]
    return (per_cognome or medici)[0]


async def cerca_disponibilita(
    cognome: str, nome: str | None = None, id_luogo: str | None = None,
    *, sorgente: str = "utente",
) -> tuple[list[Medico], Disponibilita]:
    """Esegue il flusso completo. Alza MedicoNonTrovato, FetchError o NomeNonValido.

    `id_luogo` serve alla sorveglianza: identifica il medico esatto anche se
    altri condividono il cognome.

    `sorgente="job"` è il controllo periodico nostro: non passa dal tetto
    globale (è traffico che sappiamo di volere), ma usa la stessa cache e lo
    stesso semaforo di tutti.

    Qui passano TUTTE le richieste al portale, quindi qui stanno i freni:
    validazione, cache, tetto globale, semaforo.
    """
    cognome = normalizza_nome(cognome)
    nome = normalizza_nome(nome, campo="nome") if nome else None
    if id_luogo:
        controlla_id_luogo(id_luogo)

    f = freni()
    chiave = (cognome.casefold(), (nome or "").casefold(), id_luogo or "")
    pronto = f.cache.get(chiave)
    if pronto is not None:
        log.debug("Cache: %s", chiave)
        return pronto

    if sorgente != "job":
        attesa = f.attesa_portale()
        if attesa > 0:
            raise TroppeRichieste(attesa)

    motori = ["httpx", "curl_cffi"]
    ultimo_errore: Exception | None = None

    async with f.semaforo:
        # Ricontrolla la cache: mentre aspettavamo il semaforo qualcun altro
        # può aver già chiesto la stessa cosa.
        pronto = f.cache.get(chiave)
        if pronto is not None:
            return pronto

        for motore in motori:
            f.segna_portale()
            try:
                medici, dettaglio = await asyncio.to_thread(
                    _flusso, cognome, nome, motore, id_luogo
                )
            except ImportError:
                log.info("curl_cffi non installato: nessun secondo tentativo.")
                break
            except FetchError as exc:
                # 403/503 = probabile filtro sul traffico "da script": riprova col motore dopo
                ultimo_errore = exc
                log.warning("Tentativo con %s fallito: %s", motore, exc)
                continue

            if not medici:
                raise MedicoNonTrovato(f"Nessun medico trovato per il cognome '{cognome}'.")
            f.cache.set(chiave, (medici, dettaglio))
            return medici, dettaglio

    raise ultimo_errore or FetchError("Portale della Regione Veneto non raggiungibile.")
