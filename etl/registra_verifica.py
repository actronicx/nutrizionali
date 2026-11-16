#!/usr/bin/env python3
"""
Registra (o aggiorna) verifiche in etl/verifiche.json.

Input: un file JSON con una lista di record:
  {"chiavi": ["E-8458-nutilis-clear", ...],      # uno o più prodotti (es. tutti i gusti di una linea)
   "esito": "verificato" | "scheda_non_trovata",
   "patologie": ["disfagia", ...],                # indicazioni confermate dalla fonte
   "fonte": "https://...", "tipo_fonte": "produttore" | "rivenditore",
   "citazione": "frase copiata testualmente dalla fonte",
   "note": "..."}

Per gli esiti "verificato" lo script riscarica la fonte e RIFIUTA il record se la
citazione non vi compare testualmente: nessuna citazione può essere parafrasata o inventata.
"""
import json, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from verifica import VERIFICHE, fetch_text, norm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PATOLOGIE = {p["id"] for p in json.loads((ROOT / "etl" / "patologie.json").read_text(encoding="utf-8"))["patologie"]}


def main(path):
    prodotti = {p["id"]: p for p in json.loads((ROOT / "web" / "data" / "products.json").read_text(encoding="utf-8"))["prodotti"]}
    db = json.loads(VERIFICHE.read_text(encoding="utf-8")) if VERIFICHE.exists() else {
        "_nota": "Verifiche dei prodotti su scheda tecnica o pagina ufficiale. Generato con etl/registra_verifica.py: non modificare a mano le citazioni.",
        "verifiche": []}
    per_chiave = {v["chiave"]: v for v in db["verifiche"]}
    cache, errori, ok = {}, 0, 0
    for rec in json.loads(Path(path).read_text(encoding="utf-8")):
        bad = [x for x in rec.get("patologie", []) if x not in PATOLOGIE]
        missing = [k for k in rec["chiavi"] if k not in prodotti]
        if bad or missing:
            print("✗ RIFIUTATO", rec["chiavi"][0], "patologie sconosciute:", bad, "chiavi inesistenti:", missing)
            errori += 1
            continue
        if rec["esito"] == "verificato":
            u = rec["fonte"]
            if u not in cache:
                try:
                    cache[u] = norm(fetch_text(u))
                except Exception as e:
                    cache[u] = e
            citazioni = [rec["citazione"]] + rec.get("altre_citazioni", [])
            if isinstance(cache[u], Exception) or any(norm(c) not in cache[u] for c in citazioni):
                print("✗ RIFIUTATO", rec["chiavi"][0], "- citazione non trovata nella fonte", "" if not isinstance(cache[u], Exception) else cache[u])
                errori += 1
                continue
        for k in rec["chiavi"]:
            per_chiave[k] = {
                "chiave": k,
                "prodotto": prodotti[k]["nome"],
                "azienda": prodotti[k]["azienda"],
                "esito": rec["esito"],
                "patologie": rec.get("patologie", []),
                "fonte": rec.get("fonte", ""),
                "tipo_fonte": rec.get("tipo_fonte", ""),
                "citazione": rec.get("citazione", ""),
                "altre_citazioni": rec.get("altre_citazioni", []),
                "note": rec.get("note", ""),
                "data": date.today().isoformat(),
                "verificatore": "Claude (IA) – estrazione automatica" if rec.get("automatico") else "Claude (IA) – confronto testuale con la fonte",
            }
            ok += 1
        print("✓", rec["esito"], "-", ", ".join(prodotti[k]["nome"][:40] for k in rec["chiavi"][:3]), "…" if len(rec["chiavi"]) > 3 else "")
    db["verifiche"] = sorted(per_chiave.values(), key=lambda v: v["chiave"])
    VERIFICHE.write_text(json.dumps(db, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nregistrati {ok} prodotti, {errori} record rifiutati, totale verifiche: {len(db['verifiche'])}")


if __name__ == "__main__":
    main(sys.argv[1])
