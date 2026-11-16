#!/usr/bin/env python3
"""
Strumento di verifica dei prodotti su scheda tecnica / pagina ufficiale.

  python etl/verifica.py estrai URL [URL ...]
      Scarica la pagina o il PDF e stampa le frasi che parlano di indicazioni
      (disfagia, deglutizione, "fini medici speciali", "indicato per", ecc.).
      Serve a trovare la citazione testuale da registrare in etl/verifiche.json.

  python etl/verifica.py ricontrolla [--report FILE]
      Per ogni verifica registrata, riscarica la fonte e controlla che la
      citazione sia ancora presente. Le fonti cambiate o non raggiungibili
      vengono segnalate (e il report può essere pubblicato con i dati).
"""
import argparse, io, json, re, sys, unicodedata
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
VERIFICHE = ROOT / "etl" / "verifiche.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
KEYWORDS = re.compile(
    r"disfag|deglut|dysphag|swallow|fini medici speciali|special medical purpose|gestione dietetica|"
    r"indicat|destinat|malnutri|denutri|idratazion|addensa|thicken|iddsi|livello [0-7]|controllo medico",
    re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def _get(url: str):
    import time
    for tentativo in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"}, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504) and tentativo < 2:
                raise requests.ConnectionError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout):
            if tentativo == 2:
                raise
            time.sleep(3 * (tentativo + 1))


def fetch_text(url: str) -> str:
    """testo della pagina o del PDF; con VERIFICA_CACHE=dir i testi sono memorizzati su disco"""
    import hashlib, os
    cache = os.environ.get("VERIFICA_CACHE")
    if cache:
        f = Path(cache) / (hashlib.sha1(url.encode()).hexdigest() + ".v2.txt")
        if f.exists():
            return f.read_text(encoding="utf-8")
        t = _fetch_text(url)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(t, encoding="utf-8")
        return t
    return _fetch_text(url)


def _fetch_text(url: str) -> str:
    try:
        r = _get(url)
    except requests.exceptions.SSLError:
        # alcuni siti hanno una catena di certificati incompleta che Chromium completa da solo
        return _html_a_testo(url, fetch_browser(url))
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf") or r.content[:5] == b"%PDF-":
        import pdfplumber
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    html = r.text
    if len(re.sub(r"<[^>]+>", "", html)) < 500:  # pagina generata via JavaScript
        html = fetch_browser(url)
    return _html_a_testo(url, html)


def _html_a_testo(url: str, html: str) -> str:
    html = contenuto_principale(url, html)
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|td)>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    import html as h
    return h.unescape(text)


def contenuto_principale(url: str, html: str) -> str:
    """Tiene solo il blocco che descrive il prodotto: menu, caroselli di altri prodotti
    e piè di pagina contengono frasi che non riguardano il prodotto."""
    host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/")[0])
    if host == "tuttofarma.it":
        parti = []
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        if m:
            parti.append(m.group(0))
        m = re.search(r'<div class="product_description_short[^"]*"[^>]*>(.*?)</div>', html, re.S)
        if m:
            parti.append(m.group(1))
        i = html.find('id="description"')
        if i >= 0:
            j = html.find('<div class="tab-pane', i + 20)
            parti.append(html[i:j if j > 0 else i + 20000])
        return "\n".join(parti)
    m = re.search(r"<main\b.*?</main>", html, re.S | re.I)
    return m.group(0) if m and len(m.group(0)) > 500 else html


def fetch_browser(url: str) -> str:
    import os
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        pg = b.new_page(user_agent=UA)
        pg.goto(url, wait_until="networkidle", timeout=60000)
        html = pg.content()
        b.close()
        return html


def frasi(text: str):
    righe = [re.sub(r"\s+", " ", x).strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text)]
    visti = set()
    for r in righe:
        if 20 <= len(r) <= 400 and KEYWORDS.search(r) and r not in visti:
            visti.add(r)
            yield r


FORTI = re.compile(r"fini medici speciali|special medical|gestione dietetica|dietary management|indicat[oaie] (per|nei|nella|in)|"
                   r"destinat[oaie] a|for the dietary|per pazienti|per persone|adatto a", re.I)


def cmd_estrai(urls, tutte=False, limite=12):
    for u in urls:
        print(f"\n=== {u}")
        try:
            t = fetch_text(u)
        except Exception as e:
            print("  ERRORE:", e)
            continue
        fs = list(frasi(t))
        if not tutte:
            fs = [f for f in fs if FORTI.search(f)]
        for f in fs[:limite]:
            print("  •", f)
        if not fs:
            print("  (nessuna frase sulle indicazioni trovata)")


def cmd_ricontrolla(report: Path | None):
    data = json.loads(VERIFICHE.read_text(encoding="utf-8"))
    cache, esiti = {}, []
    for v in data["verifiche"]:
        if v.get("esito") != "verificato" or not v.get("fonte"):
            continue
        u = v["fonte"]
        if u not in cache:
            try:
                cache[u] = norm(fetch_text(u))
            except Exception as e:
                cache[u] = e
        t = cache[u]
        if isinstance(t, Exception):
            stato = f"fonte non raggiungibile ({type(t).__name__})"
        elif all(norm(c) in t for c in [v["citazione"]] + v.get("altre_citazioni", [])):
            stato = "ok"
        else:
            stato = "citazione non più presente"
        esiti.append({"chiave": v["chiave"], "prodotto": v["prodotto"], "fonte": u, "stato": stato})
        print(("✓ " if stato == "ok" else "✗ ") + v["prodotto"], "-", stato)
    ko = [e for e in esiti if e["stato"] != "ok"]
    print(f"\n{len(esiti) - len(ko)} ok, {len(ko)} da riesaminare")
    if report:
        report.write_text(json.dumps({"data": date.today().isoformat(), "esiti": esiti}, ensure_ascii=False, indent=1), encoding="utf-8")
    return ko


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("estrai"); e.add_argument("urls", nargs="+"); e.add_argument("--tutte", action="store_true")
    r = sub.add_parser("ricontrolla"); r.add_argument("--report", type=Path)
    a = ap.parse_args()
    if a.cmd == "estrai":
        cmd_estrai(a.urls, a.tutte)
    else:
        cmd_ricontrolla(a.report)


if __name__ == "__main__":
    main()
