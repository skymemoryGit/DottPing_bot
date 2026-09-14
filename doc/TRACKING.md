# DottPing — Tracking

Registro del progetto. **Una riga per ogni cosa fatta o da fare.**
Chi lavora su DottPing legge questo file all'inizio e lo aggiorna alla fine.

- Formato date: `AAAA-MM-GG`
- Stati: `✅ fatto` · `🔄 in corso` · `⏸ in attesa` · `❌ scartato`

---

## Stato attuale in una riga

Progetto appena staccato da Nora, funzionante in locale, mai ancora messo su VPS.

---

## Fatto

### 2026-09-14 — Nascita del progetto

| | Cosa | Note |
|---|---|---|
| ✅ | Nato come modulo di Nora | Flusso a 3 passaggi, parser, sorveglianza, notifiche |
| ✅ | **Staccato in progetto indipendente** | Token, database, configurazione e documentazione propri; nessuna dipendenza da Nora |
| ✅ | Struttura a layer | `dottping/` servizi, `dottping/medici/` la funzionalità |
| ✅ | Lista dei medici nel database | Tabella `medico_watch`, una riga per (chat, medico); si gestisce da chat |
| ✅ | Bottoni inline | Scelta fra omonimi e rimozione dalla sorveglianza |
| ✅ | Notifica ai soli cambi di stato | 0→N e N→0; stato precedente nella tabella `kv` |
| ✅ | Un fetch per medico, non per chat | Il job raggruppa per `idLuogo` |
| ✅ | Legenda dei due numeri | In fondo a ogni messaggio |
| ✅ | Bot pubblico | Nessuna whitelist: ogni chat ha la sua lista. `ALLOWED_USER_IDS` resta come opzione |
| ✅ | Ripiego anti-WAF | header da Chrome, poi `curl_cffi` se arriva 403 |
| ✅ | Test offline | `test_parser.py` e `test_smoke.py`, nessuna chiamata di rete |
| ✅ | Strumenti | `tools/check_portale.py`, `tools/db_peek.py` |
| ✅ | Unit systemd | `deploy/dottping.service` |

---

## Da fare

### Priorità alta

| | Cosa | Perché |
|---|---|---|
| ⏸ | **Creare il bot su @BotFather** e mettere il token nel `.env` | Serve un token dedicato: due processi con lo stesso token si rubano i messaggi |
| ⏸ | Primo avvio e prova end-to-end | `/medico rossi`, poi `/medico_on rossi` |
| ⏸ | `python tools/check_portale.py` **sul VPS** | Se quell'IP è filtrato serve `pip install curl-cffi` |
| ⏸ | Deploy sul VPS con systemd | Il file unit è pronto |

### Priorità media

| | Cosa | Note |
|---|---|---|
| ⏸ | Verificare una notifica vera | Finora mai scattata: serve che si liberi un posto davvero |
| ⏸ | Messaggio di benvenuto più esplicito | È pubblico: chi arriva non sa cos'è «assistiti illimitati» finché non lancia un comando |
| ⏸ | Rotazione dei log | Se gira come servizio, `RotatingFileHandler` |
| ⏸ | Log leggibili su Windows | La console cp1252 stampa `?` al posto di à/è: `PYTHONUTF8=1` |

### Priorità bassa / idee

| | Cosa | Note |
|---|---|---|
| ⏸ | Pediatri di libera scelta | Stesso portale, stessa tipologia di scheda: un secondo pacchetto sotto `dottping/` |
| ⏸ | Filtro per comune/provincia | Il form li accetta già, il modulo per ora non li usa |
| ⏸ | Altre regioni | Ogni regione ha un portale diverso: servirebbe un `source.py` per ciascuna, con la stessa interfaccia |
| ⏸ | Storico delle disponibilità | Oggi si tiene solo l'ultimo stato; servirebbe una tabella a parte |
| ⏸ | Statistiche d'uso | Quante chat, quanti medici seguiti |

---

## Problemi noti

| Problema | Impatto | Stato |
|---|---|---|
| Il portale risponde 403 agli IP datacenter | Il bot potrebbe non funzionare sul VPS | Mitigato con `curl_cffi`, da verificare sul posto |
| Il parser dipende dall'HTML del portale | Se cambia il template il bot smette di leggere i numeri | `check_portale.py` lo segnala; i selettori sono in un punto solo |
| Nessun limite di richieste per utente | Essendo pubblico, un utente potrebbe abusare di `/medico_check` | Per ora accettato: max 10 medici per chat |

---

## Decisioni prese (e perché)

- **Progetto separato da Nora.** Nora è un assistente personale; questo è un
  servizio a tema unico, pubblico, che va su un VPS con la sua vita. Tenerli
  insieme avrebbe legato il deploy di uno agli aggiornamenti dell'altro.
- **Niente Playwright.** Il flusso funziona con tre richieste HTTP normali:
  avviare un browser headless per leggere due numeri sarebbe stato sproporzionato
  e fragile.
- **`idLuogo` letto ogni volta, mai scritto nel codice.** È un identificativo
  interno del portale: leggerlo dall'elenco costa un POST e rende il bot immune
  a una sua rigenerazione.
- **Lista dei medici nel database, non nel `.env`.** Chi sorvegliare è una scelta
  dell'utente, che deve poter cambiare da chat senza riavviare il bot.
- **Notifica solo ai cambi di stato.** Un medico resta a 0 posti per settimane:
  interessa il momento della transizione, non il bollettino quotidiano.
- **Controllo al momento dell'iscrizione.** Registra lo stato di partenza, così
  il primo giro del job non annuncia uno zero che c'era già.
- **Due controlli al giorno.** È un servizio pubblico: bastano, e non pesano.
