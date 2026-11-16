#!/usr/bin/env python3
"""
Aggiorna la banca dati di NutriProntuario da fonti pubbliche ufficiali:

  1. AIFA  - Elenco confezioni farmaci (open data)         -> nutrizione PARENTERALE
  2. Ministero della Salute - Registro nazionale AFMS (PDF) -> nutrizione ENTERALE / AFMS
  3. Ministero della Salute - Repertorio Dispositivi Medici -> DISPOSITIVI per NE/NP

Output: web/data/products.json (+ web/data/meta.json)

Uso:
    python etl/update_data.py                 # scarica tutto e rigenera
    python etl/update_data.py --cache DIR     # riusa i file già scaricati in DIR
"""
import argparse, csv, datetime as dt, io, json, re, sys, unicodedata, zipfile
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "data"
RULES = json.loads((ROOT / "etl" / "patologie.json").read_text(encoding="utf-8"))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

AIFA_CONFEZIONI = "https://drive.aifa.gov.it/farmaci/confezioni.csv"
AIFA_LISTE_PAGE = "https://www.aifa.gov.it/liste-farmaci-a-h"
AFMS_PAGE = "https://www.salute.gov.it/new/it/tema/alimenti-fini-medici-speciali-ed-integratori/registro-nazionale-alimenti-fini-medici-speciali/"
RDM_PAGE = "https://www.dati.salute.gov.it/it/dataset/dispositivi-medici/"

# ATC per la nutrizione parenterale
ATC_PARENTERALE = {
    "B05BA01": "Aminoacidi", "B05BA02": "Emulsioni lipidiche", "B05BA03": "Carboidrati (glucosio)",
    "B05BA04": "Idrolisati proteici", "B05BA10": "Sacche multicamera / combinazioni",
    "B05XC": "Vitamine per uso parenterale", "B05XA31": "Oligoelementi / elettroliti in associazione",
}
# CND dei dispositivi per nutrizione enterale/parenterale
CND_DISPOSITIVI = {
    "G02020101": "Sonde naso-gastriche", "G02020102": "Sonde naso-duodeno-digiunali",
    "G02020199": "Sonde naso-gastro-intestinali - altre", "G02020201": "Sonde e kit per gastrostomia (PEG)",
    "G02020202": "Sonde e kit per digiunostomia (PEJ)", "G020299": "Sonde per alimentazione - altre",
    "A030403": "Kit / set per nutrizione enterale", "A03010399": "Deflussori per nutrizione enterale",
    "A020108": "Siringhe per nutrizione enterale", "A080101": "Sacche per nutrizione enterale",
    "A080102": "Sacche per nutrizione parenterale", "A080199": "Sacche per nutrizione - altre",
    "A0880": "Sacche nutrizione/infusione - accessori", "A088099": "Sacche nutrizione/infusione - accessori",
    "A0899": "Sacche nutrizione/infusione - altre", "Z12030303": "Nutripompe",
    "V0203": "Dispositivi per la nutrizione del neonato", "V0280": "Dispositivi per la nutrizione del neonato - accessori",
}

CATEGORIE = {
    "addensanti": "Addensanti, acque gelificate e consistenze modificate",
    "ipoproteici": "Alimenti ipoproteici / aproteici",
    "reidratanti": "Soluzioni reidratanti orali",
    "metabolici": "Prodotti per malattie metaboliche",
    "formule_infanzia": "Formule speciali per l'infanzia",
    "moduli": "Moduli nutrizionali (calorici / proteici / lipidici)",
    "formule_enterali": "Formule per nutrizione enterale (sonda)",
    "ons": "Supplementi nutrizionali orali (ONS)",
    "altri_afms": "Altri alimenti a fini medici speciali",
}
CAT_REGEX = [
    ("metabolici", r"afenil|\bpku\b|msud|anamix|lophlex|\bxp\b|\bzero\b|\btvmi|\bvil\b|phlexy|glytactin|easiphen|\btyr|\bhcu|\bucd|\beaa\b|\bgmp\b|keto|k\.? ?vita|dkx|\bx ?(phe|met|mtvi|lys|leu|cys|ptyr)|xmtvi|maxamaid|maxamum|antifen|harifen|\bga\b|\biva\b|vitaflo - (hcu|tyr|msud|pku|gel)|\bhom\b|\bmma|\bleu\b|milupa|sineamin|tylactin|phenylalanin|glycin\d|plus8|\blnaa|\bmsud|\bpa\b|\btvmi|\bilv\b"),
    ("ipoproteici", r"aprot|apro|ipoprot|low ?protein|loprofin|\bamino'|promin|taranis|my snack|bonami|eggz|\bbiscott|\bpasta\b|\bpane\b|piadin|grissin|crostat|\bfette\b|\bsnack"),
    ("reidratanti", r"reid|idra(?!ta)|dral\b|hydra(?!fruit)|\bors\b|reidrat|idraton|neoidra"),
    ("addensanti", r"addens|thick|gelific|acquagel|aquasure|nutilis|(?<!soft-)\bgel\b|resource instant|acqua gel|hydrafruit|dysph|disfag|nutridens|livello [1-5]\b|gourmed|textur"),
    ("formule_infanzia", r"infatrini|neocate|nutramigen|alfar[eé]|pregestimil|alfamino|puramino|aptamil|allernova|alth[eé]ra|sinelac|\binfant|\blatte\b|\bbaby|fortifier|\bfm ?85|pediasure|fortini|\bjunior|\bkid|\bar\b|\bmilk\b|humana|hipp|mellin|plasmon|nidina|novalac"),
    ("moduli", r"calogen|polycal|fantomalt|protifar|maxijul|duocal|liquigen|\bmct\b|modul|maltodestrin|proteine in polvere|\bprotein powder|renapro|\badpro|protein ?\+? ?plus powder|resource protein instant|vitaflo|calorinut|protipulse|sugarmix|carbolip|dispolipid|\boil\b|olio|richoil|\bdha\b|\bsucrosy"),
    ("formule_enterali", r"nutrison|jevity|osmolite|isosource|novasource|sondalis|nutricomp|survimed|diason|easy ?bag|nutrini|frebini|\bsonda|\btube|peptisorb|peptamen|\bhn\b|\b(500|1000|1500) ?ml|\bsp\b|smartflex|pack"),
    ("ons", r"fortimel|nutridrink|ensure|resource|clinutren|meritene|fresubin|drink|compact|shake|energy|\bhp\b|cr[eè]me|dessert|pudding|forticare|cubitan|diasip|glucerna|impact|supportan|prosure|abound|arginaid|scandishake|renutryl|protein"),
]


class _LegacyTLS(requests.adapters.HTTPAdapter):
    """I server di salute.gov.it negoziano solo cifrari RSA (es. AES256-GCM-SHA384) che
    OpenSSL 3 rifiuta di default: abbassiamo il SECLEVEL solo per quei domini.
    La verifica del certificato resta attiva."""
    def _ctx(self):
        import os, ssl
        ctx = ssl.create_default_context()
        ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        if ca and os.path.exists(ca):
            ctx.load_verify_locations(ca)
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        return ctx
    def init_poolmanager(self, *a, **k):
        k["ssl_context"] = self._ctx(); return super().init_poolmanager(*a, **k)
    def proxy_manager_for(self, *a, **k):
        k["ssl_context"] = self._ctx(); return super().proxy_manager_for(*a, **k)


HTTP = requests.Session()
HTTP.headers["User-Agent"] = UA
HTTP.mount("https://www.dati.salute.gov.it", _LegacyTLS())
HTTP.mount("https://www.salute.gov.it", _LegacyTLS())


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def log(*a):
    print("[update]", *a, file=sys.stderr, flush=True)


def get(url, cache: Path | None, name: str, binary=True, browser=False) -> bytes:
    if cache and (cache / name).exists():
        log("cache", name)
        return (cache / name).read_bytes()
    log("download", url)
    if browser:
        data = browser_get(url)
    else:
        r = HTTP.get(url, timeout=600)
        r.raise_for_status()
        data = r.content
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_bytes(data)
    return data


def _launch(p):
    import os
    exe = os.environ.get("CHROMIUM_PATH")
    return p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()


def browser_get(url: str, page_url: str = AFMS_PAGE) -> bytes:
    """salute.gov.it è protetto da un controllo anti-bot che richiede JavaScript:
    apriamo la pagina con Chromium (Playwright) e scarichiamo il file nella stessa sessione."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(user_agent=UA)
        pg = ctx.new_page()
        pg.goto(page_url, wait_until="networkidle", timeout=90000)
        r = ctx.request.get(url, timeout=120000)
        if r.status != 200:
            raise RuntimeError(f"{url}: HTTP {r.status}")
        body = r.body()
        b.close()
        return body


def browser_links(page_url: str) -> list[tuple[str, str]]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = _launch(p)
        pg = b.new_page(user_agent=UA)
        pg.goto(page_url, wait_until="networkidle", timeout=90000)
        links = pg.eval_on_selector_all("a", "as => as.map(a => [a.innerText.trim(), a.href])")
        b.close()
        return links


# ---------------------------------------------------------------- PARENTERALE
def load_parenterale(cache):
    raw = get(AIFA_CONFEZIONI, cache, "aifa_confezioni.csv").decode("utf-8", errors="replace")
    # Classe di rimborsabilità (A/H) e prezzo dalle liste AIFA, se disponibili
    classe = {}
    try:
        html = HTTP.get(AIFA_LISTE_PAGE, timeout=60).text
        for c in ("A", "H"):
            m = re.search(rf'href="([^"]*Classe-{c}_per_nome-commerciale[^"]*\.csv)"', html)
            if not m:
                continue
            url = "https://www.aifa.gov.it" + m.group(1) if m.group(1).startswith("/") else m.group(1)
            txt = get(url, cache, f"aifa_classe{c}.csv").decode("latin-1")
            rows = csv.reader(io.StringIO(txt), delimiter=";")
            hdr = [h.replace("\n", " ").strip() for h in next(rows)]
            i_aic = next(i for i, h in enumerate(hdr) if "AIC" in h)
            i_pr = next((i for i, h in enumerate(hdr) if h.lower().startswith("prezzo al pubblico")), None)
            for r in rows:
                if len(r) > i_aic:
                    classe[r[i_aic].strip().zfill(9)] = (c, r[i_pr].strip() if i_pr is not None else "")
    except Exception as e:  # le liste A/H sono un arricchimento: non bloccano l'aggiornamento
        log("liste A/H non disponibili:", e)

    prodotti = {}
    for r in csv.DictReader(io.StringIO(raw), delimiter=";"):
        atc = (r.get("CODICE_ATC") or "").strip()
        cat = next((v for k, v in ATC_PARENTERALE.items() if atc.startswith(k)), None)
        if not cat or r.get("STATO_AMMINISTRATIVO") != "Autorizzata":
            continue
        key = (r["COD_FARMACO"], r["DENOMINAZIONE"].strip())
        p = prodotti.setdefault(key, {
            "id": "P-" + r["COD_FARMACO"],
            "tipo": "parenterale",
            "nome": r["DENOMINAZIONE"].strip().title(),
            "azienda": r["RAGIONE_SOCIALE"].strip(),
            "categoria": cat,
            "atc": atc,
            "principi": (r.get("PA_ASSOCIATI") or "").strip().title(),
            "forma": (r.get("FORMA") or "").strip(),
            "link": (r.get("LINK") or "").strip(),
            "confezioni": [],
        })
        aic = r["CODICE_AIC"].strip().zfill(9)
        cl, prezzo = classe.get(aic, ("", ""))
        p["confezioni"].append({"aic": aic, "descr": r["DESCRIZIONE"].strip(), "classe": cl, "prezzo": prezzo})
    out = list(prodotti.values())
    log("parenterali:", len(out))
    return out


# ---------------------------------------------------------------- AFMS / ENTERALE
def find_afms_pdf_url() -> str:
    for text, href in browser_links(AFMS_PAGE):
        if text.strip().upper() == "AFMS PRODOTTO" and href.lower().endswith(".pdf"):
            return href
    raise RuntimeError("Link 'AFMS prodotto' non trovato nella pagina del Ministero")


def load_afms(cache):
    import pdfplumber
    if cache and (cache / "afms.pdf").exists():
        pdf_bytes = (cache / "afms.pdf").read_bytes()
    else:
        url = find_afms_pdf_url()
        pdf_bytes = get(url, cache, "afms.pdf", browser=True)
    records, aggiornato = [], ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            text = " ".join(w["text"] for w in words[:30])
            m = re.search(r"aggiornato al (\d{2}/\d{2}/\d{4})", text)
            if m:
                aggiornato = m.group(1)
            hdr = {w["text"]: w for w in words if w["text"] in ("PRODOTTO", "IMPRESA", "CODICE")}
            if "IMPRESA" not in hdr or "CODICE" not in hdr:
                continue
            x_imp, x_cod, y_hdr = hdr["IMPRESA"]["x0"] - 2, hdr["CODICE"]["x0"] - 2, hdr["CODICE"]["top"]
            lines = defaultdict(list)
            for w in words:
                if w["top"] <= y_hdr + 2 or w["text"].startswith("Pagina") or re.fullmatch(r"\d+", w["text"]) and w["top"] > page.height - 60:
                    continue
                lines[round(w["top"])].append(w)
            for y in sorted(lines):
                ws = sorted(lines[y], key=lambda w: w["x0"])
                prod = " ".join(w["text"] for w in ws if w["x0"] < x_imp)
                imp = " ".join(w["text"] for w in ws if x_imp <= w["x0"] < x_cod)
                cod = " ".join(w["text"] for w in ws if w["x0"] >= x_cod)
                if cod and re.fullmatch(r"\d+", cod):
                    records.append({"prodotto": prod, "impresa": imp, "codice": cod})
                elif records:  # riga di continuazione
                    if prod:
                        records[-1]["prodotto"] += " " + prod
                    if imp:
                        records[-1]["impresa"] += " " + imp
    out, seen = [], set()
    for r in records:
        nome = re.sub(r"\s+", " ", r["prodotto"]).strip()
        az = re.sub(r"\s+", " ", r["impresa"]).strip()
        key = (r["codice"], nome)
        if key in seen:
            continue
        seen.add(key)
        n = norm(nome)
        cat = next((c for c, rx in CAT_REGEX if re.search(rx, n)), "altri_afms")
        out.append({
            "id": f"E-{r['codice']}-{len(out)}",
            "tipo": "enterale",
            "nome": nome,
            "azienda": az,
            "categoria_id": cat,
            "categoria": CATEGORIE[cat],
            "codice_registro": r["codice"],
        })
    log("AFMS:", len(out), "aggiornato al", aggiornato)
    return out, aggiornato


# ---------------------------------------------------------------- DISPOSITIVI
def find_rdm_zip_url() -> str:
    html = HTTP.get(RDM_PAGE, timeout=60).text
    m = re.search(r'href="([^"]*DISPO_RDM[^"]*_csv\.zip)"', html)
    if not m:
        raise RuntimeError("Link CSV del Repertorio Dispositivi Medici non trovato")
    return "https://www.dati.salute.gov.it" + m.group(1) if m.group(1).startswith("/") else m.group(1)


def load_dispositivi(cache):
    url = None if (cache and (cache / "rdm.zip").exists()) else find_rdm_zip_url()
    data = get(url or "", cache, "rdm.zip")
    today = dt.date.today().isoformat()
    groups = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as f:
            rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""), delimiter=";")
            hdr = next(rd)
            ix = {h: i for i, h in enumerate(hdr)}
            for r in rd:
                if len(r) < 16:
                    continue
                cnd = r[ix["classificazione_cnd"]].strip()
                cat = next((v for k, v in CND_DISPOSITIVI.items() if cnd.startswith(k)), None)
                if not cat:
                    continue
                if r[ix["iscrizione_repertorio"]] != "S":
                    continue
                fine_comm = r[ix["data_fine_commercio"]][:10]
                fine_val = r[ix["data_fine_validita"]][:10]
                if (fine_comm and fine_comm <= today) or (fine_val and fine_val <= today):
                    continue
                den = re.sub(r"\s+", " ", r[ix["denominazione_commerciale"]]).strip()
                fab = re.sub(r"\s+", " ", r[ix["fabbricante_assemblatore"]]).strip()
                key = (norm(den), norm(fab), cnd)
                g = groups.get(key)
                if not g:
                    g = groups[key] = {
                        "id": "D-" + r[ix["progressivo_dm_ass"]],
                        "tipo": "dispositivo",
                        "nome": den,
                        "azienda": fab,
                        "categoria": cat,
                        "cnd": cnd,
                        "cnd_descr": r[ix["descrizione_cnd"]].strip().capitalize(),
                        "codici": [],
                        "rdm": [],
                    }
                cod = r[ix["cod_catalogo_fabbr_ass"]].strip().strip(";")
                if cod and cod not in g["codici"] and len(g["codici"]) < 60:
                    g["codici"].append(cod)
                if len(g["rdm"]) < 60:
                    g["rdm"].append(r[ix["progressivo_dm_ass"]])
    out = list(groups.values())
    log("dispositivi:", len(out))
    return out


# ---------------------------------------------------------------- PATOLOGIE
def assign_patologie(products):
    pats = RULES["patologie"]
    compiled = []
    for p in pats:
        rg = p.get("regole", {})
        compiled.append((p["id"],
                         [re.compile(x, re.I) for x in rg.get("nome", [])],
                         set(rg.get("categoria", [])),
                         tuple(rg.get("cnd", [])),
                         tuple(rg.get("atc", [])),
                         p.get("nome_parenterale", p["id"] in ("renale", "epatopatia", "pediatria")),
                         set(rg.get("escludi_categoria", []))))
    manual = RULES.get("manuali", {})
    for pr in products:
        n = norm(pr["nome"])
        found, strong = [], []
        for pid, rx, cats, cnds, atcs, nome_par, excl in compiled:
            ok = False
            if pr["tipo"] == "enterale":
                by_name = any(r.search(n) for r in rx) and pr.get("categoria_id") not in excl
                ok = by_name or (pr.get("categoria_id") in cats and pr.get("categoria_id") not in excl)
                if by_name:
                    strong.append(pid)
            elif pr["tipo"] == "parenterale":
                ok = bool(atcs) and pr["atc"].startswith(atcs) or (nome_par and any(r.search(n) for r in rx))
            elif pr["tipo"] == "dispositivo":
                ok = bool(cnds) and pr["cnd"].startswith(cnds)
            if ok:
                found.append(pid)
        found += [x for x in manual.get(pr["id"], []) if x not in found]
        pr["patologie"] = found
        if strong:
            pr["pat_specifiche"] = strong  # riconosciute dal nome: mostrate per prime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, help="cartella dove salvare/riusare i file scaricati")
    ap.add_argument("--skip", nargs="*", default=[], choices=["parenterale", "enterale", "dispositivi"])
    a = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prev = {}
    if (OUT_DIR / "products.json").exists():
        for p in json.loads((OUT_DIR / "products.json").read_text(encoding="utf-8"))["prodotti"]:
            prev.setdefault(p["tipo"], []).append(p)

    sources, products = {}, []
    steps = [("parenterale", lambda: (load_parenterale(a.cache), "AIFA - Elenco confezioni farmaci")),
             ("enterale", lambda: load_afms(a.cache)),
             ("dispositivi", lambda: (load_dispositivi(a.cache), "Ministero della Salute - Repertorio Dispositivi Medici"))]
    tipo_map = {"parenterale": "parenterale", "enterale": "enterale", "dispositivi": "dispositivo"}
    for name, fn in steps:
        try:
            if name in a.skip:
                raise RuntimeError("saltato")
            res, info = fn()
            products += res
            sources[name] = {"aggiornato": dt.date.today().isoformat() if name != "enterale" else info, "n": len(res)}
        except Exception as e:
            # Se una fonte non è raggiungibile manteniamo i dati della versione precedente
            old = prev.get(tipo_map[name], [])
            log(f"ERRORE {name}: {e} -> mantengo {len(old)} prodotti precedenti")
            products += old
            sources[name] = {"aggiornato": "non aggiornato", "n": len(old), "errore": str(e)}

    assign_patologie(products)
    patologie = [{k: p[k] for k in ("id", "nome", "sinonimi", "descrizione")} for p in RULES["patologie"]]
    data = {
        "generato": dt.datetime.now().isoformat(timespec="minutes"),
        "fonti": sources,
        "patologie": patologie,
        "prodotti": products,
    }
    (OUT_DIR / "products.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log("scritto", OUT_DIR / "products.json", "prodotti:", len(products))
    failed = [k for k, v in sources.items() if "errore" in v]
    sys.exit(1 if len(failed) == len(sources) else 0)


if __name__ == "__main__":
    main()
