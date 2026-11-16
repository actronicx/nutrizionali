/* NutriProntuario – ricerca client-side su web/data/products.json */
(() => {
  "use strict";
  const PAGE = 20;
  const TIPI = {
    tutti: "Tutti",
    enterale: "Enterale / AFMS",
    parenterale: "Parenterale",
    dispositivo: "Dispositivi",
  };
  const TIPO_ORD = { enterale: 0, parenterale: 1, dispositivo: 2 };
  const CHIPS_HOME = ["disfagia", "malnutrizione", "diabete", "renale", "pku", "aplv", "lesioni", "oncologia", "insufficienza_intestinale", "nutrizione_enterale_sonda"];

  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const norm = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const STOP = new Set(["di", "da", "per", "il", "lo", "la", "le", "i", "gli", "e", "ed", "con", "in", "del", "della", "dei", "delle", "a", "al", "alla", "prodotti", "prodotto", "tutti"]);
  // stemming leggerissimo: "disfagie" e "disfagia" -> "disfag"
  const stem = (w) => (w.length > 4 ? w.replace(/[aeiou]+$/, "") : w);
  const tokens = (s) => norm(s).split(/[^a-z0-9]+/).filter((w) => w && !STOP.has(w)).map(stem);

  let DB = null, PAT = {}, state = { q: "", tipo: "tutti", cat: "", pat: null, shown: PAGE, open: null };

  // ------------------------------------------------------------------ dati
  async function load() {
    const r = await fetch("data/products.json", { cache: "no-cache" });
    DB = await r.json();
    for (const p of DB.patologie) {
      p.terms = [p.nome, ...(p.sinonimi || [])].map((t) => tokens(t));
      p.count = 0;
      PAT[p.id] = p;
    }
    for (const pr of DB.prodotti) {
      pr._name = tokens(pr.nome);
      pr._all = Array.from(new Set([
        ...pr._name, ...tokens(pr.azienda), ...tokens(pr.categoria), ...tokens(pr.cnd_descr),
        ...tokens(pr.principi), ...tokens(pr.atc), ...tokens(pr.cnd), ...(pr.codici || []).flatMap(tokens),
        ...(pr.confezioni || []).map((c) => c.aic),
      ]));
      for (const id of pr.patologie) if (PAT[id]) PAT[id].count++;
    }
    const n = DB.prodotti.length.toLocaleString("it-IT");
    const f = DB.fonti || {};
    $("#home-stats").innerHTML =
      `${n} prodotti · ${(f.enterale?.n || 0).toLocaleString("it-IT")} enterali/AFMS · ${(f.parenterale?.n || 0).toLocaleString("it-IT")} parenterali · ${(f.dispositivi?.n || 0).toLocaleString("it-IT")} dispositivi`;
    $("#footer-date").textContent = ` · banca dati aggiornata il ${new Date(DB.generato).toLocaleDateString("it-IT")}`;
    $("#home-chips").innerHTML = CHIPS_HOME.filter((id) => PAT[id])
      .map((id) => `<button class="chip" data-pat="${id}">${esc(short(PAT[id].nome))}</button>`).join("");
  }
  const short = (s) => s.split(/ [/(]/)[0];

  // ------------------------------------------------------------------ ricerca
  const prefixIn = (q, words) => words.some((w) => w.startsWith(q));

  function matchPatologia(q) {
    const qt = tokens(q);
    if (!qt.length) return null;
    let best = null, bestScore = 0;
    for (const p of DB.patologie) {
      for (const t of p.terms) {
        if (!t.length) continue;
        // tutte le parole della query devono essere (prefissi di) parole del termine
        // ogni parola della query deve coprire quasi tutta una parola del termine ("disfagie" ~ "disfagia")
        if (qt.every((w) => t.some((x) => x.startsWith(w) && w.length >= 0.75 * x.length))) {
          const score = qt.join("").length / t.join("").length + (t === p.terms[0] ? 0.01 : 0);
          if (score > bestScore) { best = p; bestScore = score; }
        }
      }
    }
    // richiede almeno 4 caratteri o corrispondenza completa per evitare falsi positivi
    return best && bestScore >= 0.25 && (qt.join("").length >= 4 || bestScore >= 0.99) ? best : null;
  }

  function search(q) {
    const qt = tokens(q);
    const pat = state.pat ? PAT[state.pat] : matchPatologia(q);
    let res = [];
    if (pat) {
      res = DB.prodotti.filter((p) => p.patologie.includes(pat.id)).map((p) => ({ p, s: (p.pat_specifiche?.includes(pat.id) ? 2 : 1) + (p.verifica?.esito === "verificato" ? 0.5 : 0) }));
      // aggiungi anche corrispondenze testuali dirette (es. nome prodotto contenente il termine)
      if (qt.length && !state.pat) {
        const ids = new Set(res.map((r) => r.p.id));
        for (const p of DB.prodotti) if (!ids.has(p.id) && qt.every((w) => prefixIn(w, p._name))) res.push({ p, s: 0.5 });
      }
    } else if (qt.length) {
      for (const p of DB.prodotti) {
        if (!qt.every((w) => prefixIn(w, p._all))) continue;
        let s = 0;
        for (const w of qt) s += p._name.includes(w) ? 3 : prefixIn(w, p._name) ? 2 : 1;
        if (norm(p.nome).startsWith(norm(q).trim())) s += 2;
        res.push({ p, s });
      }
    }
    res.sort((a, b) => b.s - a.s || TIPO_ORD[a.p.tipo] - TIPO_ORD[b.p.tipo] ||
      (a.p.categoria || "").localeCompare(b.p.categoria || "") || a.p.nome.localeCompare(b.p.nome, "it"));
    return { pat, res: res.map((r) => r.p) };
  }

  // ------------------------------------------------------------------ render
  function render() {
    const { q } = state;
    const home = !q && !state.pat;
    $("#home").hidden = !home;
    $("#results").hidden = home;
    document.title = home ? "NutriProntuario" : `${q || PAT[state.pat]?.nome} – NutriProntuario`;
    if (home) { $("#home-q").value = ""; toggleClear($("#home-q")); return; }
    $("#top-q").value = q || PAT[state.pat]?.nome || "";
    toggleClear($("#top-q"));

    const { pat, res } = search(q || "");
    const byTipo = { tutti: res.length, enterale: 0, parenterale: 0, dispositivo: 0 };
    for (const p of res) byTipo[p.tipo]++;
    if (state.tipo !== "tutti" && !byTipo[state.tipo]) state.tipo = "tutti";

    $("#tabs").innerHTML = Object.entries(TIPI).filter(([k]) => k === "tutti" || byTipo[k]).map(([k, v]) =>
      `<button class="tab ${state.tipo === k ? "on" : ""}" data-tipo="${k}">${v}<span class="n">${byTipo[k].toLocaleString("it-IT")}</span></button>`).join("");

    let list = state.tipo === "tutti" ? res : res.filter((p) => p.tipo === state.tipo);
    const cats = {};
    for (const p of list) cats[p.categoria] = (cats[p.categoria] || 0) + 1;
    const catList = Object.entries(cats).sort((a, b) => b[1] - a[1]);
    if (state.cat && !cats[state.cat]) state.cat = "";
    const MAXF = 5;
    let visibili = state.allCats ? catList : catList.slice(0, MAXF);
    if (state.cat && !visibili.some(([c]) => c === state.cat)) visibili = [...visibili, catList.find(([c]) => c === state.cat)];
    $("#filters").innerHTML = catList.length > 1
      ? `<button class="chip ${!state.cat ? "on" : ""}" data-cat="">Tutte le categorie</button>` +
        visibili.map(([c, n]) => `<button class="chip ${state.cat === c ? "on" : ""}" data-cat="${esc(c)}">${esc(c)}<span class="n">${n}</span></button>`).join("") +
        (catList.length > visibili.length ? `<button class="chip more-cats" data-allcats="1">+ altre ${catList.length - visibili.length}</button>` : "")
      : "";
    if (state.cat) list = list.filter((p) => p.categoria === state.cat);
    const nVer = list.filter((p) => p.verifica?.esito === "verificato").length;
    if (nVer) $("#filters").insertAdjacentHTML("afterbegin",
      `<button class="chip ver ${state.soloVer ? "on" : ""}" data-sover="1">✓ Solo verificati<span class="n">${nVer}</span></button>`);
    if (state.soloVer) list = list.filter((p) => p.verifica?.esito === "verificato");

    $("#count").textContent = list.length
      ? `${list.length.toLocaleString("it-IT")} ${list.length === 1 ? "risultato" : "risultati"}${pat ? ` indicati per «${pat.nome}»` : ""}`
      : "";

    $("#items").innerHTML = list.length
      ? list.slice(0, state.shown).map((p) => item(p, pat)).join("")
      : `<li class="empty">Nessun prodotto trovato per <b>${esc(q)}</b>.<ul><li>Verifica l'ortografia.</li><li>Prova una patologia: ${CHIPS_HOME.slice(0, 4).map((id) => `<a href="#" data-pat="${id}">${esc(short(PAT[id].nome))}</a>`).join(", ")}.</li><li>Cerca per azienda, codice AIC, codice CND o di catalogo.</li></ul></li>`;
    $("#more").hidden = list.length <= state.shown;
    $("#more").textContent = `Altri risultati (${(list.length - state.shown).toLocaleString("it-IT")})`;

    renderPanel(pat, res, byTipo);
  }

  function item(p, pat) {
    const tipoLbl = { enterale: "Enterale", parenterale: "Parenterale", dispositivo: "Dispositivo" }[p.tipo];
    const tags = p.patologie.slice(0, 6).map((id) => PAT[id]
      ? `<button class="tag ${pat && pat.id === id ? "hit" : ""}" data-pat="${id}">${esc(short(PAT[id].nome))}</button>` : "").join("");
    const open = state.open === p.id;
    return `<li class="item">
      <div class="crumb"><span class="badge ${p.tipo}">${tipoLbl}</span>${statoBadge(p)}<span class="cat">${esc(p.categoria)}</span></div>
      <h3><button data-open="${esc(p.id)}" aria-expanded="${open}">${esc(p.nome)}</button></h3>
      <div class="az">${esc(p.azienda)}</div>
      ${tags ? `<div class="tags">${tags}</div>` : ""}
      ${open ? detail(p) : ""}
    </li>`;
  }

  const STATI = {
    verificato: ["ok", "✓ Verificato"],
    scheda_non_trovata: ["warn", "⚠ Scheda non trovata"],
    fonte_cambiata: ["warn", "⚠ Fonte cambiata"],
    da_verificare: ["warn", "⚠ Da verificare"],
    cnd: ["info", "Classe CND"],
  };
  function statoBadge(p) {
    const [cls, lbl] = STATI[p.verifica?.esito] || STATI.da_verificare;
    const tip = { ok: "Indicazioni confrontate con la scheda tecnica o la pagina ufficiale del prodotto",
      warn: "Indicazioni assegnate automaticamente, non ancora confrontate con la scheda tecnica",
      info: "Indicazione dedotta dalla classificazione CND ufficiale del dispositivo" }[cls];
    return `<span class="stato ${cls}" title="${tip}">${lbl}</span>`;
  }

  function verificaBox(p) {
    const v = p.verifica || { esito: "da_verificare" };
    if (v.esito === "cnd") {
      return `<div class="verifica info"><b>Indicazione per classe CND.</b> L'associazione alla patologia deriva dalla classificazione nazionale dei dispositivi medici (CND ${esc(p.cnd)}), non dal singolo prodotto.</div>`;
    }
    if (v.esito === "verificato") {
      const fonteLbl = v.tipo_fonte === "produttore" ? "fonte del produttore" : "fonte secondaria (rivenditore)";
      const cit = [v.citazione, ...(v.altre_citazioni || [])].map((c) => `<blockquote>«${esc(c)}»</blockquote>`).join("");
      const corr = [
        v.corrette_tolte?.length ? `rimosse: ${v.corrette_tolte.map((id) => esc(PAT[id]?.nome || id)).join(", ")}` : "",
        v.corrette_aggiunte?.length ? `aggiunte: ${v.corrette_aggiunte.map((id) => esc(PAT[id]?.nome || id)).join(", ")}` : "",
      ].filter(Boolean).join(" · ");
      let host = ""; try { host = new URL(v.fonte).hostname.replace(/^www\./, ""); } catch (_) {}
      return `<div class="verifica ok"><b>✓ Verificato sulla ${fonteLbl}</b>${cit}
        <div class="meta"><a href="${esc(v.fonte)}" target="_blank" rel="noopener">${esc(host)} ↗</a> · ${esc(v.verificatore || "")} · ${esc(v.data || "")}</div>
        ${corr ? `<div class="meta">Correzioni rispetto all'assegnazione automatica: ${corr}</div>` : ""}
        ${v.note ? `<div class="meta">Nota: ${esc(v.note)}</div>` : ""}</div>`;
    }
    const why = v.esito === "da_verificare" ? "Prodotto non ancora confrontato con la scheda tecnica." : (v.note || "Nessuna scheda tecnica trovata online.");
    return `<div class="verifica warn"><b>⚠ Indicazioni non verificate.</b> ${esc(why)} Le patologie mostrate sono assegnate automaticamente in base al nome e alla categoria: controllare la scheda tecnica prima dell'uso.</div>`;
  }

  function detail(p) {
    const row = (k, v) => (v ? `<dt>${k}</dt><dd>${v}</dd>` : "");
    let html = "<dl>";
    html += row("Tipologia", esc({ enterale: "Alimento a fini medici speciali (AFMS)", parenterale: "Farmaco per nutrizione parenterale", dispositivo: "Dispositivo medico" }[p.tipo]));
    html += row("Azienda", esc(p.azienda));
    html += row("Categoria", esc(p.categoria));
    if (p.tipo === "enterale") {
      html += row("Codice registro", esc(p.codice_registro));
      html += row("Fonte", `Registro nazionale AFMS – Ministero della Salute (agg. ${esc(DB.fonti.enterale?.aggiornato || "")})`);
    }
    if (p.tipo === "parenterale") {
      html += row("ATC", esc(p.atc));
      html += row("Principi attivi", esc(p.principi));
      html += row("Forma", esc(p.forma));
      if (p.link) html += row("Foglietto / RCP", `<a href="${esc(p.link)}" target="_blank" rel="noopener">Banca dati AIFA ↗</a>`);
    }
    if (p.tipo === "dispositivo") {
      html += row("CND", `${esc(p.cnd)} – ${esc(p.cnd_descr)}`);
      html += row("N. repertorio", esc(p.rdm.slice(0, 8).join(", ") + (p.rdm.length > 8 ? ` … (+${p.rdm.length - 8})` : "")));
      html += row("Codici catalogo", esc(p.codici.slice(0, 20).join(", ") + (p.codici.length > 20 ? ` … (+${p.codici.length - 20})` : "")));
    }
    const pats = p.patologie.map((id) => PAT[id] && esc(PAT[id].nome)).filter(Boolean).join(" · ");
    html += row(p.verifica?.esito === "verificato" ? "Indicazioni (verificate)" : "Indicazioni (indicative)", pats || "—");
    html += "</dl>";
    html += verificaBox(p);
    if (p.confezioni?.length) {
      html += `<table><thead><tr><th>AIC</th><th>Confezione</th><th>Classe</th><th>Prezzo</th></tr></thead><tbody>` +
        p.confezioni.map((c) => `<tr><td>${esc(c.aic)}</td><td>${esc(c.descr)}</td><td>${esc(c.classe || "–")}</td><td>${c.prezzo && c.prezzo !== "-" ? "€ " + esc(c.prezzo) : "–"}</td></tr>`).join("") +
        `</tbody></table>`;
    }
    return `<div class="detail">${html}</div>`;
  }

  function renderPanel(pat, res, byTipo) {
    const el = $("#panel");
    if (!pat) { el.hidden = true; return; }
    const cats = {};
    for (const p of res) if (p.tipo === "enterale") cats[p.categoria] = (cats[p.categoria] || 0) + 1;
    const top = Object.entries(cats).sort((a, b) => b[1] - a[1]).slice(0, 6);
    el.hidden = false;
    el.innerHTML = `
      <h2>${esc(pat.nome)}</h2>
      <div class="sub">Patologia · ${res.length.toLocaleString("it-IT")} prodotti indicati${(() => { const n = res.filter((p) => p.verifica?.esito === "verificato").length; return n ? ` · <button class="link-ver" data-sover="1">${n} verificati su scheda</button>` : ""; })()}</div>
      <p>${esc(pat.descrizione)}</p>
      <div class="split">
        <button data-tipo="enterale"><strong>${byTipo.enterale}</strong><span>Enterale</span></button>
        <button data-tipo="parenterale"><strong>${byTipo.parenterale}</strong><span>Parenterale</span></button>
        <button data-tipo="dispositivo"><strong>${byTipo.dispositivo}</strong><span>Dispositivi</span></button>
      </div>
      ${top.length ? `<h4>Prodotti enterali per categoria</h4><div class="cats">${top.map(([c, n]) =>
        `<button class="chip" data-tipo="enterale" data-cat="${esc(c)}">${esc(c)}<span class="n">${n}</span></button>`).join("")}</div>` : ""}`;
  }

  // ------------------------------------------------------------------ suggerimenti
  const ICON_SEARCH = `<svg class="ico" viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>`;
  const ICON_PAT = `<svg class="ico" viewBox="0 0 24 24"><path d="M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm-2 10h-4v4h-2v-4H7v-2h4V7h2v4h4v2z"/></svg>`;

  function suggestions(q) {
    const qt = tokens(q);
    if (!qt.length) return [];
    const out = [];
    for (const p of DB.patologie) {
      if (p.terms.some((t) => qt.every((w) => prefixIn(w, t))))
        out.push({ kind: "pat", id: p.id, label: p.nome, hint: `${p.count} prodotti` });
    }
    out.sort((a, b) => PAT[b.id].count - PAT[a.id].count);
    const seen = new Set();
    for (const pr of DB.prodotti) {
      if (out.length >= 9) break;
      const key = norm(pr.nome);
      if (seen.has(key) || !qt.every((w) => prefixIn(w, pr._name))) continue;
      seen.add(key);
      out.push({ kind: "prod", label: pr.nome, hint: { enterale: "Enterale", parenterale: "Parenterale", dispositivo: "Dispositivo" }[pr.tipo] });
    }
    return out.slice(0, 9);
  }

  function hilite(label, q) {
    const n = norm(q).trim();
    const i = norm(label).indexOf(n);
    return i < 0 || !n ? esc(label) : esc(label.slice(0, i)) + "<b>" + esc(label.slice(i, i + n.length)) + "</b>" + esc(label.slice(i + n.length));
  }

  function bindSearch(form, input) {
    const box = $(".suggest", form), clear = $(".clear", form);
    let items = [], active = -1;
    const close = () => { box.hidden = true; form.classList.remove("open"); active = -1; };
    const show = () => {
      items = suggestions(input.value);
      if (!items.length || document.activeElement !== input) return close();
      box.innerHTML = items.map((s, i) => `<li role="option" data-i="${i}" class="${i === active ? "active" : ""}">${s.kind === "pat" ? ICON_PAT : ICON_SEARCH}<span class="lbl">${hilite(s.label, input.value)}</span><span class="hint">${esc(s.hint)}</span></li>`).join("");
      box.hidden = false; form.classList.add("open");
    };
    const pick = (s) => { close(); s.kind === "pat" ? go({ pat: s.id }) : go({ q: s.label }); };
    input.addEventListener("input", () => { active = -1; toggleClear(input); show(); });
    input.addEventListener("focus", show);
    input.addEventListener("blur", () => setTimeout(close, 150));
    input.addEventListener("keydown", (e) => {
      if (box.hidden) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length + 1) % (items.length + 1) - 0;
        if (active === items.length) active = -1;
        [...box.children].forEach((li, i) => li.classList.toggle("active", i === active));
        if (active >= 0) input.value = items[active].label;
      } else if (e.key === "Escape") close();
    });
    box.addEventListener("mousedown", (e) => {
      const li = e.target.closest("li"); if (li) { e.preventDefault(); pick(items[+li.dataset.i]); }
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (active >= 0) return pick(items[active]);
      close(); input.blur();
      go({ q: input.value.trim() });
    });
    clear.addEventListener("click", () => { input.value = ""; toggleClear(input); input.focus(); });
  }
  const toggleClear = (input) => { $(".clear", input.form).hidden = !input.value; };

  // ------------------------------------------------------------------ stato / URL
  function go({ q = "", pat = null, tipo = "tutti", cat = "" }) {
    const p = new URLSearchParams();
    if (pat) p.set("patologia", pat); else if (q) p.set("q", q);
    if (tipo !== "tutti") p.set("tipo", tipo);
    if (cat) p.set("cat", cat);
    const url = p.toString() ? "?" + p : location.pathname;
    // lo stato vive in memoria; l'URL è solo per condividere/ricaricare (può non essere disponibile in un iframe)
    try { history.pushState(null, "", url); } catch (_) { /* ignora */ }
    setState(p);
    window.scrollTo(0, 0);
  }
  function setState(p) {
    state = { q: p.get("q") || "", pat: PAT[p.get("patologia")] ? p.get("patologia") : null, tipo: p.get("tipo") || "tutti", cat: p.get("cat") || "", shown: PAGE, open: null };
    if (!TIPI[state.tipo]) state.tipo = "tutti";
    render();
  }
  function fromURL() {
    let p;
    try { p = new URLSearchParams(location.search); } catch (_) { p = new URLSearchParams(); }
    setState(p);
  }
  function replaceURL() {
    try {
      const p = new URLSearchParams(location.search);
      state.tipo !== "tutti" ? p.set("tipo", state.tipo) : p.delete("tipo");
      state.cat ? p.set("cat", state.cat) : p.delete("cat");
      history.replaceState(null, "", "?" + p);
    } catch (_) { /* ignora */ }
  }

  // ------------------------------------------------------------------ eventi
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-pat],[data-tipo],[data-cat],[data-open],[data-allcats],[data-sover],#more,#logo-home");
    if (!t) return;
    if (t.dataset.allcats) { state.allCats = true; render(); return; }
    if (t.dataset.sover) { state.soloVer = !state.soloVer; state.shown = PAGE; render(); return; }
    if (t.id === "logo-home") { e.preventDefault(); go({}); return; }
    if (t.id === "more") { state.shown += PAGE; render(); return; }
    if (t.dataset.open) { state.open = state.open === t.dataset.open ? null : t.dataset.open; render(); return; }
    if (t.dataset.pat) { e.preventDefault(); go({ pat: t.dataset.pat }); return; }
    if (t.dataset.tipo !== undefined) { state.tipo = t.dataset.tipo; state.cat = t.dataset.cat || ""; }
    else if (t.dataset.cat !== undefined) state.cat = t.dataset.cat;
    state.shown = PAGE; state.open = null;
    replaceURL(); render();
    if (t.closest("#panel")) $("#tabs").scrollIntoView({ block: "start" });
  });
  window.addEventListener("popstate", fromURL);

  bindSearch($("#home-form"), $("#home-q"));
  bindSearch($("#top-form"), $("#top-q"));
  load().then(fromURL).catch((err) => {
    $("#home-stats").textContent = "Impossibile caricare la banca dati: " + err.message;
  });
})();
