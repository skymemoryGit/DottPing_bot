# CLAUDE.md — istruzioni per chi lavora su DottPing

Primo file da leggere. Poi `doc/TRACKING.md`, e se serve `doc/ARCHITETTURA.md`.

---

## Il progetto

**DottPing** è un bot Telegram che sorveglia i posti liberi dai medici di base
sul portale della Regione Veneto e avvisa quando se ne libera uno.

È nato come modulo del bot personale **Nora** ed è stato staccato per restare
indipendente: gira da solo, con il suo token e il suo database, e verrà messo su
un VPS. Non deve dipendere da Nora in nessun modo.

Il bot è **pubblico**: chiunque può usarlo, ogni chat ha la sua lista di medici.

## Lingua

Jiancheng scrive in italiano: **rispondi in italiano**. Commenti e documentazione
in italiano, nomi di funzioni e variabili in inglese.

---

## Regole di lavoro

1. **Prima di iniziare** leggi `doc/TRACKING.md`.
2. **Alla fine di ogni sessione** aggiornalo: una riga in "Fatto" per ogni cosa
   conclusa, e le decisioni di struttura con la loro motivazione.
3. **Spiega il perché, non il cosa.** Questo codice è anche materiale didattico.
4. Modifiche piccole e spiegate, non rifacimenti.

---

## Dove mettere le cose

| Se stai scrivendo… | Va in… |
|---|---|
| Logica di ricerca/parsing del portale | `dottping/medici/source.py` |
| Comandi e bottoni | `dottping/medici/handlers.py` |
| Controllo periodico e notifiche | `dottping/medici/jobs.py` |
| SQL, qualunque esso sia | `dottping/storage.py` — **solo lì** |
| Richieste HTTP verso l'esterno | passano da `dottping/net.py` |
| Formattazione dei messaggi | `dottping/textfmt.py` |
| Una funzionalità nuova (es. pediatri, altra regione) | un pacchetto nuovo sotto `dottping/`, con la sua `register(app, ctx)`, aggiunto a `MODULES` in `bot.py` |

---

## Cose da non fare

- ❌ Segreti nel codice: il token sta nel `.env` (in `.gitignore`).
- ❌ Committare `.env`, `data/`, `*.log`.
- ❌ `parse_mode=MarkdownV2`: il progetto usa **HTML** (MarkdownV2 vuole l'escape
  di 18 caratteri, i nomi dei medici lo romperebbero).
- ❌ Interpolare testo del portale senza `esc()` di `textfmt.py`.
- ❌ Aumentare la frequenza dei controlli senza motivo: è un servizio pubblico,
  due volte al giorno bastano.
- ❌ Scrivere `idLuogo` nel codice: si legge ogni volta dall'elenco.
- ❌ Chiamare librerie sincrone lente senza `asyncio.to_thread`.
- ❌ Playwright/Selenium: il flusso funziona con richieste HTTP normali.
- ❌ Reintrodurre dipendenze da Nora: i due progetti sono separati per scelta.

---

## Comandi

```powershell
.\.venv\Scripts\python.exe main.py

.\.venv\Scripts\python.exe tests\test_parser.py
.\.venv\Scripts\python.exe tests\test_smoke.py

.\.venv\Scripts\python.exe tools\check_portale.py rossi
.\.venv\Scripts\python.exe tools\db_peek.py
```

---

## Trappole note

- **Il portale tiene lo stato in sessione.** La scheda di un medico non ha un URL
  proprio: vanno rifatti i tre passaggi (GET ricerca → POST cognome → POST
  idLuogo) riusando i cookie. Non provare a salvare un link diretto.
- **403 dagli IP datacenter.** `net.py` manda header da browser e ripiega su
  `curl_cffi`. Su una macchina nuova verifica con `tools/check_portale.py`.
- **Un solo processo per token.** Due istanze si rubano gli update.
- **Il parser dipende dall'HTML di un sito terzo.** Se smette di trovare i
  numeri, il primo sospetto è il template cambiato: `check_portale.py` lo dice, e
  i selettori stanno in `dottping/medici/source.py::parse_dettaglio`.
- **Console Windows**: i log con accenti escono con `?`. Encoding della console,
  non del codice.

---

## Prima di dire "fatto"

- [ ] `tests\test_parser.py` e `tests\test_smoke.py` passano
- [ ] Nessun segreto nel codice
- [ ] `doc/TRACKING.md` aggiornato
