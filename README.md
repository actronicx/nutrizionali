# NutriProntuario

Web app per cercare **prodotti per nutrizione enterale, parenterale e dispositivi medici** in commercio in Italia, filtrabili per **patologia** (es. cercando *disfagia* compaiono addensanti, acque gelificate, consistenze modificate, sonde e kit per nutrizione enterale).

Interfaccia minimale, ispirata a un motore di ricerca: una barra di ricerca, suggerimenti mentre si scrive, schede per tipologia, filtri per categoria e un riquadro riassuntivo della patologia.

## Fonti dati (ufficiali, pubbliche e gratuite)

| Sezione | Fonte | Aggiornamento della fonte |
|---|---|---|
| Parenterale (ATC B05BA, B05XC, B05XA31) | [AIFA – elenco confezioni farmaci](https://www.aifa.gov.it/dati-aperti) + liste classe A/H per classe e prezzo | continuo |
| Enterale / AFMS | [Ministero della Salute – Registro nazionale alimenti a fini medici speciali](https://www.salute.gov.it/new/it/tema/alimenti-fini-medici-speciali-ed-integratori/registro-nazionale-alimenti-fini-medici-speciali/) | mensile |
| Dispositivi (sonde NG/PEG/PEJ, kit, siringhe, sacche, nutripompe…) | [Ministero della Salute – Repertorio Dispositivi Medici (open data)](https://www.dati.salute.gov.it/it/dataset/dispositivi-medici/) | giornaliero |

Dei dispositivi vengono tenuti solo quelli iscritti al repertorio con data di fine commercio e di fine validità non ancora passate.

**Perché non Codifa / dica33.** dica33.it ripubblica dati Codifa, che sono a pagamento e protetti da diritti: prenderli da lì sarebbe fragile e a rischio legale. Copre inoltre solo i farmaci (le soluzioni parenterali), non gli AFMS né i dispositivi. Le fonti qui sopra sono quelle ufficiali da cui derivano anche le banche dati commerciali.

## Associazione prodotto → patologia

Nessuna fonte pubblica riporta le indicazioni cliniche in forma strutturata. Le associazioni sono quindi generate da **regole modificabili** in [`etl/patologie.json`](etl/patologie.json):

- `nome`: espressioni regolari sul nome del prodotto (es. `addens`, `thick`, `gelific` → Disfagia)
- `categoria`: categoria del prodotto AFMS (es. `addensanti`)
- `cnd`: prefissi della classificazione CND dei dispositivi (es. `G02020201` = sonde PEG)
- `atc`: prefissi ATC dei farmaci parenterali
- `escludi_categoria`: categorie da escludere
- `manuali`: associazioni puntuali, `{"<id prodotto>": ["disfagia", ...]}`, per correggere singoli casi

I prodotti riconosciuti dal nome sono mostrati per primi. Dopo ogni modifica alle regole, rilanciare l'aggiornamento.

> ⚠️ Le associazioni sono indicative e non sostituiscono la scheda tecnica né la valutazione clinica. Prima di un uso clinico è consigliata una revisione da parte di un dietista o di un farmacista.

## Verifica dei prodotti su scheda tecnica

Ogni prodotto ha uno stato visibile nell'app:

| Stato | Significato |
|---|---|
| ✓ **Verificato** | Le indicazioni sono state confrontate con la scheda tecnica o la pagina ufficiale. Nel dettaglio compaiono la **frase citata testualmente**, il link alla fonte, il tipo di fonte (produttore o rivenditore), la data e le eventuali correzioni rispetto all'assegnazione automatica. |
| ⚠ **Scheda non trovata** | Nessuna fonte affidabile trovata online (la nota spiega cosa è stato cercato). Restano le indicazioni automatiche. |
| ⚠ **Fonte cambiata** | Il ricontrollo settimanale non trova più la frase citata nella fonte: la verifica decade finché non viene ripetuta. |
| ⚠ **Da verificare** | Prodotto non ancora esaminato. |
| **Classe CND** | Dispositivi medici: l'indicazione deriva dalla classificazione nazionale CND, non dal singolo prodotto. |

Strumenti:

- `etl/verifica.py estrai URL` estrae da una pagina o da un PDF le frasi sulle indicazioni.
- `etl/registra_verifica.py lotto.json` registra gli esiti in `etl/verifiche.json` e **rifiuta ogni citazione che non compare testualmente nella fonte**, così nessuna citazione può essere parafrasata o inventata.
- `etl/verifica.py ricontrolla` (gira ogni settimana nel workflow) riscarica le fonti e segnala le citazioni sparite.

Regole adottate: si preferisce la fonte del produttore; una fonte di un rivenditore è accettata solo se riporta l'indicazione del prodotto ed è segnalata come fonte secondaria. Una fonte relativa a un altro gusto della stessa linea è accettata e annotata. Se il nome trovato non coincide con quello del registro, il prodotto **non** viene dato per verificato.

Le verifiche attuali sono state eseguite da Claude (IA) confrontando il testo delle fonti. Per un uso clinico è raccomandata una revisione a campione da parte di un dietista o di un farmacista.

## Struttura

```
etl/update_data.py     scarica le 3 fonti, normalizza, assegna categorie e patologie
etl/patologie.json     regole patologie (modificabili)
etl/verifiche.json     verifiche su scheda tecnica (citazione, fonte, esito)
etl/verifica.py        estrazione frasi dalle fonti e ricontrollo settimanale
etl/registra_verifica.py  registrazione verifiche con controllo della citazione
web/index.html         app (HTML/CSS/JS statici, nessun server necessario)
web/data/products.json banca dati generata
.github/workflows/     aggiornamento automatico settimanale + pubblicazione su GitHub Pages
```

## Uso in locale

```bash
pip install -r etl/requirements.txt
```

```bash
python -m playwright install chromium
```

```bash
python etl/update_data.py
```

```bash
python -m http.server 8000 --directory web
```

Poi aprire http://localhost:8000. Con `--cache DIR` i file scaricati vengono salvati e riutilizzati.

Il sito del Ministero (salute.gov.it) usa una protezione anti-bot che richiede JavaScript: per questo il PDF del registro AFMS viene scaricato tramite Chromium headless (Playwright). Se una fonte non risponde, lo script mantiene i dati della versione precedente per quella sezione e lo segnala.

## Aggiornamento periodico e pubblicazione

Il workflow `.github/workflows/aggiorna-dati.yml` ogni lunedì:
1. rigenera `web/data/products.json` dalle fonti ufficiali;
2. fa il commit dei dati se sono cambiati;
3. pubblica la cartella `web/` su GitHub Pages.

Per attivarlo: creare un repository su GitHub, caricare il progetto e in *Settings → Pages* scegliere **Source: GitHub Actions**. Si può lanciare a mano da *Actions → Aggiorna banca dati → Run workflow*.
