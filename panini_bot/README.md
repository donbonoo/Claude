# Bot di acquisto per panini.it

Compra un prodotto su panini.it appena scade il timer. Usa il **tuo** browser e il **tuo** account:
fa al posto tuo i click del checkout, solo più velocemente.

## Installazione (una volta sola)

Serve Python 3.9 o successivo ([python.org](https://www.python.org/downloads/); su Windows spunta *"Add Python to PATH"*).
Apri un terminale nella cartella `panini_bot` ed esegui:

```
pip install -r requirements.txt
python -m playwright install chromium
```

Poi copia `config.esempio.json` in `config.json` e compilalo (vedi sotto).

## Uso

1. **Login e preparazione:** `python bot.py login`
   Si apre un browser. Fai login, **metti il prodotto nel carrello**, vai al checkout e controlla che
   indirizzo e metodo di spedizione/pagamento siano corretti. Poi **chiudi la finestra**: la sessione resta salvata.
2. **Prova generale:** `python bot.py test`
   Fa tutto il checkout ma **si ferma prima** di "Effettua l'ordine" e salva uno screenshot.
   Se il prodotto non è ancora acquistabile continua a riprovare: in quel caso chiudilo con Ctrl+C,
   ha già dimostrato che login e carrello funzionano.
3. **Il giorno dell'uscita:** `python bot.py vai` (avvialo qualche minuto prima, con il PC che non va in standby).
   Aspetta l'orario (sincronizzato sull'orologio del server Panini), poi tenta il checkout
   ogni `intervallo_ricarica` secondi finché l'ordine non va a buon fine o passano `minuti_massimi`.

## config.json

| Campo | Significato |
|---|---|
| `url_prodotto` | Link alla pagina del prodotto (serve solo se il carrello è vuoto) |
| `orario_uscita` | Ora italiana di uscita, formato `AAAA-MM-GG HH:MM:SS` |
| `secondi_anticipo` | Quanti secondi prima dell'orario iniziare a provare |
| `intervallo_ricarica` | Secondi tra un tentativo e l'altro. Non scendere sotto 1: rischi il blocco |
| `minuti_massimi` | Dopo quanto tempo rinunciare |
| `quantita` | Quantità da aggiungere al carrello (se lo fa il bot) |
| `metodo_pagamento` | Testo (anche parziale) del metodo di pagamento come appare nel checkout; vuoto = quello già selezionato |
| `selettori` | Per sovrascrivere i selettori CSS se il sito è diverso dal Magento standard (vedi `SELETTORI_DEFAULT` in `bot.py`) |

## Cosa NON fa (di proposito)

- **CAPTCHA, coda virtuale, verifiche anti-bot:** il bot si ferma, suona e ti lascia completare a mano. Poi riprende da solo.
- **3D Secure / PayPal:** quando arrivi alla pagina della banca o di PayPal, il bot ti avvisa e aspetta che confermi tu.
  Una carta di credito salvata, senza redirect, è il metodo più rapido.

## Note

- I termini d'uso dei negozi spesso vietano gli acquisti automatizzati: l'ordine può essere annullato o l'account limitato.
- Lo script è stato provato su un checkout Magento simulato, non sul sito reale (che potrebbe avere pulsanti con nomi diversi).
  Per questo la **prova `test` è fondamentale**: se si blocca su un passaggio, mandami lo screenshot o il messaggio d'errore.
- La cartella `profilo_browser/` contiene la tua sessione di login: non condividerla.
