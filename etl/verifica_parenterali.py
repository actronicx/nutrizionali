#!/usr/bin/env python3
"""
Verifica automatica dei prodotti parenterali sul Riassunto delle Caratteristiche
del Prodotto (RCP) ufficiale AIFA, sezione 4.1 "Indicazioni terapeutiche".

  python etl/verifica_parenterali.py OUT.json
      Produce un lotto di verifiche da registrare con:
      python etl/registra_verifica.py OUT.json
"""
import csv, io, json, re, sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from verifica import fetch_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AIFA_CONFEZIONI = "https://drive.aifa.gov.it/farmaci/confezioni.csv"
RCP_URL = "https://api.aifa.gov.it/aifa-bdf-eif-be/1.0.0/organizzazione/{ditta}/farmaci/{farmaco}/stampati?ts=RCP"

# parola chiave nella sezione 4.1 -> patologia
MAPPA = [
    (r"nutri\w*\s+(paren\w*al|endoven|per via endoven)|alimentazione paren\w*al|parenteral|nutrizione orale o enterale (è|e) impossibile", "insufficienza_intestinale"),
    (r"malnutri|denutri|catabol|cachessia|carenz[ae] (proteic|nutrizional)|deficit (calorico|proteico|nutrizional)|bilancio azotato negativo|perdit[ae] di azoto|ricostruzione del patrimonio proteico", "malnutrizione"),
    (r"disidrat|reidrat|idratazion|perdit[ae] di (acqua|liquidi)|deplezione (idrica|di liquidi)", "reidratazione"),
    (r"insufficienza renale|nefropat|uremi|dialisi|dialitic", "renale"),
    (r"epatopat|insufficienza epatica|encefalopatia|cirrosi|coma epatico|epatic", "epatopatia"),
    (r"neonat|pretermine|prematur|lattant|bambin|pediatric|infanzia", "pediatria"),
    (r"msud|sciroppo d.acero|errori congeniti del metabolismo|malattie metaboliche", "metaboliche"),
]
NEGAZIONE = re.compile(r"(\bnon\b|\bsenza\b|eccetto|esclus|controindicat)[^.;]{0,40}$", re.I)


def trova(rx, f):
    """vero se la parola chiave compare in forma non negata ("senza insufficienza renale" non conta)"""
    return any(not NEGAZIONE.search(f[:m.start()]) for m in re.finditer(rx, f, re.I))



def sezione_41(text: str) -> str:
    t = re.sub(r"\s+", " ", text)
    # i PDF AIFA contengono lettere spurie (filigrana): titoli cercati in modo tollerante
    m = re.search(r"4\.\s?1\.?\s*Indic\w*\s+ter\w*\.?(.*?)4\.\s?2\.?\s*Pos\w*", t, re.I)
    return m.group(1).strip() if m else ""


def sezione_fi(text: str) -> str:
    t = re.sub(r"\s+", " ", text)
    m = re.search(r"1\.?\s*Che cos.{1,3}\s.{0,80}?a cosa serve(.*?)2\.?\s*(Cosa deve sapere|Prima di)", t, re.I)
    return m.group(1).strip() if m else ""


def frasi(sez: str):
    return [f.strip() for f in re.split(r"(?<=[.;])\s+(?=[A-Z0-9])", sez) if len(f.strip()) > 15]


def main(out):
    prodotti = [p for p in json.loads((ROOT / "web" / "data" / "products.json").read_text(encoding="utf-8"))["prodotti"] if p["tipo"] == "parenterale"]
    ditte = {}
    raw = requests.get(AIFA_CONFEZIONI, timeout=300).content.decode("utf-8", errors="replace")
    for r in csv.DictReader(io.StringIO(raw), delimiter=";"):
        ditte.setdefault(r["COD_FARMACO"], r["CODICE_DITTA"])
    lotto, stat = [], {"ok": 0, "senza_41": 0, "senza_patologie": 0, "errori": 0}
    for p in prodotti:
        cod = p["id"].split("-", 1)[1]
        url = RCP_URL.format(ditta=ditte.get(cod, ""), farmaco=cod)
        try:
            try:
                sez = sezione_41(fetch_text(url))
            except requests.HTTPError:
                sez = ""
            if not sez:  # ripiego: foglietto illustrativo, sezione "Che cos'è ... e a cosa serve"
                url = url.replace("ts=RCP", "ts=FI")
                sez = sezione_fi(fetch_text(url))
        except Exception as e:
            print("✗", p["nome"], "errore:", e)
            stat["errori"] += 1
            lotto.append({"chiavi": [p["id"]], "esito": "scheda_non_trovata", "patologie": [],
                          "note": "RCP AIFA non scaricabile al momento della verifica."})
            continue
        if not sez:
            print("✗", p["nome"], "sezione 4.1 non trovata")
            stat["senza_41"] += 1
            lotto.append({"chiavi": [p["id"]], "esito": "scheda_non_trovata", "patologie": [],
                          "note": "RCP AIFA scaricato ma sezione 4.1 non leggibile."})
            continue
        patologie, citazioni = [], []
        for f in frasi(sez):
            trovate = [pid for rx, pid in MAPPA if trova(rx, f)]
            if trovate:
                citazioni.append(f)
                patologie += [x for x in trovate if x not in patologie]
        if not patologie:
            # indicazioni lette ma non riconducibili all'elenco: non si dichiara verificato
            print("·", p["nome"], "- nessuna patologia del vocabolario:", sez[:120])
            stat["senza_patologie"] += 1
            continue
        stat["ok"] += 1
        print("✓", p["nome"], patologie)
        lotto.append({"chiavi": [p["id"]], "esito": "verificato", "patologie": patologie,
                      "fonte": url, "tipo_fonte": "rcp_aifa" if "ts=RCP" in url else "fi_aifa",
                      "citazione": citazioni[0], "altre_citazioni": citazioni[1:4],
                      "note": ("Sezione 4.1 'Indicazioni terapeutiche' del Riassunto delle Caratteristiche del Prodotto (AIFA)." if "ts=RCP" in url else "Sezione 1 'Che cos'è e a cosa serve' del foglio illustrativo (AIFA).")
                              + ("" if patologie else " Le indicazioni non corrispondono a nessuna patologia dell'elenco.")})
    Path(out).write_text(json.dumps(lotto, ensure_ascii=False, indent=1), encoding="utf-8")
    print(stat)


if __name__ == "__main__":
    main(sys.argv[1])
