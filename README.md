# DottPing

Bot Telegram che sorveglia i **posti liberi dai medici di base** sul portale della
Regione Veneto e avvisa quando se ne libera uno.

Il numero che conta è «Disponibilità assistiti illimitati»: finché è 0 non si può
fare niente, quando diventa diverso da 0 si può presentare domanda di cambio
medico. Guardarlo a mano ogni giorno è la cosa che ci si dimentica di fare —
da qui il bot.

Nato come modulo del bot personale Nora, staccato in un progetto a sé.

---

## Comandi

```
/medico          posti liberi di un medico, subito
/medico_on       sorveglia un medico in questa chat
/medico_lista    chi sto sorvegliando, con l'ultimo stato letto
/medico_off      elenco con i bottoni per togliere
/medico_check    forza subito un controllo
/status          stato del bot e prossimo controllo
/supporta        offri un caffè a DottPing
```

`/id` esiste ancora (dice il tuo id Telegram, serve per `ALLOWED_USER_IDS`) ma
non compare né nel menu né in `/help`: a chi cerca un medico non interessa.

**Nessun comando vuole parametri.** Il cognome non si scrive attaccato al
comando: il bot lo chiede («Dimmi il nome del medico da sorvegliare») e legge la
risposta come un messaggio normale. Chi preferisce può comunque scrivere
`/medico_on <cognome>` su una riga sola, continua a funzionare.

Dove serve una scelta ci sono i bottoni: la scheda di un medico ha
«🔔 Avvisami quando si libera un posto», e `/medico_off` elenca i sorvegliati da
togliere. Se un cognome dà più risultati, i medici trovati compaiono come
bottoni.

> In un **gruppo** Telegram consegna al bot solo i comandi, a meno che la privacy
> del bot sia disattivata (BotFather → `/setprivacy` → Disable). Senza quello, in
> gruppo le risposte libere non arrivano e serve la forma `/medico_on <cognome>`.
> In chat privata funziona sempre.

Il bot è **aperto a tutti**: ogni chat ha la propria lista di medici, quindi più
persone possono usarlo senza interferire.

---

## Avvio

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
notepad .env            # incolla il token di @BotFather

python main.py
```

Serve un **token dedicato**: due processi con lo stesso token si rubano i
messaggi a vicenda, quindi non riusare quello di un altro bot.

Prima di fidarti della parte di rete su una macchina nuova:

```powershell
python tools\check_portale.py rossi
```

## Test

```powershell
python tests\test_parser.py     # i parser reggono il markup del portale
python tests\test_smoke.py      # storage, comandi, job, messaggi — tutto offline
python tests\test_sicurezza.py  # validazione degli input e freni anti-abuso
```

---

## Come arriva ai dati

Il servizio della Regione è una portlet Liferay che tiene lo stato **in
sessione**: la scheda di un medico non ha un URL proprio, quello che si vede
nella barra degli indirizzi non è riutilizzabile. Il bot rifà gli stessi tre
passaggi di una persona, riusando i cookie:

1. `GET` della pagina di ricerca → apre la sessione
2. `POST` del form con il cognome → pagina con l'elenco dei medici
3. `POST` con `idLuogo` → scheda con la tabella delle disponibilità

`idLuogo` (es. `009249`) **non è scritto nel codice**: viene
letto ogni volta dall'elenco, dentro `onclick="submitLuogo('...')"`. Se il
portale rigenerasse gli id, il bot continuerebbe a funzionare.

### Il 403

Il portale filtra le richieste che non sembrano venire da un browser, e parecchi
IP di datacenter. Due contromisure in `dottping/net.py`:

1. header completi da Chrome (spesso basta);
2. se arriva comunque 403/429/503, ripiego su **curl_cffi**, che replica anche il
   TLS fingerprint di Chrome.

Da casa passa quasi sempre la via 1. **Dal VPS potrebbe servire la 2**: lancia
`tools/check_portale.py` sul VPS prima di dare il deploy per buono.

---

## Quando arriva la notifica

Solo **ai cambi di stato**, non a ogni controllo:

- `0 → N` → «🟢 POSTI DISPONIBILI»
- `N → 0` → «🔴 Posti esauriti»
- nessun cambio → nessun messaggio

Lo stato precedente sta nella tabella `kv`, quindi un riavvio non provoca una
notifica doppia. Quando aggiungi un medico il bot fa subito un controllo, così lo
stato di partenza è registrato e il primo giro non annuncia uno zero che c'era già.

Se più chat seguono lo stesso medico, il portale viene interrogato **una volta
sola** per giro e il messaggio parte verso tutte: le richieste crescono col
numero di medici, non col numero di utenti.

### I due numeri

- **Assistiti illimitati** — l'iscrizione normale, senza scadenza. È il numero
  sorvegliato: se è diverso da 0 si può presentare domanda di cambio medico.
- **Assistiti a termine** — posti temporanei (domicilio provvisorio, studenti
  fuori sede), con una data di scadenza.

La spiegazione compare in fondo a ogni messaggio.

---

## Struttura

```
DottPing_bot/
├── main.py                 avvio
├── dottping/
│   ├── config.py           .env → Settings
│   ├── bot.py              Application + registro moduli
│   ├── storage.py          SQLite (medici sorvegliati, ultimo stato)
│   ├── net.py              HTTP con header browser + ripiego anti-WAF
│   ├── textfmt.py          escaping HTML e taglio messaggi
│   ├── guard.py            whitelist utenti + freno anti-flood
│   ├── freni.py            limiti per utente e globali, cache del portale
│   ├── wait.py             "sto aspettando una risposta" (comandi senza parametri)
│   ├── supporto.py         /supporta e il bottone delle offerte
│   ├── core.py             /start /help /id /status
│   └── medici/
│       ├── source.py       flusso a 3 passaggi + parser
│       ├── handlers.py     i comandi e i bottoni
│       └── jobs.py         controllo periodico e notifiche
├── tools/check_portale.py  diagnostica pre-deploy
├── tools/db_peek.py        ispezione del database
├── tests/                  parser + smoke test
├── doc/                    tracking e architettura
└── deploy/dottping.service unit systemd
```

---

## Deploy sul VPS

```bash
sudo useradd -r -m -d /opt/dottping dottping
sudo -u dottping git clone <repo> /opt/dottping      # oppure copia la cartella
cd /opt/dottping
sudo -u dottping python3 -m venv .venv
sudo -u dottping .venv/bin/pip install -r requirements.txt
sudo -u dottping cp .env.example .env && sudo -u dottping nano .env

sudo -u dottping .venv/bin/python tools/check_portale.py rossi   # verifica il 403

sudo cp deploy/dottping.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now dottping
journalctl -u dottping -f
```

---

## Freni anti-abuso

Il bot è aperto a tutti, il portale della Regione no: ogni ricerca sono tre
richieste fatte dall'IP del server. Senza limiti basterebbe un utente molesto
per far sembrare il server uno scraper e farlo bloccare. I freni stanno in
`dottping/freni.py`, i numeri nel `.env`:

| Limite | Default | Cosa impedisce |
|---|---|---|
| `MAX_COMANDI_MIN` | 20/min per utente | flood di messaggi al bot |
| `MAX_RICERCHE_MIN` | 8/min per utente | usare il bot per martellare il portale |
| `MAX_RICERCHE_ORA` | 60/ora per utente | la stessa cosa, con più pazienza |
| `MAX_PORTALE_MIN` | 30/min in totale | il traffico complessivo in uscita |
| `MAX_MEDICI_TOTALI` | 50 medici distinti | che i controlli automatici diventino uno scraping |
| `MAX_SORVEGLIATI` | 3 per chat | che una chat si prenda tutto lo spazio |
| `CACHE_PORTALE_S` | 120 s | rifare la stessa domanda al portale |
| `PORTALE_PARALLELI` | 2 | raffiche di connessioni contemporanee |

Chi supera il limite viene avvisato **una volta** e poi ignorato finché non
rallenta: rispondere a ogni messaggio di un flood significa floodare con lui.
I nomi cercati vengono validati prima di partire (solo lettere, spazi,
apostrofi e trattini, 2-40 caratteri), e gli errori del portale arrivano in
chat come messaggio generico — il dettaglio resta nel log.

`/medico_check` ricontrolla **solo i medici della chat che lo chiede**: prima
faceva ripartire il giro su tutte le chat, ed era il modo più comodo per far
uscire tante richieste con un solo comando.

---

## Le offerte

`dottping/supporto.py`, un modulo a sé come gli altri. L'indirizzo sta nel
`.env`:

```
SUPPORTO_URL=https://ko-fi.com/skymemory
```

Deve essere **https**, altrimenti viene scartato e il bottone sparisce: chi
tocca un bottone di un bot si fida del bot, e un indirizzo qualsiasi lì dentro
manderebbe la gente dove capita. Chi riusa questo codice, svuotando la riga
toglie la richiesta del tutto.

Il cappello si passa in due posti soli: quando qualcuno scrive `/supporta`, e
sotto la notifica «🟢 POSTI DISPONIBILI» — l'unico momento in cui il bot ha
davvero risolto un problema. Sulla notifica di posti esauriti no: nessuno ha
voglia di offrire caffè per una brutta notizia.

---

## Nota di cortesia

Il portale è un servizio pubblico: due controlli al giorno per medico bastano e
non pesano. Non aumentare la frequenza senza un motivo reale.
