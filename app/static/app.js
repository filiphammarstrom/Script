"use strict";
const $ = (id) => document.getElementById(id);
const TYPES = ["scene_heading", "action", "character", "dialogue", "parenthetical", "transition", "general"];
const TYPE_LABELS = {
  scene_heading: "Scenrubrik",
  action: "Action",
  character: "Karaktär",
  dialogue: "Dialog",
  parenthetical: "Parentes",
  transition: "Övergång",
  general: "Allmänt",
};
let scenesCollapsed = false;
let project = null;
let undoSnapshot = null;  // manusets element FÖRE senaste diktering (för ångra)

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (!res.ok) {
    const raw = await res.text();
    let msg = raw;
    try {
      const d = JSON.parse(raw).detail;       // FastAPI lägger felet i "detail"
      if (typeof d === "string") msg = d;
    } catch (_) { /* inte JSON – visa råtexten */ }
    throw new Error(msg || res.status);
  }
  return res.json();
}

function esc(s) {
  return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function mkStatus(id) {
  return (msg, busy = false) => {
    const s = $(id);
    s.textContent = msg;
    s.className = "status" + (busy ? " busy" : "");
  };
}
const setStatus = mkStatus("status");
const setGlobalStatus = mkStatus("globalStatus");
const setProjSetStatus = mkStatus("projSetStatus");
const setBaseStatus = mkStatus("baseStatus");
const setAccessStatus = mkStatus("accessStatus");
const setKeysStatus = mkStatus("keysStatus");

// ---- vy-navigering ----
function showView(name) {
  for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== "view-" + name;
  $("navProjects").classList.toggle("active", name === "projects");
  $("navSettings").classList.toggle("active", name === "settings");
  $("navAdmin").classList.toggle("active", name === "admin");
}
$("navProjects").onclick = async () => {
  await loadProjectList();
  showView("projects");
};
$("navSettings").onclick = () => showView("settings");
$("navAdmin").onclick = async () => { await loadAdmin(); showView("admin"); };

// ---- egna regler (läggs ovanpå grunden) ----
async function loadGlobal() {
  const s = await api("GET", "/api/settings");
  $("globalDirectives").value = s.directives || "";
}
$("saveGlobalBtn").onclick = async () => {
  setGlobalStatus("Sparar ...", true);
  try {
    await api("PUT", "/api/settings", { directives: $("globalDirectives").value, rules_filename: "" });
    setGlobalStatus("Sparat ✓");
  } catch (e) {
    setGlobalStatus("Kunde inte spara: " + e.message);
  }
};

// ---- admin: grund (gäller alla) + åtkomst ----
let baseRulesFilename = "";
async function loadAdmin() {
  const base = await api("GET", "/api/base-settings");
  $("baseDirectivesEdit").value = base.directives || "";
  baseRulesFilename = base.rules_filename || "";
  renderBaseActive();
  await loadAccess();
}
function renderBaseActive(pending) {
  const chars = $("baseDirectivesEdit").value.length;
  const box = $("baseRulesActive");
  if (baseRulesFilename) {
    box.innerHTML = `Aktiv regelbok: <strong>${esc(baseRulesFilename)}</strong> · ${chars} tecken` +
      (pending ? ' <em>(ej sparad)</em>' : "");
  } else if (chars > 0) {
    box.innerHTML = `Inskriven text · ${chars} tecken` + (pending ? ' <em>(ej sparad)</em>' : "");
  } else {
    box.textContent = "Ingen grund satt än.";
  }
}
$("saveBaseBtn").onclick = async () => {
  setBaseStatus("Sparar ...", true);
  try {
    await api("PUT", "/api/base-settings", {
      directives: $("baseDirectivesEdit").value,
      rules_filename: baseRulesFilename,
    });
    renderBaseActive();
    setBaseStatus("Grund sparad ✓");
  } catch (e) {
    setBaseStatus("Kunde inte spara: " + e.message);
  }
};
$("baseRulesFile").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  setBaseStatus(`Läser in ${file.name} ...`, true);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/extract-text", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.text()) || res.status);
    const { text } = await res.json();
    const existing = $("baseDirectivesEdit").value.trim();
    $("baseDirectivesEdit").value = existing ? existing + "\n\n" + text : text;
    baseRulesFilename = file.name;
    renderBaseActive(true);
    setBaseStatus(`Inläst ${file.name} (${text.length} tecken) – tryck "Spara grund".`);
  } catch (err) {
    setBaseStatus("Kunde inte läsa filen: " + err.message);
  }
  e.target.value = "";
};
async function loadAccess() {
  renderAccess(await api("GET", "/api/admin/access"));
}
function renderAccess(snap) {
  const box = $("accessList");
  box.innerHTML = "";
  const rows = [
    ...snap.admins.map((email) => ({ email, admin: true, env: snap.env_admins.includes(email) })),
    ...snap.allowed.map((email) => ({ email, admin: false, env: false })),
  ];
  if (!rows.length) {
    box.innerHTML = '<p class="hint">Inga inbjudna än – bara du (admin) kommer in.</p>';
    return;
  }
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "accessrow";
    const tags = (r.admin ? '<span class="badge">admin</span>' : "") +
      (r.env ? ' <span class="hint">(via miljövariabel)</span>' : "");
    row.innerHTML = `<span class="ae-email">${esc(r.email)} ${tags}</span>`;
    const actions = document.createElement("span");
    actions.className = "ae-actions";
    if (!r.env) {
      const adminBtn = document.createElement("button");
      adminBtn.className = "linkbtn";
      adminBtn.textContent = r.admin ? "Ta bort admin" : "Gör till admin";
      adminBtn.onclick = async () => {
        await api("POST", "/api/admin/access/admin", { email: r.email, is_admin: !r.admin });
        await loadAccess();
      };
      const rm = document.createElement("button");
      rm.className = "linkbtn danger";
      rm.textContent = "Ta bort";
      rm.onclick = async () => {
        await api("POST", "/api/admin/access/remove", { email: r.email });
        await loadAccess();
      };
      actions.append(adminBtn, rm);
    }
    row.appendChild(actions);
    box.appendChild(row);
  }
}
$("addAllowBtn").onclick = async () => {
  const email = $("newAllowEmail").value.trim();
  if (!email) return;
  setAccessStatus("Lägger till ...", true);
  try {
    await api("POST", "/api/admin/access/allow", { email });
    $("newAllowEmail").value = "";
    setAccessStatus("Inbjuden ✓");
    await loadAccess();
  } catch (e) {
    setAccessStatus("Kunde inte: " + e.message);
  }
};

// ---- projektlista ----
async function loadProjectList() {
  const list = await api("GET", "/api/projects");
  const box = $("projectList");
  box.innerHTML = "";
  if (!list.length) {
    box.innerHTML = '<p class="hint">Inga projekt än – skapa ett ovan.</p>';
    return;
  }
  for (const p of list) {
    const card = document.createElement("div");
    card.className = "projectcard";
    card.innerHTML = `<span class="pc-title">${esc(p.title)}</span><span class="pc-meta">${p.scenes} scener</span>`;
    card.onclick = async () => openProject(await api("GET", `/api/projects/${p.id}`));
    box.appendChild(card);
  }
}
$("newProjectBtn").onclick = async () => {
  const title = $("newTitle").value.trim() || "Namnlöst projekt";
  const p = await api("POST", "/api/projects", { title });
  $("newTitle").value = "";
  openProject(p);
};

// ---- projektvy ----
function openProject(p) {
  project = p;
  $("projHeadTitle").textContent = p.title;
  $("projTitle").value = p.title;
  $("projContext").value = p.context;
  $("projDirectives").value = p.directives;
  renderBible();
  renderElements();
  $("clarPanel").hidden = true;
  $("clarifications").innerHTML = "";
  $("inputText").value = "";
  hideEditPreview();
  undoSnapshot = null;
  $("undoBtn").hidden = true;
  setStatus("");
  setProjSetStatus("");
  showProjectTab("manus");
  showView("project");
}
function showProjectTab(name) {
  $("tab-manus").hidden = name !== "manus";
  $("tab-projset").hidden = name !== "projset";
  $("tabManusBtn").classList.toggle("active", name === "manus");
  $("tabSettingsBtn").classList.toggle("active", name === "projset");
}
$("tabManusBtn").onclick = () => showProjectTab("manus");
$("tabSettingsBtn").onclick = () => showProjectTab("projset");
$("backToProjects").onclick = async () => {
  await loadProjectList();
  showView("projects");
};

$("saveProjectBtn").onclick = async () => {
  setProjSetStatus("Sparar ...", true);
  try {
    project = await api("PUT", `/api/projects/${project.id}`, {
      title: $("projTitle").value,
      context: $("projContext").value,
      directives: $("projDirectives").value,
      story_bible: project.story_bible,
    });
    $("projHeadTitle").textContent = project.title;
    renderBible();
    setProjSetStatus("Projekt sparat ✓");
  } catch (e) {
    setProjSetStatus("Kunde inte spara: " + e.message);
  }
};
$("synopsisFile").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  setProjSetStatus(`Läser in ${file.name} ...`, true);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/extract-text", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.text()) || res.status);
    const { text } = await res.json();
    const existing = $("projContext").value.trim();
    $("projContext").value = existing ? existing + "\n\n" + text : text;
    setProjSetStatus(`Inläst ${file.name} – tryck "Spara projekt".`);
  } catch (err) {
    setProjSetStatus("Kunde inte läsa filen: " + err.message);
  }
  e.target.value = "";
};

// ---- story-bibel (redigerbar) ----
function splitLines(s) { return s.split("\n").map((x) => x.trim()).filter(Boolean); }
function splitCommas(s) { return s.split(",").map((x) => x.trim()).filter(Boolean); }
function bibleInput(value, ph, oninput) {
  const i = document.createElement("input");
  i.value = value;
  i.placeholder = ph;
  i.oninput = () => oninput(i.value);
  return i;
}
function bibleGroup(label, node) {
  const w = document.createElement("div");
  w.className = "bible-group";
  const l = document.createElement("div");
  l.className = "bible-label";
  l.textContent = label;
  w.append(l, node);
  return w;
}
function renderBible() {
  const b = project.story_bible || (project.story_bible = {});
  b.characters = b.characters || [];
  b.locations = b.locations || [];
  b.notes = b.notes || [];
  const box = $("storyBible");
  box.innerHTML = "";

  // Karaktärer
  const chars = document.createElement("div");
  chars.className = "bible-chars";
  for (const c of b.characters) {
    const row = document.createElement("div");
    row.className = "char-row";
    row.append(
      bibleInput(c.name || "", "Namn (VERSALER)", (v) => (c.name = v)),
      bibleInput((c.aliases || []).join(", "), "Alias (komma)", (v) => (c.aliases = splitCommas(v))),
      bibleInput((c.languages || []).join(", "), "Språk (komma)", (v) => (c.languages = splitCommas(v))),
      bibleInput(c.description || "", "Beskrivning", (v) => (c.description = v)),
    );
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "iconbtn";
    rm.textContent = "✕";
    rm.title = "Ta bort karaktär";
    rm.onclick = () => {
      const i = b.characters.indexOf(c);
      if (i >= 0) { b.characters.splice(i, 1); renderBible(); }
    };
    row.append(rm);
    chars.append(row);
  }
  const add = document.createElement("button");
  add.type = "button";
  add.className = "iconbtn addbtn";
  add.textContent = "+ Karaktär";
  add.onclick = () => { b.characters.push({ name: "", aliases: [], description: "", languages: [] }); renderBible(); };
  const charsWrap = document.createElement("div");
  charsWrap.append(chars, add);
  box.append(bibleGroup("Karaktärer", charsWrap));

  // Platser
  const locs = document.createElement("textarea");
  locs.rows = 3;
  locs.value = b.locations.join("\n");
  locs.placeholder = "En plats per rad ...";
  locs.oninput = () => (b.locations = splitLines(locs.value));
  box.append(bibleGroup("Platser (en per rad)", locs));

  // Anteckningar
  const notes = document.createElement("textarea");
  notes.rows = 3;
  notes.value = b.notes.join("\n");
  notes.placeholder = "En anteckning per rad ...";
  notes.oninput = () => (b.notes = splitLines(notes.value));
  box.append(bibleGroup("Anteckningar (en per rad)", notes));
}

// ---- ljudtranskribering ----
$("audioFile").onchange = () => {
  const f = $("audioFile").files[0];
  $("audioName").textContent = f ? f.name : "";
};
$("transcribeEngine").onchange = () => {
  // Modellvalet är bara relevant för OpenAI-motorn.
  $("transcribeModel").hidden = $("transcribeEngine").value !== "openai";
};
$("transcribeBtn").onclick = async () => {
  const f = $("audioFile").files[0];
  if (!f) {
    setStatus("Välj en ljudfil först.");
    return;
  }
  setStatus("Laddar upp ljud ...", true);
  try {
    const fd = new FormData();
    fd.append("file", f);
    const engine = $("transcribeEngine").value;
    const params = new URLSearchParams();
    if (engine) params.set("backend", engine);
    if (engine === "openai") params.set("model", $("transcribeModel").value);
    const qs = params.toString();
    const url = `/api/projects/${project.id}/transcribe` + (qs ? `?${qs}` : "");
    const res = await fetch(url, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.text()) || res.status);
    const { job_id } = await res.json();
    $("audioFile").value = "";
    $("audioName").textContent = "";
    pollTranscription(job_id);
  } catch (e) {
    setStatus("Fel vid uppladdning: " + e.message);
  }
};

function pollTranscription(jobId) {
  setStatus("Transkriberar ljud (kan ta en stund) ...", true);
  const tick = async () => {
    try {
      const job = await api("GET", `/api/transcribe-jobs/${jobId}`);
      if (job.status === "done") {
        const existing = $("inputText").value.trim();
        $("inputText").value = existing ? existing + "\n\n" + job.text : job.text;
        setStatus("Transkribering klar – granska och tryck Analysera.");
      } else if (job.status === "error") {
        setStatus("Fel vid transkribering: " + job.error);
      } else {
        setTimeout(tick, 3000);
      }
    } catch (e) {
      setStatus("Fel vid statuskoll: " + e.message);
    }
  };
  tick();
}

$("transcriptFile").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  setStatus(`Importerar ${file.name} ...`, true);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/import-transcript", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.text()) || res.status);
    const { text } = await res.json();
    const existing = $("inputText").value.trim();
    $("inputText").value = existing ? existing + "\n\n" + text : text;
    setStatus(`Importerade ${file.name} – granska och tryck Analysera.`);
  } catch (err) {
    setStatus("Kunde inte importera: " + err.message);
  }
  e.target.value = "";
};

// ---- analys ----
$("analyzeBtn").onclick = async () => {
  const text = $("inputText").value.trim();
  if (!text) {
    setStatus("Diktera eller klistra in text först.");
    return;
  }
  setStatus("Bygger in i manuset ...", true);
  const snapshot = JSON.parse(JSON.stringify(project.elements));
  try {
    const data = await api("POST", `/api/projects/${project.id}/dictate`, { text, provider: $("aiEngine").value });
    project = data.project;
    undoSnapshot = snapshot;
    $("undoBtn").hidden = false;
    renderBible();
    renderElements();
    renderClarifications(data.clarifications || []);
    $("inputText").value = "";
    const pending = data.pending_ops || [];
    if (pending.length) {
      showEditPreview(pending);
      setStatus(data.summary || `${pending.length} ändring(ar) av befintligt innehåll att godkänna.`);
    } else {
      hideEditPreview();
      setStatus(data.summary || "Inlagt ✓");
    }
  } catch (e) {
    setStatus("Fel: " + e.message);
  }
};
$("undoBtn").onclick = async () => {
  if (!undoSnapshot) return;
  setStatus("Ångrar ...", true);
  try {
    project = await api("PUT", `/api/projects/${project.id}`, { elements: undoSnapshot });
    undoSnapshot = null;
    $("undoBtn").hidden = true;
    hideEditPreview();
    renderElements();
    setStatus("Ångrade senaste dikteringen.");
  } catch (e) {
    setStatus("Kunde inte ångra: " + e.message);
  }
};

function renderClarifications(clar) {
  const box = $("clarifications");
  box.innerHTML = "";
  $("clarPanel").hidden = clar.length === 0;
  for (const c of clar) {
    const div = document.createElement("div");
    div.className = "clar";
    const opts = (c.options || []).map((o) => `<span class="chip">${esc(o)}</span>`).join(" ");
    div.innerHTML = `<div class="q">${esc(c.question)}</div>` + (opts ? `<div class="opts">${opts}</div>` : "");
    div.onclick = () => highlightElement(c.element_id);
    box.appendChild(div);
  }
}

// ---- manus-editor ----
function autogrow(ta) {
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + 2 + "px";
}
function iconBtn(label, title, fn) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "iconbtn";
  b.textContent = label;
  b.title = title;
  b.onclick = (e) => { e.stopPropagation(); fn(); };
  return b;
}
function elementRow(el) {
  const row = document.createElement("div");
  row.className = "fel fel-" + el.type + (el.confidence !== "high" ? " low-conf" : "");
  row.dataset.id = el.id;

  const ta = document.createElement("textarea");
  ta.className = "fel-text";
  ta.rows = 1;
  ta.value = el.text;
  ta.oninput = () => { el.text = ta.value; autogrow(ta); };
  row.appendChild(ta);

  const tools = document.createElement("div");
  tools.className = "fel-tools";
  const sel = document.createElement("select");
  for (const t of TYPES) {
    const o = document.createElement("option");
    o.value = t;
    o.textContent = TYPE_LABELS[t] || t;
    if (t === el.type) o.selected = true;
    sel.appendChild(o);
  }
  sel.onchange = () => { el.type = sel.value; renderElements(); };  // typbyte kan ändra scenindelning
  tools.appendChild(sel);
  tools.appendChild(iconBtn("+", "Infoga rad under", () => insertElementAfter(el)));
  tools.appendChild(iconBtn("↑", "Flytta upp", () => moveElement(el, -1)));
  tools.appendChild(iconBtn("↓", "Flytta ner", () => moveElement(el, 1)));
  tools.appendChild(iconBtn("✕", "Ta bort raden", () => deleteElement(el)));
  row.appendChild(tools);

  if (el.is_gap) {
    const tag = document.createElement("span");
    tag.className = "gap";
    tag.textContent = "LUCKA";
    row.appendChild(tag);
  }
  return row;
}
// Gruppera elementen i scener: varje scenrubrik startar en ny scen.
function groupScenes() {
  const groups = [];
  let cur = null;
  for (const el of project.elements) {
    if (el.type === "scene_heading") {
      cur = { heading: el, items: [el] };
      groups.push(cur);
    } else {
      if (!cur) { cur = { heading: null, items: [] }; groups.push(cur); }
      cur.items.push(el);
    }
  }
  return groups;
}
// Grov uppskattning av sidor (~55 rader/sida). Final Draft räknar exakt vid export.
const LINES_PER_PAGE = 55;
const CPL = { scene_heading: 60, action: 60, general: 60, transition: 60, character: 38, dialogue: 35, parenthetical: 25 };
function linesFor(el) {
  let l = Math.max(1, Math.ceil((el.text || "").length / (CPL[el.type] || 60)));
  if (el.type === "scene_heading" || el.type === "action" || el.type === "transition" || el.type === "character") l += 1;
  return l;
}
function estimatePages(elements) {
  let lines = 0;
  for (const el of elements) lines += linesFor(el);
  return Math.max(0.1, lines / LINES_PER_PAGE);
}
function fmtPages(p) {
  return (Math.round(p * 10) / 10).toString().replace(".", ",");
}
function pageBreakDivider(page) {
  const pb = document.createElement("div");
  pb.className = "page-break";
  pb.dataset.page = page;
  pb.innerHTML = `<span>Sida ${page}</span>`;
  return pb;
}
function renderElements() {
  const box = $("elements");
  box.innerHTML = "";
  if (!project.elements.length) {
    box.innerHTML = '<p class="hint">Inget manus än – diktera/klistra in text, eller tryck <strong>+ Lägg till rad</strong>.</p>';
    return;
  }

  const groups = groupScenes();
  const sceneTotal = groups.filter((g) => g.heading).length;
  const totalPages = Math.max(1, Math.ceil(estimatePages(project.elements)));

  const bar = document.createElement("div");
  bar.className = "scenes-bar";
  const toggleAll = document.createElement("button");
  toggleAll.type = "button";
  toggleAll.textContent = scenesCollapsed ? "Fäll ut alla" : "Fäll ihop alla";
  toggleAll.onclick = () => { scenesCollapsed = !scenesCollapsed; renderElements(); };
  const stats = document.createElement("span");
  stats.className = "script-stats";
  stats.innerHTML = `Sida <b id="pageNow">1</b> / ${totalPages} · ${sceneTotal} ${sceneTotal === 1 ? "scen" : "scener"}`;
  bar.append(toggleAll, stats);
  box.appendChild(bar);

  let sceneNo = 0;
  let runningLines = 0;
  let lastPage = 1;
  let nextMilestone = 5;
  for (const g of groups) {
    const startPage = Math.floor(runningLines / LINES_PER_PAGE) + 1;
    if (startPage >= nextMilestone) {  // grov sidlinjal (~var 5:e sida) på toppnivå – syns även ihopfällt
      box.appendChild(pageBreakDivider(startPage));
      nextMilestone = Math.floor(startPage / 5) * 5 + 5;
    }
    lastPage = startPage;  // mid-scen-brytningar jämförs mot scenens startsida
    const scene = document.createElement("div");
    scene.className = "scene";
    scene.dataset.page = startPage;

    const head = document.createElement("div");
    head.className = "scene-head";
    const chev = document.createElement("span");
    chev.className = "chev";
    const title = document.createElement("span");
    title.className = "scene-title";
    title.textContent = g.heading ? (g.heading.text || "(ny scenrubrik)") : "(Före första scenrubriken)";
    const pageTag = document.createElement("span");
    pageTag.className = "scene-page";
    pageTag.textContent = "s. " + startPage;
    pageTag.title = "Börjar på sida " + startPage;
    const count = document.createElement("span");
    count.className = "scene-count";
    count.textContent = g.items.length + (g.items.length === 1 ? " rad" : " rader");
    if (g.heading) {
      sceneNo += 1;
      const num = document.createElement("span");
      num.className = "scene-num";
      num.textContent = sceneNo;
      num.title = `Scen ${sceneNo}`;
      head.append(chev, num, title, pageTag, count);
    } else {
      head.append(chev, title, pageTag, count);
    }

    const body = document.createElement("div");
    body.className = "scene-body";
    g.items.forEach((el, idx) => {
      const elPage = Math.floor(runningLines / LINES_PER_PAGE) + 1;
      if (idx > 0 && elPage > lastPage) {  // sidbrytning mitt i en scen – inuti kroppen
        lastPage = elPage;
        body.appendChild(pageBreakDivider(elPage));
      }
      body.appendChild(elementRow(el));
      runningLines += linesFor(el);
    });

    const setCollapsed = (collapsed) => {
      scene.classList.toggle("collapsed", collapsed);
      chev.textContent = collapsed ? "▸" : "▾";
      if (!collapsed) body.querySelectorAll(".fel-text").forEach(autogrow);
    };
    head.onclick = () => setCollapsed(!scene.classList.contains("collapsed"));

    scene.append(head, body);
    box.appendChild(scene);
    setCollapsed(scenesCollapsed);
  }
  box.querySelectorAll(".scene:not(.collapsed) .fel-text").forEach(autogrow);
  bindPageScroll();
  updatePageIndicator();
}
// ---- levande "Sida X / Y" medan man scrollar ----
let pageScrollBound = false;
function bindPageScroll() {
  if (pageScrollBound) return;
  pageScrollBound = true;
  let ticking = false;
  window.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { updatePageIndicator(); ticking = false; });
  }, { passive: true });
}
function updatePageIndicator() {
  const now = document.getElementById("pageNow");
  if (!now) return;
  const ref = 140;  // referenslinje strax under header + sticky-bar
  let cur = 1;
  for (const m of document.querySelectorAll("#elements [data-page]")) {
    if (m.getBoundingClientRect().top <= ref) cur = parseInt(m.dataset.page, 10) || cur;
    else break;  // markörerna är i dokumentordning = visuell ordning
  }
  now.textContent = cur;
}
function moveElement(el, dir) {
  const i = project.elements.indexOf(el);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= project.elements.length) return;
  [project.elements[i], project.elements[j]] = [project.elements[j], project.elements[i]];
  renderElements();
  setStatus('Flyttad – glöm inte "Spara ändringar".');
}
function deleteElement(el) {
  const i = project.elements.indexOf(el);
  if (i < 0) return;
  if (!confirm(`Ta bort raden?\n\n${(el.text || "").slice(0, 80)}`)) return;
  project.elements.splice(i, 1);
  renderElements();
  setStatus('Rad borttagen – glöm inte "Spara ändringar".');
}
// ---- manuellt lägga till rader (som i Final Draft) ----
function newBlankElement(type) {
  const id = project.elements.reduce((m, e) => Math.max(m, e.id), -1) + 1;
  return { id, type: type || "action", text: "", confidence: "high", is_gap: false };
}
function focusElement(id) {
  const ta = document.querySelector(`.fel[data-id="${id}"] .fel-text`);
  if (ta) { ta.focus(); autogrow(ta); }
}
function insertElementAfter(el) {
  const i = project.elements.indexOf(el);
  if (i < 0) return;
  const ne = newBlankElement(el.type === "character" ? "dialogue" : "action");
  project.elements.splice(i + 1, 0, ne);
  renderElements();
  focusElement(ne.id);
  setStatus('Ny rad – välj typ, skriv, och "Spara ändringar".');
}
function appendElement() {
  if (!project) return;
  const ne = newBlankElement(project.elements.length ? "action" : "scene_heading");
  project.elements.push(ne);
  renderElements();
  focusElement(ne.id);
  setStatus('Ny rad – välj typ, skriv, och "Spara ändringar".');
}
$("addRowBtn").onclick = appendElement;
function highlightElement(id) {
  showProjectTab("manus");
  const row = document.querySelector(`.fel[data-id="${id}"]`);
  if (!row) return;
  const scene = row.closest(".scene");
  if (scene && scene.classList.contains("collapsed")) scene.querySelector(".scene-head").click();
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  row.classList.add("flash");
  setTimeout(() => row.classList.remove("flash"), 1500);
}

$("saveElementsBtn").onclick = async () => {
  project = await api("PUT", `/api/projects/${project.id}`, { elements: project.elements });
  renderElements();
  setStatus("Ändringar sparade.");
};

// ---- export ----
$("exportBtn").onclick = async () => {
  const res = await fetch(`/api/projects/${project.id}/export`, { method: "POST" });
  if (!res.ok) {
    setStatus("Export misslyckades.");
    return;
  }
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (project.title || "manus").replace(/\s+/g, "_") + ".fdx";
  a.click();
  URL.revokeObjectURL(a.href);
};

$("segmentBtn").onclick = async () => {
  if (!project || !project.elements.length) { setStatus("Inget manus att dela in än."); return; }
  setStatus("Delar in i scener ...", true);
  try {
    const data = await api("POST", `/api/projects/${project.id}/segment`, { provider: $("aiEngine").value });
    const ops = data.operations || [];
    if (!ops.length) { setStatus(data.summary || "Inga scengränser hittades."); return; }
    undoSnapshot = JSON.parse(JSON.stringify(project.elements));  // för ångra efter godkännande
    showEditPreview(ops);
    setStatus(data.summary || `${ops.length} scenrubriker att godkänna.`);
  } catch (e) {
    setStatus("Fel: " + e.message);
  }
};

// ---- ändringar av befintligt innehåll: granska & godkänn ----
let pendingEdits = null;
function hideEditPreview() {
  pendingEdits = null;
  $("editPreviewPanel").hidden = true;
  $("editPreview").innerHTML = "";
}
function showEditPreview(ops) {
  pendingEdits = ops;
  const byId = {};
  for (const el of project.elements) byId[el.id] = el;
  const box = $("editPreview");
  box.innerHTML = "";

  const list = document.createElement("div");
  list.className = "revise-ops";
  for (const op of ops) {
    const item = document.createElement("div");
    item.className = "revise-op op-" + op.op;
    let label;
    if (op.op === "replace") {
      const cur = byId[op.target_id];
      label = `✏️ Ändra: ”${esc(cur ? cur.text : "?")}” → ”${esc(op.text || "")}”`;
    } else if (op.op === "delete") {
      const cur = byId[op.target_id];
      label = `🗑️ Ta bort: ”${esc(cur ? cur.text : "?")}”`;
    } else {
      // insert_after / insert_after_scene / append: visa de nya elementens text
      const txt = (op.elements || []).map((e) => e.text).filter(Boolean).join(" / ");
      const cur = op.target_id != null ? byId[op.target_id] : null;
      label = `➕ Infoga: ”${esc(txt)}”` + (cur ? ` (efter ”${esc(cur.text)}”)` : " (först)");
    }
    item.innerHTML = `<div class="revise-op-text">${label}</div>` +
      (op.reason ? `<div class="revise-reason">${esc(op.reason)}</div>` : "");
    list.appendChild(item);
  }
  box.appendChild(list);

  const actions = document.createElement("div");
  actions.className = "row";
  const ok = document.createElement("button");
  ok.className = "primary";
  ok.textContent = "Godkänn ändringar";
  ok.onclick = approveEdits;
  const cancel = document.createElement("button");
  cancel.textContent = "Avbryt";
  cancel.onclick = () => { hideEditPreview(); setStatus("Ändringarna ignorerades."); };
  actions.append(ok, cancel);
  box.appendChild(actions);

  $("editPreviewPanel").hidden = false;
}
async function approveEdits() {
  if (!pendingEdits) return;
  setStatus("Godkänner ändringar ...", true);
  try {
    const data = await api("POST", `/api/projects/${project.id}/apply-edits`, { operations: pendingEdits });
    project = data.project;
    hideEditPreview();
    renderElements();
    if (undoSnapshot) $("undoBtn").hidden = false;
    setStatus("Ändringar godkända ✓");
  } catch (e) {
    setStatus("Kunde inte godkänna: " + e.message);
  }
}

// ---- API-nycklar (per användare) ----
function setKeyPlaceholder(id, isSet) {
  $(id).value = "";
  $(id).placeholder = isSet ? "✓ Satt (lämna tomt för att behålla)" : "Inte satt";
}
async function loadSecrets() {
  try {
    const s = await api("GET", "/api/secrets");
    setKeyPlaceholder("keyAnthropic", s.anthropic);
    setKeyPlaceholder("keyOpenai", s.openai);
    setKeyPlaceholder("keyAssemblyai", s.assemblyai);
  } catch (e) { /* ignoreras */ }
}
$("saveKeysBtn").onclick = async () => {
  setKeysStatus("Sparar ...", true);
  try {
    await api("PUT", "/api/secrets", {
      anthropic_key: $("keyAnthropic").value || null,
      openai_key: $("keyOpenai").value || null,
      assemblyai_key: $("keyAssemblyai").value || null,
    });
    await loadSecrets();
    setKeysStatus("Nycklar sparade ✓");
  } catch (e) { setKeysStatus("Kunde inte spara: " + e.message); }
};

// ---- inloggning / uppstart ----
let appConfig = {};
async function boot() {
  try { appConfig = await api("GET", "/api/config"); } catch (e) { appConfig = {}; }
  if (appConfig.auth_enabled) {
    // I molnläget funkar bara moln-transkribering.
    for (const v of ["local", "watch"]) {
      const o = $("transcribeEngine").querySelector(`option[value="${v}"]`);
      if (o) o.remove();
    }
  }
  try {
    const me = await api("GET", "/api/me");
    onLoggedIn(me);
  } catch (e) {
    showLogin();  // 401 i molnläget = inte inloggad
  }
}
function onLoggedIn(me) {
  $("loginOverlay").hidden = true;
  renderUserArea(me);
  $("navAdmin").hidden = !me.is_admin;
  showView("projects");
  loadGlobal();
  loadProjectList();
  loadSecrets();
}
function renderUserArea(me) {
  const ua = $("userArea");
  if (!me.auth_enabled) { ua.hidden = true; return; }
  ua.hidden = false;
  ua.innerHTML = `<span class="uname">${esc(me.name || me.email || "Inloggad")}</span> ` +
    `<button id="logoutBtn" class="linkbtn">Logga ut</button>`;
  $("logoutBtn").onclick = async () => { await api("POST", "/auth/logout"); location.reload(); };
}
function showLogin() {
  $("loginOverlay").hidden = false;
  if (window.google && window.google.accounts) { renderGoogleBtn(); return; }
  const s = document.createElement("script");
  s.src = "https://accounts.google.com/gsi/client";
  s.async = true;
  s.onload = renderGoogleBtn;
  s.onerror = () => { $("loginError").textContent = "Kunde inte ladda Google-inloggning."; };
  document.head.appendChild(s);
}
function renderGoogleBtn() {
  if (!appConfig.google_client_id) {
    $("loginError").textContent = "GOOGLE_CLIENT_ID är inte konfigurerat på servern.";
    return;
  }
  $("googleBtn").innerHTML = "";  // tillåt omrendering vid nytt försök
  google.accounts.id.initialize({ client_id: appConfig.google_client_id, callback: handleCredential });
  google.accounts.id.renderButton($("googleBtn"), { theme: "filled_blue", size: "large", text: "signin_with", shape: "pill" });
}
async function handleCredential(resp) {
  $("loginError").textContent = "";
  try {
    await api("POST", "/auth/google", { credential: resp.credential });
    // Verifiera att sessionscookien faktiskt fastnade innan vi byter vy
    // (i stället för ett blint location.reload() som döljer cookie-problem).
    const me = await api("GET", "/api/me");
    onLoggedIn(me);
  } catch (e) {
    const msg = String((e && e.message) || e);
    if (/inte åtkomst/i.test(msg)) {
      // Servern nekade kontot (inte på allowlisten).
      $("loginError").textContent = msg;
    } else if (/inte inloggad/i.test(msg) || /\b401\b/.test(msg)) {
      // Token godkändes men sessionen kunde inte läsas tillbaka = cookien sparades inte.
      $("loginError").textContent =
        "Inloggningen lyckades men sessionen sparades inte – webbläsaren blockerar troligen cookies. " +
        "Tillåt cookies för den här sidan (eller testa ett inkognitofönster / annan webbläsare) och försök igen.";
    } else {
      $("loginError").textContent = "Inloggning misslyckades: " + msg;
    }
    renderGoogleBtn();  // återställ knappen så att man kan försöka igen
  }
}

// ---- manus: vitt papper / mörkt läge (sparas) ----
const PAPER_KEY = "scriptvoice_paper";
function applyPaper() {
  const on = localStorage.getItem(PAPER_KEY) === "1";
  $("elements").classList.toggle("paper", on);
  $("paperToggle").textContent = on ? "🌙 Mörkt läge" : "📄 Vitt papper";
}
$("paperToggle").onclick = () => {
  localStorage.setItem(PAPER_KEY, localStorage.getItem(PAPER_KEY) === "1" ? "0" : "1");
  applyPaper();
};

// ---- init ----
applyPaper();
boot();
