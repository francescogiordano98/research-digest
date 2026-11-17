/* Research Digest — client app (no build step). */
(() => {
  const $ = (s) => document.querySelector(s);
  const state = { index: null, issue: null, q: "", topics: new Set(), groups: new Set(), picks: false, sort: "rank", savedView: false };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
  };
  let saved = store.get("saved", {}); // id -> paper snapshot

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------- tiny markdown (brief) ---------- */
  function md(text) {
    const inline = (s) => esc(s)
      .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, "$1<em>$2</em>");
    const out = []; let list = false;
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      if (line.startsWith("- ")) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(line.slice(2))}</li>`); continue; }
      if (list) { out.push("</ul>"); list = false; }
      if (!line) continue;
      if (line.startsWith("#")) out.push(`<h3>${inline(line.replace(/^#+\s*/, ""))}</h3>`);
      else out.push(`<p>${inline(line)}</p>`);
    }
    if (list) out.push("</ul>");
    return out.join("");
  }

  /* ---------- boolean search: AND / OR / NOT / ( ) / "phrases" / field:term ---------- */
  function tokenize(q) {
    const re = /\s*(\(|\)|"[^"]*"|[^\s()"]+)/g; const toks = []; let m;
    while ((m = re.exec(q))) toks.push(m[1]);
    return toks;
  }
  function parse(q) {
    const t = tokenize(q); let i = 0;
    const peek = () => t[i];
    const isOp = (x, op) => x && x.toUpperCase() === op;
    function or() { let n = and(); while (isOp(peek(), "OR")) { i++; n = { op: "or", a: n, b: and() }; } return n; }
    function and() {
      let n = not();
      while (peek() && peek() !== ")" && !isOp(peek(), "OR")) { if (isOp(peek(), "AND")) i++; n = { op: "and", a: n, b: not() }; }
      return n;
    }
    function not() { if (isOp(peek(), "NOT") || peek() === "-") { i++; return { op: "not", a: not() }; } return atom(); }
    function atom() {
      const x = t[i++];
      if (x === undefined) return { op: "true" };
      if (x === "(") { const n = or(); if (peek() === ")") i++; return n; }
      let field = null, term = x;
      const fm = /^(author|title|journal|abstract):(.*)$/i.exec(x);
      if (fm) { field = fm[1].toLowerCase(); term = fm[2]; }
      if (term.startsWith("-") && term.length > 1 && !field) return { op: "not", a: { op: "term", term: term.slice(1).toLowerCase(), field } };
      term = term.replace(/^"|"$/g, "").toLowerCase();
      return term ? { op: "term", term, field } : { op: "true" };
    }
    return t.length ? or() : null;
  }
  function evalQ(n, p) {
    if (!n) return true;
    switch (n.op) {
      case "true": return true;
      case "and": return evalQ(n.a, p) && evalQ(n.b, p);
      case "or": return evalQ(n.a, p) || evalQ(n.b, p);
      case "not": return !evalQ(n.a, p);
      case "term": {
        const hay = n.field ? p._f[n.field] : p._all;
        if (n.term.endsWith("*")) return hay.includes(n.term.slice(0, -1));
        return hay.includes(n.term);
      }
    }
  }
  function terms(n, acc = []) { if (!n) return acc; if (n.op === "term") acc.push(n.term.replace(/\*$/, "")); if (n.op !== "not") { terms(n.a, acc); terms(n.b, acc); } return acc; }
  function highlight(text, ts) {
    let h = esc(text);
    for (const t of ts.filter((x) => x.length > 2)) {
      h = h.replace(new RegExp(`(${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"), "<mark>$1</mark>");
    }
    return h;
  }

  /* ---------- data ---------- */
  async function getJSON(url) { const r = await fetch(url, { cache: "no-cache" }); if (!r.ok) throw new Error(url); return r.json(); }

  function prep(p) {
    p._f = { author: p.authors.join(" ").toLowerCase(), title: p.title.toLowerCase(), journal: `${p.journal} ${p.journal_short}`.toLowerCase(), abstract: `${p.abstract} ${p.summary}`.toLowerCase() };
    p._all = `${p._f.title} ${p._f.author} ${p._f.journal} ${p._f.abstract}`;
    return p;
  }

  async function loadIssue(date) {
    state.issue = await getJSON(`data/issues/${date}.json`);
    state.issue.papers.forEach(prep);
    const sel = $("#issueSelect"); sel.value = date;
    history.replaceState(null, "", `#issue=${date}`);
    renderBrief(); renderChips(); renderList();
  }

  /* ---------- render ---------- */
  function renderBrief() {
    const i = state.issue;
    $("#kicker").textContent = `Weekly Research Digest · No. ${i.number}`;
    $("#issueMeta").textContent = `${fmtDate(i.date)} · ${i.count} papers from ${i.n_sources} outlets · ${i.window}`;
    $("#brief").innerHTML = md(i.brief);
    const card = $("#briefCard");
    card.classList.add("collapsed");
    let btn = card.querySelector(".more");
    if (!btn) { btn = document.createElement("button"); btn.className = "more"; card.appendChild(btn); }
    const sync = () => (btn.textContent = card.classList.contains("collapsed") ? "Read the full brief ↓" : "Collapse ↑");
    btn.onclick = () => { card.classList.toggle("collapsed"); sync(); };
    sync();
  }

  function fmtDate(d) { return new Date(d + "T12:00:00").toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }); }

  function chip(label, n, pressed, onClick) {
    const b = document.createElement("button");
    b.className = "chip"; b.setAttribute("aria-pressed", pressed);
    b.innerHTML = `${esc(label)}${n != null ? `<span class="n">${n}</span>` : ""}`;
    b.onclick = onClick; return b;
  }

  function renderChips() {
    const papers = state.issue.papers, labels = state.issue.topics;
    const tc = {}, gc = {};
    papers.forEach((p) => { p.topics.forEach((t) => (tc[t] = (tc[t] || 0) + 1)); gc[p.group] = (gc[p.group] || 0) + 1; });
    const T = $("#topicChips"); T.innerHTML = "";
    T.appendChild(chip("All topics", null, state.topics.size === 0, () => { state.topics.clear(); renderChips(); renderList(); }));
    Object.entries(labels).filter(([id]) => tc[id]).forEach(([id, label]) =>
      T.appendChild(chip(label, tc[id], state.topics.has(id), () => { toggle(state.topics, id); renderChips(); renderList(); })));
    const G = $("#groupChips"); G.innerHTML = "";
    G.appendChild(chip("All outlets", null, state.groups.size === 0, () => { state.groups.clear(); renderChips(); renderList(); }));
    const ORDER = ["Top 5", "Theory journals", "General-interest journals", "Field journals", "Management & Strategy",
      "Philosophy & Psychology", "Statistics & Applied Math", "General Science", "Preprints"];
    const pos = (g) => (ORDER.indexOf(g) + 1 || 99);
    Object.keys(gc).sort((a, b) => pos(a) - pos(b) || a.localeCompare(b)).forEach((g) =>
      G.appendChild(chip(g, gc[g], state.groups.has(g), () => { toggle(state.groups, g); renderChips(); renderList(); })));
  }
  const toggle = (set, v) => (set.has(v) ? set.delete(v) : set.add(v));

  function renderList() {
    const ast = parse(state.q), ts = terms(ast);
    let ps = state.savedView ? Object.values(saved).map(prep) : state.issue.papers;
    ps = ps.filter((p) =>
      (!state.topics.size || p.topics.some((t) => state.topics.has(t))) &&
      (!state.groups.size || state.groups.has(p.group)) &&
      (!state.picks || p.pick) && evalQ(ast, p));
    const sorters = {
      rank: (a, b) => b.rank - a.rank,
      journal: (a, b) => a.journal.localeCompare(b.journal) || b.rank - a.rank,
      date: (a, b) => (b.date || "").localeCompare(a.date || ""),
    };
    ps = [...ps].sort(sorters[state.sort]);
    $("#count").textContent = `${ps.length} paper${ps.length === 1 ? "" : "s"}${state.savedView ? " saved" : ""}`;
    const labels = (state.issue && state.issue.topics) || {};
    const TIERS = { 1: "Top journals — economics, management & top field", 2: "Field-specific journals", 3: "Other outlets & working papers" };
    let lastTier = null;
    $("#list").innerHTML = ps.length ? ps.map((p) => {
      let head = "";
      if (state.sort === "rank" && !state.savedView && p.tier !== lastTier) {
        lastTier = p.tier;
        head = `<h3 class="band">${esc(TIERS[p.tier] || "Other")}</h3>`;
      }
      return head + paperHTML(p, ts, labels);
    }).join("") : `<div class="empty">${state.savedView ? "No saved papers yet — tap ☆ on any paper." : "No papers match."}</div>`;
  }

  function paperHTML(p, ts, labels) {
    {
      const authors = p.authors.length > 6 ? p.authors.slice(0, 6).join(", ") + " et al." : p.authors.join(", ");
      const isSaved = !!saved[p.id];
      return `<article class="paper">
        <div class="paper-top">
          <h2><a href="${esc(p.url)}" target="_blank" rel="noopener">${highlight(p.title, ts)}</a></h2>
          <button class="star" data-id="${esc(p.id)}" aria-pressed="${isSaved}" title="${isSaved ? "Remove from saved" : "Save"}">${isSaved ? "★" : "☆"}</button>
        </div>
        <div class="byline">${highlight(authors, ts)} · <span class="venue">${esc(p.journal)}</span>${p.date ? " · " + esc(p.date) : ""}</div>
        ${p.summary ? `<p class="summary">${highlight(p.summary, ts)}</p>` : ""}
        ${p.abstract && p.abstract !== p.summary ? `<details><summary>Abstract</summary><p>${highlight(p.abstract, ts)}</p></details>` : ""}
        <div class="tags">
          ${p.pick ? '<span class="tag pick">Top pick</span>' : ""}
          ${p.type === "preprint" ? '<span class="tag pre">Preprint</span>' : ""}
          ${p.topics.filter((t) => labels[t]).slice(0, 3).map((t) => `<span class="tag">${esc(labels[t])}</span>`).join("")}
        </div>
      </article>`;
    }
  }

  /* ---------- events ---------- */
  $("#list").addEventListener("click", (e) => {
    const b = e.target.closest(".star"); if (!b) return;
    const id = b.dataset.id;
    if (saved[id]) delete saved[id];
    else {
      const p = (state.issue.papers.find((x) => x.id === id));
      if (p) { const { _f, _all, ...snap } = p; saved[id] = snap; }
    }
    store.set("saved", saved); updateSavedCount(); renderList();
  });
  let qt; $("#q").addEventListener("input", (e) => { clearTimeout(qt); qt = setTimeout(() => { state.q = e.target.value; renderList(); }, 120); });
  $("#picksOnly").onchange = (e) => { state.picks = e.target.checked; renderList(); };
  $("#sort").onchange = (e) => { state.sort = e.target.value; renderList(); };
  $("#issueSelect").onchange = (e) => loadIssue(e.target.value);
  $("#aboutBtn").onclick = () => $("#about").showModal();
  $("#savedBtn").onclick = () => {
    state.savedView = !state.savedView;
    $("#savedBtn").setAttribute("aria-pressed", state.savedView);
    $("#briefCard").hidden = state.savedView;
    renderList();
  };
  function updateSavedCount() { $("#savedCount").textContent = Object.keys(saved).length; }

  // keep sticky toolbar under the masthead
  const setMH = () => document.documentElement.style.setProperty("--mh", $(".masthead").offsetHeight + "px");
  addEventListener("resize", setMH);

  /* ---------- install (PWA) ---------- */
  let deferred;
  addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; $("#installBtn").hidden = false; });
  $("#installBtn").onclick = async () => { if (deferred) { deferred.prompt(); await deferred.userChoice; deferred = null; $("#installBtn").hidden = true; } };
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.navigator.standalone;
  if (isIOS) { const b = $("#installBtn"); b.hidden = false; b.onclick = () => $("#about").showModal(); }
  if ("serviceWorker" in navigator && location.protocol !== "file:") navigator.serviceWorker.register("sw.js").catch(() => {});

  /* ---------- boot ---------- */
  (async () => {
    setMH(); updateSavedCount();
    try {
      state.index = await getJSON("data/index.json");
    } catch {
      $("#issueMeta").textContent = "No issues yet — run the weekly pipeline to publish the first one.";
      return;
    }
    $("#issueSelect").innerHTML = state.index.issues.map((i) => `<option value="${i.date}">No. ${i.number} · ${i.date}</option>`).join("");
    const m = /issue=([\d-]+)/.exec(location.hash);
    const want = m && state.index.issues.some((i) => i.date === m[1]) ? m[1] : state.index.issues[0].date;
    await loadIssue(want);
  })();
})();
