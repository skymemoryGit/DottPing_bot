"""Freni anti-abuso: il bot è pubblico, il portale della Regione no.

Ogni ricerca si traduce in tre richieste al portale, fatte dall'IP di questo
server. Senza freni bastano un utente molesto o uno script perché il server
sembri uno scraper e si becchi un blocco — e nel frattempo il bot resterebbe
occupato a fare da proxy per qualcun altro.

Tre livelli, dal più stretto al più largo:

  1. comandi per utente  — quanti messaggi al minuto accetto da uno
  2. ricerche per utente — quante interrogazioni al portale può causare
  3. richieste globali   — quante ne partono in tutto, chiunque le chieda

più due cose che tolgono lavoro invece di rifiutarlo: una cache breve (la
stessa ricerca ripetuta non ripassa dal portale) e un semaforo che tiene basso
il numero di flussi HTTP in parallelo.

Tutto in memoria: al riavvio si riparte da zero, e va bene così. Questi freni
servono a non fare danni adesso, non a tenere una contabilità.
"""
from __future__ import annotations

import asyncio
import copy
import time
from collections import defaultdict, deque
from typing import Any, Hashable

# Oltre questa soglia si sfoltiscono le chiavi scadute: nemmeno la memoria
# del contatore deve poter crescere all'infinito.
MAX_CHIAVI = 10_000


class Finestra:
    """Contatore a finestra scorrevole.

    `attesa(chiave)` dice quanti secondi mancano al via libera (0 = passa),
    `segna(chiave)` registra che l'azione è avvenuta.
    """

    def __init__(self, limite: int, secondi: float) -> None:
        self.limite = max(1, limite)
        self.secondi = secondi
        self._eventi: dict[Hashable, deque[float]] = defaultdict(deque)

    def attesa(self, chiave: Hashable = "") -> float:
        coda = self._eventi[chiave]
        adesso = time.monotonic()
        while coda and coda[0] <= adesso - self.secondi:
            coda.popleft()
        if len(coda) < self.limite:
            return 0.0
        return max(0.0, self.secondi - (adesso - coda[0]))

    def segna(self, chiave: Hashable = "") -> None:
        self._eventi[chiave].append(time.monotonic())
        if len(self._eventi) > MAX_CHIAVI:
            self._sfoltisci()

    def _sfoltisci(self) -> None:
        adesso = time.monotonic()
        scadute = [k for k, coda in self._eventi.items()
                   if not coda or coda[-1] <= adesso - self.secondi]
        for k in scadute:
            del self._eventi[k]


class CacheBreve:
    """Risultati del portale tenuti da parte per qualche minuto.

    I numeri del portale cambiano una volta al giorno: rileggerli venti volte
    al minuto non serve a nessuno, e chi insiste a chiederli ottiene comunque
    una risposta — solo senza traffico in uscita.
    """

    def __init__(self, ttl_s: float = 120.0, massimo: int = 300) -> None:
        self.ttl_s = ttl_s
        self.massimo = massimo
        self._voci: dict[Hashable, tuple[float, Any]] = {}

    def get(self, chiave: Hashable) -> Any | None:
        voce = self._voci.get(chiave)
        if voce is None:
            return None
        scritta, valore = voce
        if time.monotonic() - scritta > self.ttl_s:
            self._voci.pop(chiave, None)
            return None
        # copia: chi la riceve non deve poter modificare quella degli altri
        return copy.deepcopy(valore)

    def set(self, chiave: Hashable, valore: Any) -> None:
        if len(self._voci) >= self.massimo:
            self._scada()
        self._voci[chiave] = (time.monotonic(), copy.deepcopy(valore))

    def _scada(self) -> None:
        adesso = time.monotonic()
        for k, (scritta, _) in list(self._voci.items()):
            if adesso - scritta > self.ttl_s:
                del self._voci[k]
        if len(self._voci) >= self.massimo:   # ancora piena: via le più vecchie
            for k, _ in sorted(self._voci.items(), key=lambda kv: kv[1][0])[: self.massimo // 4]:
                del self._voci[k]

    def svuota(self) -> None:
        self._voci.clear()


# Quanto dura la pausa, alla prima infrazione e a quelle dopo: un minuto,
# cinque, un quarto d'ora, un'ora, sei ore, un giorno. Chi sbaglia una volta
# quasi non se ne accorge; chi insiste smette di trovarlo comodo.
SCALA_PAUSE = (60, 300, 900, 3600, 6 * 3600, 24 * 3600)

# Dopo sei ore senza infrazioni si riparte da capo: una persona che un giorno
# ha cliccato troppo in fretta non deve pagarla per sempre.
DECADENZA_S = 6 * 3600


class Sanzioni:
    """Pause progressive per chi continua a sbattere contro i limiti.

    Una finestra scorrevole da sola non basta: superi il limite, aspetti un
    minuto, ricominci — per sempre. Uno script ci convive benissimo. Qui ogni
    infrazione *nuova* (fatta a pausa finita, non durante) fa salire di un
    gradino la volta successiva.

    Il tempo è quello dell'orologio, non `monotonic`: le pause vengono salvate
    su disco e devono sopravvivere a un riavvio del bot.
    """

    def __init__(self, scala: tuple[int, ...] = SCALA_PAUSE,
                 decadenza_s: float = DECADENZA_S) -> None:
        self.scala = scala
        self.decadenza_s = decadenza_s
        self._stato: dict[int, dict] = {}

    def residuo(self, utente: int) -> float:
        """Secondi che mancano alla fine della pausa. 0 = può parlare."""
        voce = self._stato.get(utente)
        if not voce:
            return 0.0
        return max(0.0, float(voce.get("fino_a", 0)) - time.time())

    def infrazione(self, utente: int) -> float:
        """Registra una violazione e restituisce la durata della pausa.

        Se la pausa è già in corso non conta come infrazione nuova: chi è
        fermo continua a bussare, non sta peggiorando la situazione.
        """
        adesso = time.time()
        residuo = self.residuo(utente)
        if residuo > 0:
            return residuo

        voce = self._stato.get(utente) or {}
        ultima = float(voce.get("ultima", 0))
        gradini = int(voce.get("gradini", 0))
        if ultima and adesso - ultima > self.decadenza_s:
            gradini = 0          # è passato un bel po': fiducia ripristinata

        durata = self.scala[min(gradini, len(self.scala) - 1)]
        self._stato[utente] = {
            "utente": utente,
            "gradini": gradini + 1,
            "fino_a": adesso + durata,
            "ultima": adesso,
        }
        self._sfoltisci()
        return float(durata)

    def stato(self, utente: int) -> dict:
        return dict(self._stato.get(utente) or {})

    def perdona(self, utente: int) -> None:
        self._stato.pop(utente, None)

    def ripristina(self, voci) -> int:
        """Ricarica le pause salvate (all'avvio), scartando quelle scadute."""
        adesso = time.time()
        quante = 0
        for voce in voci or ():
            try:
                utente = int(voce["utente"])
            except (TypeError, KeyError, ValueError):
                continue
            if float(voce.get("fino_a", 0)) <= adesso and \
                    adesso - float(voce.get("ultima", 0)) > self.decadenza_s:
                continue            # scaduta e pure decaduta: si dimentica
            self._stato[utente] = dict(voce)
            if float(voce.get("fino_a", 0)) > adesso:
                quante += 1
        return quante

    def _sfoltisci(self) -> None:
        if len(self._stato) <= MAX_CHIAVI:
            return
        adesso = time.time()
        for utente, voce in list(self._stato.items()):
            if adesso - float(voce.get("ultima", 0)) > self.decadenza_s:
                del self._stato[utente]


class Freni:
    """Tutti i limiti insieme, così chi li usa non deve conoscerne i dettagli."""

    def __init__(self, *, comandi_min: int = 20, ricerche_min: int = 5,
                 ricerche_ora: int = 40, portale_min: int = 20,
                 cache_s: float = 120.0, paralleli: int = 2) -> None:
        self.sanzioni = Sanzioni()
        self.comandi = Finestra(comandi_min, 60)
        self.ricerche_breve = Finestra(ricerche_min, 60)
        self.ricerche_lunga = Finestra(ricerche_ora, 3600)
        self.portale = Finestra(portale_min, 60)
        self.cache = CacheBreve(cache_s)
        self.semaforo = asyncio.Semaphore(max(1, paralleli))
        self._avvisi: Finestra = Finestra(1, 60)   # un avviso al minuto per utente

    # ---------------------------------------------------------- comandi
    def attesa_comando(self, utente: int) -> float:
        return self.comandi.attesa(utente)

    def segna_comando(self, utente: int) -> None:
        self.comandi.segna(utente)

    def deve_avvisare(self, utente: int) -> bool:
        """Chi flooda va avvisato una volta sola: rispondergli sempre è floodare a nostra volta."""
        if self._avvisi.attesa(utente) > 0:
            return False
        self._avvisi.segna(utente)
        return True

    # ---------------------------------------------------------- pause
    def residuo(self, utente: int) -> float:
        """Secondi di pausa che restano a questo utente (0 = può parlare)."""
        return self.sanzioni.residuo(utente)

    def infrazione(self, utente: int) -> float:
        """Un limite è stato superato: fa scattare (o allungare) la pausa."""
        return self.sanzioni.infrazione(utente)

    # ---------------------------------------------------------- ricerche
    def attesa_ricerca(self, utente: int) -> float:
        return max(self.ricerche_breve.attesa(utente), self.ricerche_lunga.attesa(utente))

    def segna_ricerca(self, utente: int) -> None:
        self.ricerche_breve.segna(utente)
        self.ricerche_lunga.segna(utente)

    # ---------------------------------------------------------- portale
    def attesa_portale(self) -> float:
        return self.portale.attesa()

    def segna_portale(self) -> None:
        self.portale.segna()


# Istanza unica: il bot è un processo solo. `configura` la rifà leggendo il .env.
FRENI = Freni()


def configura(settings: Any) -> Freni:
    """Rilegge i limiti dalle impostazioni. Chiamata all'avvio da bot.py."""
    global FRENI
    FRENI = Freni(
        comandi_min=getattr(settings, "max_comandi_min", 20),
        ricerche_min=getattr(settings, "max_ricerche_min", 5),
        ricerche_ora=getattr(settings, "max_ricerche_ora", 40),
        portale_min=getattr(settings, "max_portale_min", 20),
        cache_s=getattr(settings, "cache_portale_s", 120),
        paralleli=getattr(settings, "portale_paralleli", 2),
    )
    return FRENI


def freni() -> Freni:
    """Accesso all'istanza corrente (non importare FRENI direttamente: cambia a configura())."""
    return FRENI
