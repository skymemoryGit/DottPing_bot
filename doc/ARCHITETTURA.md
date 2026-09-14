# DottPing — Architettura

Com'è fatto il bot e **perché** è fatto così.

---

## 1. L'idea in una frase

> Il codice che fa funzionare il bot è separato dal codice che sa parlare col
> portale della Regione.

`dottping/` contiene i servizi (Telegram, database, HTTP, configurazione).
`dottping/medici/` contiene l'unica funzionalità. I servizi non sanno cosa sia un
medico; la funzionalità non sa come si apre un database.

Il bot oggi fa una cosa sola, ma la struttura resta a moduli: aggiungere i
pediatri o un'altra regione significa creare un pacchetto con la sua
`register(app, ctx)`, senza toccare nient'altro.

---

## 2. Mappa

```
DottPing_bot/
├── main.py                  ⚡ l'unico file che si lancia
├── .env                     🔑 token e impostazioni (MAI su git)
│
├── dottping/                🏗  INFRASTRUTTURA
│   ├── config.py                .env  →  oggetto Settings
│   ├── bot.py                   costruisce l'Application, registra i moduli
│   ├── storage.py               SQLite: chi sorveglia cosa, ultimo stato
│   ├── net.py                   richieste HTTP + ripiego anti-WAF
│   ├── textfmt.py               escape HTML, taglio dei messaggi
│   ├── guard.py                 whitelist (facoltativa: il bot è pubblico)
│   ├── core.py                  /start /help /id /status
│   │
│   └── medici/              🧩 LA FUNZIONALITÀ
│       ├── source.py            flusso a 3 passaggi + parser
│       ├── handlers.py          comandi e bottoni
│       └── jobs.py              controllo periodico, confronto, notifiche
│
├── data/dottping.db         💾 creato da solo (MAI su git)
├── tests/ tools/ doc/ deploy/
```

---

## 3. Il giro di avvio

```
main.py
  ├─ load_settings()            config.py: .env → Settings (token, orari, ...)
  ├─ logging.basicConfig()
  ├─ build_application()        bot.py
  │     ├─ BotContext = { settings, storage }      ← lo "zaino" condiviso
  │     ├─ Application.builder().token(...).build()
  │     ├─ app.bot_data["ctx"] = contesto
  │     └─ per ogni modulo in MODULES: register(app, ctx)
  │            core      → /start /help /id /status
  │            medici    → /medico ... + pianifica i controlli
  └─ run_polling()              resta in ascolto
```

`MODULES` in `bot.py` è una lista di stringhe; `import_module` importa il modulo
dal nome e chiama la sua `register`. Ogni modulo **dichiara da sé** i propri
comandi: `bot.py` non sa che esiste `/medico`.

---

## 4. Il giro di un comando

`/medico rossi`:

```
@guarded                        guard.py: la whitelist, se attiva
   │
medico_command()                medici/handlers.py
   ├─ risponde "⏳ Interrogo il portale…"        feedback immediato
   ├─ cerca_disponibilita(cognome)                medici/source.py
   │     └─ asyncio.to_thread(_flusso)            3 richieste, sessione condivisa
   │            ├─ GET  pagina ricerca            → cookie
   │            ├─ POST cognome                   → elenco  → parse_risultati()
   │            └─ POST idLuogo                   → scheda  → parse_dettaglio()
   ├─ formatta_scheda(d)                          medici/jobs.py
   └─ edit_text(risultato)                        sostituisce il "⏳"
```

Tre scelte deliberate:

1. **`source.py` non sa che esiste Telegram.** Restituisce oggetti
   `Disponibilita`. È testabile da solo, ed è infatti il pezzo coperto dai test.
2. **Il flusso è sincrono dentro un thread.** Le tre richieste vanno per forza in
   ordine; `asyncio.to_thread` evita di bloccare il bot mentre aspettano.
3. **L'errore viaggia come eccezione.** `net.py` alza `FetchError`, `source.py`
   non la tocca, `handlers.py` la trasforma in un messaggio leggibile. Si cattura
   dove si sa cosa farne.

---

## 5. Il giro del controllo periodico

```
register() all'avvio:
    per ogni ora in CHECK_HOURS: job_queue.run_daily(job_periodico, 08:05 / 20:05)
        ↓
job_periodico → controlla_tutti()                 medici/jobs.py
    ├─ storage.medico_watch_list()                 tutte le righe, di tutte le chat
    ├─ raggruppa per idLuogo                       stesso medico = una sola richiesta
    │
    └─ per ogni medico:
         ├─ cerca_disponibilita(cognome, nome, idLuogo)
         ├─ aggiorna_stato()                       confronta con kv, salva il nuovo
         └─ se lo stato è CAMBIATO:
                messaggio a tutte le chat di quel gruppo
                se una chat ha bloccato il bot → la si toglie dalla lista
```

Il raggruppamento è il motivo per cui il carico sul portale cresce col numero di
**medici**, non col numero di utenti.

---

## 6. Lo zaino condiviso

Gli handler vengono chiamati da python-telegram-bot con `update` e `context`.
Per dare loro database e configurazione senza variabili globali:

```python
@dataclass
class BotContext:
    settings: Settings
    storage: Storage

app.bot_data["ctx"] = BotContext(...)      # una volta sola, all'avvio
```

```python
app_ctx = context.application.bot_data["ctx"]
await app_ctx.storage.medico_watch_list(chat_id)
```

Si chiama *dependency injection*: le dipendenze non si prendono dall'aria, si
consegnano. Nel test si costruisce un contesto con un database temporaneo e le
stesse funzioni lavorano senza accorgersi della differenza.

---

## 7. Il database

Due tabelle, una domanda ciascuna.

```sql
medico_watch        -- "chi sorveglia cosa"
  chat_id     INTEGER
  id_luogo    TEXT        -- identificativo del medico sul portale
  cognome     TEXT        -- serve a rifare la ricerca a ogni controllo
  nome        TEXT
  nome_medico TEXT        -- per mostrarlo nei messaggi
  created_at  TEXT
  PRIMARY KEY (chat_id, id_luogo)      ← stessa chat, stesso medico: una riga sola

kv                  -- "com'era l'ultima volta"
  key         TEXT   -- "medico:stato:009249"
  value       TEXT   -- {"valore": 0, "illimitati": 0, "a_termine": 7, ...}
  updated_at  TEXT
```

`kv` è indicizzata sul **medico**, non sulla chat: lo stato del portale è uno
solo, indipendente da chi lo guarda. È ciò che permette di interrogare una volta
e notificare molti.

Perché SQLite e non un file JSON: serve che lo stato sopravviva al riavvio e che
due scritture insieme (il job delle 08:05 e un `/medico_check`) non si pestino.
SQLite fa entrambe le cose ed è già dentro Python.

Per guardarci dentro: `python tools/db_peek.py`.

---

## 8. Il parsing, e come accorgersi che si è rotto

I due parser stanno in `source.py` e sono funzioni pure: HTML in, oggetti out.

- `parse_risultati()` cerca `span.poi[onclick]` ed estrae `idLuogo` dalla
  chiamata `submitLuogo('...')`.
- `parse_dettaglio()` cerca la `table.results` che contiene la parola
  "disponibilit", e legge **tutte** le righe in un dizionario — non solo le due
  attese. Se il portale ne aggiungesse una terza, finirebbe in `voci` invece di
  far saltare il parsing.

Se la tabella non c'è, `parse_dettaglio` **alza `FetchError`** invece di
restituire zeri: uno zero finto sarebbe peggio di un errore, perché il bot
resterebbe zitto per sempre credendo che non ci siano posti.

`tools/check_portale.py` esiste per questo: dice in tre righe se la rete passa e
se i selettori trovano ancora qualcosa.

---

## 9. Le regole da portarsi via

1. **I segreti stanno nella configurazione, non nel codice.**
2. **Una funzione, una responsabilità.** Se nel descriverla dici "e poi", sono due.
3. **Chi prende i dati non li formatta.** È ciò che rende un pezzo testabile senza rete.
4. **Gli errori si catturano dove si sa cosa farne.**
5. **Meglio un errore rumoroso di un dato finto.** Uno zero inventato è un bug silenzioso.
