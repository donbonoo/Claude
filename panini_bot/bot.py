"""
Bot di acquisto per panini.it (shop Magento).

Comandi:
    python bot.py login   -> apre il browser: fai login, metti il prodotto nel carrello,
                             controlla indirizzo e pagamento. Poi chiudi la finestra.
    python bot.py test    -> prova completa del checkout SENZA confermare l'ordine.
    python bot.py vai     -> aspetta l'orario di uscita e compra.

Tutte le impostazioni sono in config.json.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

CARTELLA = Path(__file__).resolve().parent
PROFILO = CARTELLA / "profilo_browser"
SCREENSHOT = CARTELLA / "screenshot"

# Selettori standard di Magento. Si possono sovrascrivere da config.json -> "selettori".
SELETTORI_DEFAULT = {
    "aggiungi_carrello": "#product-addtocart-button, button.tocart",
    "continua_spedizione": "#shipping-method-buttons-container button, button.continue, [data-role='opc-continue']",
    "metodo_spedizione": "#checkout-shipping-method-load input[type=radio], table.table-checkout-shipping-method input[type=radio]",
    "metodo_pagamento": "#checkout-payment-method-load input[type=radio][name='payment[method]']",
    "accettazioni": ".checkout-agreements input[type=checkbox], .checkout-agreement input[type=checkbox]",
    "effettua_ordine": ".payment-method._active button.checkout, button.action.primary.checkout",
    "messaggio_errore": ".message-error, .message.error, div.mage-error",
    "caricamento": ".loading-mask, .checkout-loader",
    "captcha": "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare'], #challenge-form, .g-recaptcha",
}

TESTO_SUCCESSO = re.compile(r"grazie per (il tuo|l'|il vostro) (acquisto|ordine)|numero (del tuo )?ordine", re.I)


# ----------------------------------------------------------------- utilità

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


def allarme(msg):
    log("*** " + msg + " ***")
    print("\a\a\a", end="", flush=True)


def carica_config():
    percorso = CARTELLA / "config.json"
    if not percorso.exists():
        sys.exit("Manca config.json: copia config.esempio.json in config.json e compilalo.")
    cfg = json.loads(percorso.read_text(encoding="utf-8"))
    cfg.setdefault("url_base", "https://www.panini.it/shp_ita_it/")
    if not cfg["url_base"].endswith("/"):
        cfg["url_base"] += "/"
    cfg.setdefault("secondi_anticipo", 3)
    cfg.setdefault("intervallo_ricarica", 2.0)
    cfg.setdefault("minuti_massimi", 15)
    cfg.setdefault("quantita", 1)
    cfg.setdefault("metodo_pagamento", "")
    cfg["sel"] = {**SELETTORI_DEFAULT, **cfg.get("selettori", {})}
    return cfg


def apri_browser(p):
    PROFILO.mkdir(exist_ok=True)
    opzioni = dict(
        user_data_dir=str(PROFILO),
        headless=False,
        locale="it-IT",
        timezone_id="Europe/Rome",
        viewport={"width": 1280, "height": 900},
    )
    import os
    if os.environ.get("CHROMIUM_PATH"):  # usato solo per i test
        opzioni["executable_path"] = os.environ["CHROMIUM_PATH"]
        opzioni["headless"] = os.environ.get("HEADLESS") == "1"
    ctx = p.chromium.launch_persistent_context(**opzioni)
    pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
    pagina.set_default_timeout(5000)
    return ctx, pagina


def visibile(pagina, selettore):
    try:
        loc = pagina.locator(selettore)
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    except PWError:
        pass
    return None


def screenshot(pagina, nome):
    SCREENSHOT.mkdir(exist_ok=True)
    f = SCREENSHOT / f"{datetime.now():%Y%m%d_%H%M%S}_{nome}.png"
    try:
        pagina.screenshot(path=str(f), full_page=True)
        log(f"Screenshot salvato: {f}")
    except PWError:
        pass


def scarto_orologio(pagina, cfg):
    """Differenza (secondi) tra l'orologio del server e quello del PC."""
    try:
        t0 = time.time()
        r = pagina.request.head(cfg["url_base"], timeout=10000)
        t1 = time.time()
        data = r.headers.get("date")
        if not data:
            return 0.0
        server = parsedate_to_datetime(data).timestamp() + 0.5  # l'header ha risoluzione di 1 s
        return server - (t0 + t1) / 2
    except Exception as e:  # noqa: BLE001
        log(f"Impossibile leggere l'ora del server ({e}), uso l'orologio del PC.")
        return 0.0


def attendi_orario(pagina, cfg):
    if not cfg.get("orario_uscita"):
        return
    uscita = datetime.strptime(cfg["orario_uscita"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Rome"))
    scarto = scarto_orologio(pagina, cfg)
    log(f"Scarto orologio PC/server: {scarto:+.2f} s")
    partenza = uscita.timestamp() - cfg["secondi_anticipo"] - scarto  # in ora del PC
    ultimo_ping = time.time()
    while True:
        mancano = partenza - time.time()
        if mancano <= 0:
            break
        if mancano > 60:
            log(f"Partenza tra {int(mancano // 60)} min {int(mancano % 60)} s")
            # tiene viva la sessione ogni ~4 minuti
            if time.time() - ultimo_ping > 240:
                try:
                    pagina.reload(wait_until="domcontentloaded")
                except PWError:
                    pass
                ultimo_ping = time.time()
            time.sleep(min(30, mancano - 55))
        else:
            time.sleep(min(0.2, mancano))
    log("VIA!")


# ----------------------------------------------------------------- azioni

def carrello_vuoto(pagina, cfg):
    pagina.goto(cfg["url_base"] + "checkout/cart/", wait_until="domcontentloaded")
    return visibile(pagina, ".cart-empty, .cart-empty-message") is not None or \
        visibile(pagina, "#shopping-cart-table, .cart.item, form#form-validate") is None


def aggiungi_al_carrello(pagina, cfg, scadenza):
    """Ricarica la pagina prodotto finché il pulsante non è attivo, poi aggiunge al carrello."""
    sel = cfg["sel"]
    while time.time() < scadenza:
        try:
            pagina.goto(cfg["url_prodotto"], wait_until="domcontentloaded")
            controlla_captcha(pagina, cfg)
            btn = visibile(pagina, sel["aggiungi_carrello"])
            if btn and btn.is_enabled():
                qta = visibile(pagina, "input#qty, input[name=qty]")
                if qta and cfg["quantita"] != 1:
                    qta.fill(str(cfg["quantita"]))
                btn.click()
                pagina.wait_for_timeout(1500)
                err = visibile(pagina, sel["messaggio_errore"])
                if err:
                    log("Errore aggiunta carrello: " + err.inner_text().strip()[:200])
                else:
                    log("Prodotto aggiunto al carrello.")
                    return True
            else:
                log("Pulsante 'Aggiungi al carrello' non ancora attivo, riprovo...")
        except PWError as e:
            log(f"Errore pagina prodotto: {str(e).splitlines()[0]}")
        time.sleep(cfg["intervallo_ricarica"])
    return False


def controlla_captcha(pagina, cfg):
    """Se compare un CAPTCHA o una coda, si ferma e lascia fare all'utente."""
    if visibile(pagina, cfg["sel"]["captcha"]) or "queue-it" in pagina.url:
        allarme("CAPTCHA o coda rilevata: completala tu nel browser, poi lo script riprende")
        while visibile(pagina, cfg["sel"]["captcha"]) or "queue-it" in pagina.url:
            time.sleep(1)
        log("Ripreso.")


def ordine_completato(pagina):
    if "success" in pagina.url:
        return True
    try:
        return bool(TESTO_SUCCESSO.search(pagina.locator("body").inner_text(timeout=1000)))
    except PWError:
        return False


def attendi_caricamento(pagina, cfg, max_s=15):
    fine = time.time() + max_s
    while time.time() < fine and visibile(pagina, cfg["sel"]["caricamento"]):
        time.sleep(0.1)


def scegli_pagamento(pagina, cfg):
    sel = cfg["sel"]
    nome = cfg["metodo_pagamento"].strip()
    if nome:
        etichetta = pagina.locator(".payment-method-title label, .payment-method label").filter(
            has_text=re.compile(re.escape(nome), re.I))
        if etichetta.count():
            radio_id = etichetta.first.get_attribute("for")
            radio = pagina.locator(f"#{radio_id}") if radio_id else None
            if radio is None or not radio.is_checked():
                etichetta.first.click()
                attendi_caricamento(pagina, cfg)
            return
        log(f"Metodo di pagamento '{nome}' non trovato, uso quello già selezionato.")
    # nessun metodo indicato: se nessuno è selezionato, prende il primo
    radio = pagina.locator(sel["metodo_pagamento"])
    if radio.count() and not any(radio.nth(i).is_checked() for i in range(radio.count())):
        radio.first.check(force=True)
        attendi_caricamento(pagina, cfg)


def spunta_accettazioni(pagina, cfg):
    caselle = pagina.locator(cfg["sel"]["accettazioni"])
    for i in range(caselle.count()):
        c = caselle.nth(i)
        try:
            if c.is_visible() and not c.is_checked():
                c.check()
        except PWError:
            pass


def checkout(pagina, cfg, conferma, scadenza):
    """
    Macchina a stati sulla pagina di checkout: a ogni giro guarda cosa c'è a schermo
    e fa il passo successivo. In caso di errore (es. prodotto non ancora disponibile)
    ricarica e riprova finché non scade il tempo.
    """
    sel = cfg["sel"]
    url_checkout = cfg["url_base"] + "checkout/"
    pagina.goto(url_checkout, wait_until="domcontentloaded")
    ordine_cliccato_alle = None

    while time.time() < scadenza:
        try:
            if ordine_completato(pagina):
                allarme("ORDINE COMPLETATO!")
                screenshot(pagina, "ordine_ok")
                return True

            controlla_captcha(pagina, cfg)

            # Redirect verso pagamento esterno (PayPal, 3D Secure, banca...)
            if ordine_cliccato_alle and cfg["url_base"].split("/")[2] not in pagina.url:
                allarme("Sei sulla pagina di pagamento esterna: conferma tu il pagamento (3D Secure / PayPal)")
                pagina.wait_for_url(re.compile(r"success"), timeout=10 * 60 * 1000)
                continue

            # Se Magento ci ha rimandato al carrello (es. prodotto non acquistabile)
            if "checkout/cart" in pagina.url or "checkout" not in pagina.url:
                err = visibile(pagina, sel["messaggio_errore"])
                log("Checkout non accessibile" + (": " + err.inner_text().strip()[:150] if err else "") + ", riprovo...")
                time.sleep(cfg["intervallo_ricarica"])
                pagina.goto(url_checkout, wait_until="domcontentloaded")
                continue

            attendi_caricamento(pagina, cfg)

            ordina = visibile(pagina, sel["effettua_ordine"])
            if ordina:
                scegli_pagamento(pagina, cfg)
                spunta_accettazioni(pagina, cfg)
                ordina = visibile(pagina, sel["effettua_ordine"])
                if ordina and ordina.is_enabled():
                    if not conferma:
                        screenshot(pagina, "test_pronto")
                        allarme("TEST OK: arrivato al pulsante finale 'Effettua ordine' (non cliccato)")
                        return True
                    log("Clic su 'Effettua l'ordine'")
                    ordina.click()
                    ordine_cliccato_alle = time.time()
                    attendi_caricamento(pagina, cfg, 30)
                    pagina.wait_for_timeout(500)
                    err = visibile(pagina, sel["messaggio_errore"])
                    if err and not ordine_completato(pagina):
                        log("Errore ordine: " + err.inner_text().strip()[:200])
                        ordine_cliccato_alle = None
                        time.sleep(cfg["intervallo_ricarica"])
                        pagina.goto(url_checkout, wait_until="domcontentloaded")
                    continue

            continua = visibile(pagina, sel["continua_spedizione"])
            if continua:
                radio = pagina.locator(sel["metodo_spedizione"])
                if radio.count() and not any(radio.nth(i).is_checked() for i in range(radio.count())):
                    radio.first.check(force=True)
                    attendi_caricamento(pagina, cfg)
                log("Spedizione -> Avanti")
                continua.click()
                attendi_caricamento(pagina, cfg)
                err = visibile(pagina, sel["messaggio_errore"])
                if err:
                    log("Errore spedizione: " + err.inner_text().strip()[:200])
                    time.sleep(cfg["intervallo_ricarica"])
                    pagina.goto(url_checkout, wait_until="domcontentloaded")
                continue

            time.sleep(0.15)  # la pagina sta ancora caricando i componenti
        except PWError as e:
            log(f"Errore: {str(e).splitlines()[0]} - ricarico")
            try:
                pagina.goto(url_checkout, wait_until="domcontentloaded")
            except PWError:
                time.sleep(cfg["intervallo_ricarica"])

    screenshot(pagina, "tempo_scaduto")
    allarme("Tempo massimo scaduto senza completare l'ordine")
    return False


# ----------------------------------------------------------------- comandi

def cmd_login(cfg):
    with sync_playwright() as p:
        ctx, pagina = apri_browser(p)
        pagina.goto(cfg["url_base"] + "customer/account/login/")
        print(
            "\n1) Fai il login\n"
            "2) Metti il prodotto nel carrello (se già possibile)\n"
            "3) Vai al checkout e controlla che indirizzo e metodo di pagamento siano salvati\n"
            "4) Chiudi la finestra del browser per salvare la sessione\n"
        )
        try:
            pagina.wait_for_event("close", timeout=0)
        except PWError:
            pass
        try:
            ctx.close()
        except PWError:
            pass
    log("Sessione salvata in " + str(PROFILO))


def cmd_acquista(cfg, conferma):
    with sync_playwright() as p:
        ctx, pagina = apri_browser(p)
        pagina.goto(cfg["url_base"] + "checkout/cart/", wait_until="domcontentloaded")
        if "login" in pagina.url:
            sys.exit("Non sei loggato: esegui prima 'python bot.py login'.")

        if conferma:
            attendi_orario(pagina, cfg)
        scadenza = time.time() + cfg["minuti_massimi"] * 60

        if carrello_vuoto(pagina, cfg):
            if not cfg.get("url_prodotto"):
                sys.exit("Il carrello è vuoto e in config.json manca 'url_prodotto'.")
            log("Carrello vuoto: provo ad aggiungere il prodotto.")
            if not aggiungi_al_carrello(pagina, cfg, scadenza):
                allarme("Non sono riuscito ad aggiungere il prodotto al carrello")
                input("Premi Invio per chiudere...")
                return
        else:
            log("Il carrello contiene già prodotti: vado al checkout.")

        ok = checkout(pagina, cfg, conferma, scadenza)
        if not ok or conferma:
            input("Premi Invio per chiudere il browser...")
        ctx.close()


def main():
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = carica_config()
    if comando == "login":
        cmd_login(cfg)
    elif comando == "test":
        cmd_acquista(cfg, conferma=False)
    elif comando == "vai":
        cmd_acquista(cfg, conferma=True)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
