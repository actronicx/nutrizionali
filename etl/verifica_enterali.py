#!/usr/bin/env python3
"""
Verifica automatica dei prodotti enterali (AFMS) su pagine ufficiali o di rivenditori.

  python etl/verifica_enterali.py URLS.json OUT.json [--solo-chiavi FILE]

URLS.json: {"produttore": [url, ...], "rivenditore": [url, ...]}
  (pagine prodotto raccolte dalle sitemap pubbliche dei produttori e delle farmacie online)

Per ogni prodotto non ancora verificato:
  1. sceglie le pagine candidate il cui indirizzo contiene il nome del prodotto;
  2. scarica la pagina e accetta la corrispondenza solo se il titolo contiene tutte le
     parole significative del nome e nessuna parola che indichi una variante diversa
     (es. "Fortimel" non corrisponde a "Fortimel Compact Protein");
  3. estrae le frasi di indicazione che citano una patologia dell'elenco, scartando
     quelle che nominano marchi di altri prodotti (sezioni "prodotti correlati");
  4. scrive un lotto da registrare con etl/registra_verifica.py, che ricontrolla
     ogni citazione sulla fonte.
"""
import json, re, sys, threading, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, unquote

sys.path.insert(0, str(Path(__file__).parent))
from verifica import fetch_text, norm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# parole che non identificano il prodotto (gusti, formati, parole commerciali)
GENERICHE = set("""
vari varie gusti gusto gusti: aroma sapore neutro naturale al alla alle allo ai agli di del della dei delle da
e ed con per in il lo la le gli i un una a o senza confezione conf pz pezzi bustine bustina flaconi flacone
vasetti vasetto coppette coppetta barattolo bottiglia bottiglie brick cluster tetra pack pacco sacchetto busta
ml g gr kg mg l lt cl x alimento alimenti fini medici speciali medical food dietetico dietetici integratore
integratori nutrizionale nutrizionali prodotto speciale special purpose ricostituente polvere liquido pronto
uso nuovo nuova formula new packaging grafica pharma s.p.a spa srl
vaniglia cioccolato caffe fragola fragole banana albicocca pesca mela limone arancia lampone frutti bosco
tropicale cappuccino caramello mirtillo mirtilli frutta frutto multifrutti multifrutto neutra ciliegia
nocciola menta cocco mango ananas pera cacao miele yogurt
""".split())
# parole che distinguono una variante: se compaiono solo in uno dei due nomi, non è lo stesso prodotto
VARIANTI = set("""
compact protein plus energy advanced extra fibre fiber fibra multi junior kid kids infant baby pre post
hp hc db diabet diabetic diabete 2kcal 1.5 1,5 3.2 2.4 drink creme crema dessert powder clear instant
junior bambini adulti lp gel aqua pudding sl ultra gourmande essential complete original sensation
peptide pept elemental max mini minis lattanti
""".split())
CUE = re.compile(r"indicat|destinat|gestione dietetica|trattamento dietetico|dietary management|for the dietary|"
                 r"specific\w* per|studiat\w* per|formulat\w* per|pensat\w* per|raccomandat|adatt\w* (a|per)|ideale per|"
                 r"in caso di|nei casi di|per (pazienti|persone|soggetti|bambini|lattanti|neonati)|suitable for|intended for", re.I)
NEGAZIONE = re.compile(r"(\bnon\b|\bsenza\b|\bnot\b|eccetto|esclus|controindicat|cautela|attenzione)[^.;]{0,50}$", re.I)
# espressioni di indicazione "forti": una patologia è assegnata solo se sostenuta da almeno una di queste frasi
FORTE = re.compile(r"indicat|gestione dietetica|gestione nutrizional|trattamento dietetico|trattamento nutrizional|destinat|"
                   r"dietary management|intended for|formulat\w* per|studiat\w* per|specific\w* (per|formulat)|"
                   r"alimento a fini medici speciali per|food for special medical purposes for", re.I)
# avvertenze e consigli d'uso non sono indicazioni
AVVERTENZA = re.compile(r"concordat|consulta|parlane|chiedi al|prima dell.uso|prima di (usare|assumere)|avvertenz|attenzione|"
                        r"non (è|e) indicat|sconsigliat|controindicat|tutta la famiglia|"
                        r"non (usare|utilizzare|somministrare|assumere|adatt|idone|(è|e) adatt|deve)|monitor|valuta(re)? con|"
                        r"assicura|mantenere una|in caso di dubbio|contiene glutine|allergen|va(nno)? adattat|adattare|rivolg|capacit\w* di deglut", re.I)
# sigle di malattie metaboliche nel nome: la pagina deve parlare di malattia metabolica
SIGLE_METABOLICHE = re.compile(r"\b(pku|msud|tyr|hcu|ucd|mma|pa|ga ?1?|iva|hom|leu|tvmi|ilv|eaa|xphe|xmet|xlys|xleu|xmtvi|anamix|lophlex|afenil|numefen|antifen)\b", re.I)
TAGLIO = re.compile(r"prodotti correlati|potrebbe(ro)? interessarti|ti potrebbe|clienti hanno (anche )?acquistato|"
                    r"prodotti simili|altri prodotti|you may also like|related products|recensioni", re.I)
MAPPA = [
    (r"disfag|deglut(?!in)|dysphag|swallow|masticaz", "disfagia"),
    (r"malnutri|denutri|undernutri|malnutrition|aumentat\w* fabbisogn|fabbisogn\w* (energetic|proteic|calori|nutrizional)|"
     r"perdita di peso|calo ponderale|sarcopeni|fragilit|cachessia|ipercatabol|convalescen|inappetenz|ridotto appetito", "malnutrizione"),
    (r"malassorb|maldigest|malabsorption|insufficienza pancreatica|pancreatit|intestino corto|short bowel", "malassorbimento"),
    (r"allergi\w* alle proteine del latte|aplv|cow.?s.? milk|allergi\w* alimentar", "aplv"),
    (r"fenilchetonur|\bpku\b|iperfenilalanin|phenylketon", "pku"),
    (r"fenilchetonur|\bpku\b|errori congeniti|malattie metaboliche|disordini metabolici|msud|sciroppo d.acero|tirosinem|"
     r"omocistinur|acidemi|aciduri|aminoacidopat|ciclo dell.urea|glicogenos|galattosem|inborn error|metabolic disorder|leucinosi|iperammoniem|disturbi metabolici congeniti", "metaboliche"),
    (r"epiless|chetogen|ketogenic|glut.?1", "epilessia"),
    (r"insufficienza renale|nefropat|dialis|\brenal|uremi|malattia renale", "renale"),
    (r"epatopat|cirrosi|insufficienza epatica|encefalopatia epatica|malattia epatica|liver disease", "epatopatia"),
    (r"(?<!ulcere )(?<!ulcera )(?<!piede )(?<!lesioni )diabet|iperglicemi|glicemi|metabolismo glucidico|tolleranza al glucosio", "diabete"),
    (r"bpco|broncopneumopatia|insufficienza respiratoria|copd", "bpco"),
    (r"lesioni da pressione|piaghe|decubit|(?<!colite )ulcer(?!os)|ferite|wound|cicatrizz", "lesioni"),
    (r"oncolog|tumor|cancro|neoplas|chemioterap|radioterap|cancer|(?<!ferite )(?<!ferita )chirurg|perioperator|pre.?operator|post.?operator|surgery", "oncologia"),
    (r"crohn|colite ulcerosa|\bmici\b|infiammatori\w* cronic\w* intestinal|\bibd\b", "mici"),
    (r"lattant|neonat|pretermin|prematur|pediatric|\binfant", "pediatria"),
    (r"per sonda|tramite sonda|via sonda|tube feed|nutrizione enterale|sondino|\bpeg\b", "nutrizione_enterale_sonda"),
    (r"stipsi|costipazion|stitichezz|constipation|regolarit\w* intestinal|fibr\w*[^.]{0,60}diarrea|diarrea[^.]{0,60}fibr", "fibre"),
    (r"disidrataz|reidrat|gastroenterit|\bors\b|(per|favorire|all|l)\W?idratazion", "reidratazione"),
    (r"rigurgit|reflusso|antirigurgito|regurgit", "reflusso"),
    (r"bariatric|bypass|sleeve|obesit", "bariatrica"),
]


def toks(s: str) -> list[str]:
    s = norm(unquote(s)).replace("'", " ")
    s = re.sub(r"\b\d+\s?x\s?\d+\w*|\b\d+\s?(ml|g|gr|kg|mg|l|cl|pz|pezzi|flaconi|bustine|vasetti|brik|bottiglie|bottigliette|buste|porzioni|confezioni|compresse|barrette|coppette)\b|\b\d{8,14}\b", " ", s)
    return [t for t in re.split(r"[^a-z0-9.,]+", s) if t and t.strip(".,") and t.strip(".,") not in GENERICHE]


def significative(nome: str) -> list[str]:
    return [t.strip(".,") for t in toks(nome)]


def slug(url: str) -> list[str]:
    p = urlparse(url).path.rstrip("/").split("/")[-1]
    p = re.sub(r"^\d+-|-\d{8,14}(\.html)?$|\.html?$", "", p)
    return significative(p.replace("-", " ").replace("_", " "))


def titolo(html_text: str) -> str:
    return html_text[:300]


_lock = defaultdict(threading.Lock)
_ultimo = defaultdict(float)


def scarica(url: str) -> str:
    """scarica con pausa minima di 1 s per sito (rispetto dei server)"""
    import hashlib, os
    cache = os.environ.get("VERIFICA_CACHE")
    if cache and (Path(cache) / (hashlib.sha1(url.encode()).hexdigest() + ".v2.txt")).exists():
        return fetch_text(url)  # già in cache: nessuna richiesta al sito
    host = urlparse(url).netloc
    with _lock[host]:
        attesa = 1.0 - (time.time() - _ultimo[host])
        if attesa > 0:
            time.sleep(attesa)
        _ultimo[host] = time.time()
    return fetch_text(url)


def corrisponde(R: list[str], T: list[str]) -> bool:
    if not R:
        return False
    sR, sT = set(R), set(T)
    if not sR <= sT:
        return False
    extra = (sT - sR) & VARIANTI
    return not extra


def main(urls_file, out_file, solo=None):
    fonti = json.loads(Path(urls_file).read_text(encoding="utf-8"))
    prodotti = [p for p in json.loads((ROOT / "web" / "data" / "products.json").read_text(encoding="utf-8"))["prodotti"] if p["tipo"] == "enterale"]
    gia = {v["chiave"] for v in json.loads((ROOT / "etl" / "verifiche.json").read_text(encoding="utf-8"))["verifiche"]}
    if solo:
        solo = set(Path(solo).read_text().split())
    todo = [p for p in prodotti if p["id"] not in gia and (not solo or p["id"] in solo)]
    # marchi = prima parola significativa di ogni prodotto (per scartare frasi su altri prodotti)
    marchi = {significative(p["nome"])[0] for p in prodotti if significative(p["nome"]) and len(significative(p["nome"])[0]) >= 5}

    # indice delle pagine candidate per parola
    indice = defaultdict(list)
    for tipo, lista in fonti.items():
        for u in lista:
            S = slug(u)
            for t in set(S):
                indice[t].append((tipo, u, S))

    def candidati(p):
        R = significative(p["nome"])
        if not R:
            return R, []
        out = []
        for tipo, u, S in indice.get(R[0], []):
            if set(R) <= set(S) or (len(R) >= 2 and len(set(R) & set(S)) >= len(R) - 1):
                score = (tipo == "produttore", set(R) <= set(S), -len(set(S) - set(R)))
                out.append((score, tipo, u))
        out.sort(reverse=True)
        return R, [(t, u) for _, t, u in out[:3]]

    def verifica(p):
        R, cands = candidati(p)
        if not cands:
            return {"chiavi": [p["id"]], "esito": "scheda_non_trovata", "patologie": [],
                    "note": "Nessuna pagina del prodotto trovata tra le fonti consultate (siti dei produttori, farmacie online)."}, "nessun_candidato"
        propri = set(R)
        for tipo, u in cands:
            try:
                testo = scarica(u)
            except Exception:
                continue
            m = TAGLIO.search(testo)
            corpo = testo[:m.start()] if m else testo
            # il titolo della pagina: prime righe non vuote
            righe = [r.strip() for r in corpo.splitlines() if r.strip()]
            intestazione = " ".join(r for r in righe[:400] if any(w in norm(r) for w in R[:1]))[:2000]
            T = significative(intestazione) + slug(u)
            if not corrisponde(R, T if tipo == "produttore" else slug(u) + R):
                continue
            if tipo == "rivenditore" and not corrisponde(R, slug(u)):
                continue
            citazioni, patologie = [], []
            for f in re.split(r"(?<=[.!?])\s+|\n+", corpo):
                f = re.sub(r"\s+", " ", f).strip()
                if not (25 <= len(f) <= 450) or not CUE.search(f):
                    continue
                # domande e risposte delle FAQ dei rivenditori non sono indicazioni del produttore
                if f.endswith("?") or re.match(r"(sì|si|no)\b[,.]", f, re.I):
                    continue
                if AVVERTENZA.search(f) or "<" in f or '="' in f:
                    continue
                nf = norm(f)
                altri = {b for b in marchi if b not in propri and re.search(rf"\b{re.escape(b)}\b", nf)}
                if altri:
                    continue
                trovate = [pid for rx, pid in MAPPA
                           if any(not NEGAZIONE.search(f[:mm.start()]) for mm in re.finditer(rx, f, re.I))]
                if re.search(r"somministra|sondin|sonda", f, re.I):
                    trovate = [x for x in trovate if x != "disfagia"]  # via di somministrazione, non indicazione
                if not FORTE.search(f):
                    continue  # frase generica ("ideale per", "adatto a"): non basta a sostenere un'indicazione
                nuove = [x for x in trovate if x not in patologie]
                if nuove or (trovate and len(citazioni) < 2):
                    # ogni patologia assegnata ha almeno una frase che la sostiene tra quelle salvate
                    citazioni.append(f)
                    patologie += nuove
            if not patologie:
                continue
            if SIGLE_METABOLICHE.search(norm(p["nome"])) and not ({"metaboliche", "pku"} & set(patologie)):
                continue  # testo generico non coerente con un prodotto per malattia metabolica
            return {"chiavi": [p["id"]], "esito": "verificato", "patologie": patologie, "fonte": u, "tipo_fonte": tipo,
                    "citazione": citazioni[0], "altre_citazioni": citazioni[1:], "automatico": True,
                    "note": "Verifica automatica: pagina associata per corrispondenza del nome, frasi di indicazione estratte dal testo."}, "ok_" + tipo
        return {"chiavi": [p["id"]], "esito": "indicazioni_non_chiare", "patologie": [],
                "fonte": cands[0][1],
                "note": "Trovata una pagina candidata ma senza corrispondenza certa del nome o senza indicazioni riconducibili all'elenco delle patologie."}, "non_chiaro"

    lotto, stat = [], defaultdict(int)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (rec, esito) in enumerate(ex.map(verifica, todo), 1):
            lotto.append(rec)
            stat[esito] += 1
            if i % 50 == 0:
                print(f"{i}/{len(todo)}", dict(stat), flush=True)
                Path(out_file).write_text(json.dumps(lotto, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(out_file).write_text(json.dumps(lotto, ensure_ascii=False, indent=1), encoding="utf-8")
    print("FINE", len(todo), dict(stat), flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    solo = a[a.index("--solo-chiavi") + 1] if "--solo-chiavi" in a else None
    main(a[0], a[1], solo)
