"use strict";
const $ = (id) => document.getElementById(id);
const TYPES = [
  "scene_heading", "action", "character", "dialogue", "parenthetical", "transition", "general",
  "new_act", "end_of_act",
];
const TYPE_LABELS = {
  scene_heading: "Scenrubrik",
  action: "Action",
  character: "Karaktär",
  dialogue: "Dialog",
  parenthetical: "Parentes",
  transition: "Övergång",
  general: "Allmänt",
  new_act: "Ny akt",
  end_of_act: "Akt-slut",
};
// Korta etiketter i kontrollrailen (hela namnet visas som tooltip).
const TYPE_ABBR = {
  scene_heading: "S",
  action: "A",
  character: "K",
  dialogue: "D",
  parenthetical: "P",
  transition: "Ö",
  general: "G",
  new_act: "N",
  end_of_act: "E",
};
// Vilken typ en ny rad får när man trycker Enter (som i Final Draft / Arc Studio):
// efter karaktär kommer dialog, efter scenrubrik kommer action, osv.
const NEXT_TYPE = {
  scene_heading: "action",
  action: "action",
  character: "dialogue",
  dialogue: "action",
  parenthetical: "dialogue",
  transition: "scene_heading",
  general: "action",
  new_act: "scene_heading",
  end_of_act: "new_act",
};
let project = null;
let undoSnapshot = null;  // manusets element FÖRE senaste diktering (för ångra)
const collapsedScenes = new Set();  // id:n på scenrubriker vars scen är ihopfälld i editorn
const SHARE_TOKEN = new URLSearchParams(location.search).get("share");  // ?share=… = skrivskyddad tittarvy
let sharedToken = null;  // aktiv delningstoken i tittarvyn

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
const setAskStatus = mkStatus("askStatus");
const setVersionStatus = mkStatus("versionStatus");
const setCommentStatus = mkStatus("commentStatus");
const setShareStatus = mkStatus("shareStatus");
const setSharedCommentStatus = mkStatus("sharedCommentStatus");
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
  flushSave();
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
  flushSave();  // spara ev. väntande ändringar i föregående projekt
  project = p;
  $("projHeadTitle").textContent = p.title;
  $("projTitle").value = p.title;
  $("projAuthor").value = p.author || "";
  $("projContact").value = p.contact || "";
  $("projContext").value = p.context;
  $("projDirectives").value = p.directives;
  collapsedScenes.clear();  // alla scener utfällda när ett projekt öppnas
  setDictateCollapsed(true);  // dikteringsrutan börjar hopfälld
  showSection("manus");
  showView("project");  // måste synas INNAN renderElements(), annars mäts textareornas
                          // scrollHeight som 0 (dolt via [hidden]) och raderna blir osynliga
  renderBible();
  renderElements();
  $("clarPanel").hidden = true;
  $("clarifications").innerHTML = "";
  $("inputText").value = "";
  hideEditPreview();
  undoSnapshot = null;
  $("undoBtn").hidden = true;
  $("versionsList").innerHTML = "";
  $("commentsList").innerHTML = "";
  renderShareState(null);
  setShareStatus("");
  findCursor = -1;
  setFindStatus("");
  $("reportBody").innerHTML = "";
  $("askAnswer").hidden = true;
  setStatus("");
  setProjSetStatus("");
  setSaveState("");
}
// Byt sektion i projekt-app-skalet (sidomenyn). Laddar innehåll vid behov.
function showSection(name) {
  for (const s of document.querySelectorAll("#view-project .proj-section")) s.hidden = s.dataset.section !== name;
  for (const b of document.querySelectorAll(".side-item")) b.classList.toggle("active", b.dataset.section === name);
  if (name === "reports") renderReports();
  else if (name === "board") renderCorkboard();
  else if (name === "share") loadShareStatus();
  closeToolPopovers();  // "rutor ovanpå manuset" hör bara hemma i Manus-vyn
  $("sidebar").classList.remove("open");  // stäng mobilmenyn efter val
}
document.querySelectorAll(".side-item").forEach((b) => { b.onclick = () => showSection(b.dataset.section); });
$("sideToggle").onclick = () => $("sidebar").classList.toggle("open");

// ---- Rutor ovanpå manuset: Sök & ersätt / Kommentarer / Versioner (öppnas via knapp i Manus-headern) ----
function closeToolPopovers() {
  document.querySelectorAll(".tool-popover").forEach((p) => { p.hidden = true; });
}
function toggleToolPopover(id, btn) {
  const panel = $(id);
  const wasOpen = !panel.hidden;
  closeToolPopovers();
  if (wasOpen) return;
  const r = btn.getBoundingClientRect();
  panel.style.top = (r.bottom + 8) + "px";
  panel.style.right = (window.innerWidth - r.right) + "px";
  panel.hidden = false;
  if (id === "commentsPanel") loadComments();
  else if (id === "versionsPanel") loadVersions();
}
$("openFindBtn").onclick = () => toggleToolPopover("findPanel", $("openFindBtn"));
$("openCommentsBtn").onclick = () => toggleToolPopover("commentsPanel", $("openCommentsBtn"));
$("openVersionsBtn").onclick = () => toggleToolPopover("versionsPanel", $("openVersionsBtn"));
document.querySelectorAll(".tool-popover .popover-close").forEach((b) => { b.onclick = closeToolPopovers; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeToolPopovers(); });
document.addEventListener("mousedown", (e) => {
  if (e.target.closest(".tool-popover") || e.target.closest(".section-tools")) return;
  closeToolPopovers();
});
$("backToProjects").onclick = async () => {
  flushSave();
  await loadProjectList();
  showView("projects");
};

$("saveProjectBtn").onclick = async () => {
  setProjSetStatus("Sparar ...", true);
  try {
    project = await api("PUT", `/api/projects/${project.id}`, {
      title: $("projTitle").value,
      author: $("projAuthor").value,
      contact: $("projContact").value,
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

// ---- hopfällbar dikteringsruta ----
let dictateCollapsed = true;
function setDictateCollapsed(collapsed) {
  dictateCollapsed = collapsed;
  $("dictateBody").hidden = collapsed;
  $("dsChev").textContent = collapsed ? "▸" : "▾";
  $("dictateSummary").setAttribute("aria-expanded", String(!collapsed));
}
$("dictateSummary").onclick = () => setDictateCollapsed(!dictateCollapsed);
// Fäller ihop rutan om man skrollar i manuset medan man spelar in – den smala
// raden ligger kvar fast ovanför manuset så man ser att inspelningen pågår.
window.addEventListener("scroll", () => {
  if (!dictateCollapsed && mediaRecorder && mediaRecorder.state === "recording") setDictateCollapsed(true);
}, { passive: true });

// ---- ljudtranskribering ----
$("audioFile").onchange = () => {
  const f = $("audioFile").files[0];
  $("audioName").textContent = f ? f.name : "";
};
$("transcribeEngine").onchange = () => {
  // Modellvalet är bara relevant för OpenAI-motorn.
  $("transcribeModel").hidden = $("transcribeEngine").value !== "openai";
};
async function uploadAudio(fileOrBlob, filename) {
  setStatus("Laddar upp ljud ...", true);
  try {
    const fd = new FormData();
    fd.append("file", fileOrBlob, filename);
    const engine = $("transcribeEngine").value;
    const params = new URLSearchParams();
    if (engine) params.set("backend", engine);
    if (engine === "openai") params.set("model", $("transcribeModel").value);
    const qs = params.toString();
    const url = `/api/projects/${project.id}/transcribe` + (qs ? `?${qs}` : "");
    const res = await fetch(url, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.text()) || res.status);
    const { job_id } = await res.json();
    pollTranscription(job_id);
  } catch (e) {
    setStatus("Fel vid uppladdning: " + e.message);
  }
}
$("transcribeBtn").onclick = async () => {
  const f = $("audioFile").files[0];
  if (!f) { setStatus("Välj en ljudfil först."); return; }
  await uploadAudio(f, f.name);
  $("audioFile").value = "";
  $("audioName").textContent = "";
};

// ---- spela in direkt från mikrofonen ----
let mediaRecorder = null, recChunks = [], recStream = null, recTimer = null, recSeconds = 0;
function stopRecTracks() {
  if (recStream) recStream.getTracks().forEach((t) => t.stop());
  recStream = null;
  clearInterval(recTimer);
}
$("recordBtn").onclick = async () => {
  if (mediaRecorder && mediaRecorder.state === "recording") { mediaRecorder.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    setStatus("Inspelning stöds inte i den här webbläsaren – ladda upp en ljudfil i stället.");
    return;
  }
  if (!project) { setStatus("Öppna ett projekt först."); return; }
  try {
    recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    setStatus("Fick inte tillgång till mikrofonen: " + (e.message || e.name));
    return;
  }
  recChunks = [];
  mediaRecorder = new MediaRecorder(recStream);
  mediaRecorder.ondataavailable = (ev) => { if (ev.data && ev.data.size) recChunks.push(ev.data); };
  mediaRecorder.onstop = async () => {
    stopRecTracks();
    $("recordBtn").classList.remove("recording");
    $("recordBtn").textContent = "🎙️ Spela in";
    $("recIndicator").hidden = true;
    const mime = (mediaRecorder && mediaRecorder.mimeType) || "audio/webm";
    const blob = new Blob(recChunks, { type: mime });
    mediaRecorder = null;
    if (!blob.size) { setStatus("Tom inspelning."); return; }
    await uploadAudio(blob, "inspelning." + (mime.includes("mp4") ? "mp4" : "webm"));
  };
  mediaRecorder.start();
  recSeconds = 0;
  $("recordBtn").classList.add("recording");
  $("recordBtn").textContent = "⏹ Stoppa (0:00)";
  $("recIndicator").hidden = false;
  $("recTime").textContent = "0:00";
  recTimer = setInterval(() => {
    recSeconds++;
    const m = Math.floor(recSeconds / 60), s = String(recSeconds % 60).padStart(2, "0");
    $("recordBtn").textContent = `⏹ Stoppa (${m}:${s})`;
    $("recTime").textContent = `${m}:${s}`;
  }, 1000);
  setStatus("Spelar in – prata på, tryck ⏹ när du är klar.");
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
        const suffix = job.progress ? ` (${job.progress})` : "";
        setStatus(`Transkriberar ljud (kan ta en stund)${suffix} ...`, true);
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
  row.className = "fel fel-" + el.type + (el.confidence !== "high" ? " low-conf" : "") + (el.dual ? " dual" : "");
  row.dataset.id = el.id;

  const ta = document.createElement("textarea");
  ta.className = "fel-text";
  ta.rows = 1;
  ta.value = el.text;
  ta.oninput = () => { el.text = ta.value; autogrow(ta); updateAutocomplete(el, ta); scheduleSave(); };
  ta.onblur = () => { hideAutocomplete(); flushSave(); };
  ta.onkeydown = (e) => {
    // Autocomplete (SmartType) har företräde när menyn är öppen.
    if (acOpen() && acTa === ta) {
      if (e.key === "ArrowDown") { e.preventDefault(); acMove(1); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); acMove(-1); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); acceptAutocomplete(); return; }
      if (e.key === "Escape") { e.preventDefault(); hideAutocomplete(); return; }
    }
    // Enter = ny rad (nästa element), som i Final Draft/Arc Studio. Shift+Enter =
    // radbrytning inom samma stycke. Står markören mitt i texten delas raden.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      applyAutoType(el);  // ev. "INT./EXT. …" → scenrubrik, "CUT TO:" → övergång
      const pos = ta.selectionStart;
      if (pos < ta.value.length) {  // dela: behåll texten före markören, flytta resten ned
        const after = ta.value.slice(pos);
        el.text = ta.value.slice(0, pos);
        insertElementAfter(el, el.type, after);
      } else {  // markören sist: lägg till en tom rad av nästa naturliga typ
        insertElementAfter(el, NEXT_TYPE[el.type] || "action", "");
      }
      return;
    }
    // Tab / Shift+Tab = växla elementets typ (som Final Drafts Tab).
    if (e.key === "Tab") {
      e.preventDefault();
      cycleType(el, e.shiftKey ? -1 : 1);
      return;
    }
    // Backspace längst fram = slå ihop raden med den ovanför.
    if (e.key === "Backspace" && ta.selectionStart === 0 && ta.selectionEnd === 0) {
      const i = project.elements.indexOf(el);
      if (i > 0) {
        e.preventDefault();
        const prev = project.elements[i - 1];
        const caret = (prev.text || "").length;
        prev.text = (prev.text || "") + (el.text || "");
        project.elements.splice(i, 1);
        renderElements();
        focusElement(prev.id, caret);
        scheduleSave();
      }
    }
  };
  row.appendChild(ta);

  const tools = document.createElement("div");
  tools.className = "fel-tools";
  const typeBtn = document.createElement("button");
  typeBtn.type = "button";
  typeBtn.className = "iconbtn type-btn";
  typeBtn.textContent = TYPE_ABBR[el.type] || el.type;  // kort: S/A/K/D/P/Ö/G
  typeBtn.title = TYPE_LABELS[el.type] || el.type;  // hela namnet vid hover
  typeBtn.setAttribute("aria-haspopup", "listbox");
  typeBtn.onclick = () => toggleTypeMenu(el, typeBtn);
  typeBtn.onkeydown = (e) => typeBtnKeydown(e, el, typeBtn);
  typeBtn.onblur = () => hideTypeMenu();
  tools.appendChild(typeBtn);
  if (el.type === "character") {
    const dualBtn = iconBtn(
      "⇄",
      el.dual
        ? "Del av Dual Dialogue – klicka för att ta bort repliken ur gruppen"
        : "Markera repliken som Dual Dialogue (visas sida vid sida i FDX-exporten)",
      () => toggleDual(el)
    );
    dualBtn.classList.toggle("active", !!el.dual);
    tools.appendChild(dualBtn);
  }
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
  hideAutocomplete();  // gammal meny pekar på en rad som nu byggs om
  const box = $("elements");
  box.innerHTML = "";
  if (!project.elements.length) {
    box.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "empty-state";
    wrap.innerHTML = '<p class="hint">Inget manus än – diktera/klistra in text ovan, eller börja skriva för hand.</p>';
    const startBtn = document.createElement("button");
    startBtn.className = "primary";
    startBtn.textContent = "✍️ Börja skriva";
    startBtn.onclick = appendElement;  // skapar en första scenrubrik och sätter fokus
    wrap.appendChild(startBtn);
    box.appendChild(wrap);
    renderOutline();  // töm scenlistan i sidofältet
    return;
  }

  const headingIds = project.elements.filter((e) => e.type === "scene_heading").map((e) => e.id);
  const sceneTotal = headingIds.length;
  const totalPages = Math.max(1, Math.ceil(estimatePages(project.elements)));
  const allCollapsed = sceneTotal > 0 && headingIds.every((id) => collapsedScenes.has(id));

  const bar = document.createElement("div");
  bar.className = "scenes-bar";
  const toggleAll = document.createElement("button");
  toggleAll.type = "button";
  toggleAll.textContent = allCollapsed ? "Fäll ut alla" : "Fäll ihop alla";
  toggleAll.disabled = sceneTotal === 0;
  toggleAll.onclick = () => {
    if (allCollapsed) collapsedScenes.clear();
    else headingIds.forEach((id) => collapsedScenes.add(id));
    renderElements();
  };
  const stats = document.createElement("span");
  stats.className = "script-stats";
  stats.innerHTML = `Sida <b id="pageNow">1</b> / ${totalPages} · ${sceneTotal} ${sceneTotal === 1 ? "scen" : "scener"}`;
  bar.append(toggleAll, stats);
  box.appendChild(bar);

  // Ett enda sammanhängande "ark" – elementen flödar som i ett riktigt manus.
  // Scener kan fällas ihop: rubriken visas men resten av scenens rader döljs.
  const page = document.createElement("div");
  page.className = "page";
  let sceneNo = 0, runningLines = 0, lastPage = 1;
  let collapsedNow = false, hiddenCount = 0, headRow = null, headId = null;
  const flushHidden = () => {
    if (headRow && hiddenCount > 0) {
      const note = document.createElement("div");
      note.className = "scene-collapsed-note";
      note.textContent = `▸ ${hiddenCount} ${hiddenCount === 1 ? "rad" : "rader"} dolda`;
      const id = headId;
      note.onclick = () => toggleScene(id);
      headRow.after(note);
    }
    hiddenCount = 0; headRow = null; headId = null;
  };
  for (const el of project.elements) {
    const elPage = Math.floor(runningLines / LINES_PER_PAGE) + 1;
    if (el.type === "scene_heading") {
      flushHidden();
      if (elPage > lastPage) { page.appendChild(pageBreakDivider(elPage)); lastPage = elPage; }
      sceneNo += 1;
      const displayNo = el.scene_number || sceneNo;
      const collapsed = collapsedScenes.has(el.id);
      const row = elementRow(el);
      row.dataset.page = elPage;   // referenspunkt för live "Sida X"-indikatorn
      row.dataset.scene = displayNo;  // CSS visar scennumret i högermarginalen
      const tog = document.createElement("span");
      tog.className = "scene-toggle";
      const chevBtn = document.createElement("button");
      chevBtn.type = "button";
      chevBtn.className = "st-chev";
      chevBtn.textContent = collapsed ? "▸" : "▾";
      chevBtn.title = collapsed ? "Visa scenen" : "Dölj scenen";
      const hid = el.id;
      chevBtn.onclick = (e) => { e.stopPropagation(); toggleScene(hid); };
      const noBtn = document.createElement("button");
      noBtn.type = "button";
      noBtn.className = "st-no" + (el.scene_number ? " locked" : "");
      noBtn.textContent = displayNo;
      noBtn.title = el.scene_number
        ? `Låst scennummer "${el.scene_number}" – klicka för att ändra eller låsa upp`
        : "Klicka för att låsa ett eget scennummer (t.ex. \"12A\")";
      noBtn.onclick = (e) => { e.stopPropagation(); editSceneNumber(el, sceneNo); };
      tog.append(chevBtn, noBtn);
      row.appendChild(tog);
      page.appendChild(row);
      runningLines += linesFor(el);
      collapsedNow = collapsed;
      headRow = collapsed ? row : null;
      headId = collapsed ? el.id : null;
    } else if (collapsedNow) {
      hiddenCount += 1;
      runningLines += linesFor(el);  // räkna sidor även för dolda rader
    } else {
      if (elPage > lastPage) { page.appendChild(pageBreakDivider(elPage)); lastPage = elPage; }
      page.appendChild(elementRow(el));
      runningLines += linesFor(el);
    }
  }
  flushHidden();
  box.appendChild(page);

  page.querySelectorAll(".fel-text").forEach(autogrow);
  bindPageScroll();
  updatePageIndicator();
  renderOutline();
}
function toggleScene(id) {
  if (collapsedScenes.has(id)) collapsedScenes.delete(id);
  else collapsedScenes.add(id);
  renderElements();
}
// Låser/ändrar/tar bort ett eget scennummer (exporteras som Number i FDX i stället
// för den automatiska löpande räkningen, se app/fdx.py).
function editSceneNumber(el, autoNo) {
  const val = window.prompt(
    `Scennummer för den här scenen (t.ex. "12A"). Lämna tomt för automatisk numrering (${autoNo}).`,
    el.scene_number || ""
  );
  if (val === null) return;  // avbrutet
  const trimmed = val.trim();
  el.scene_number = trimmed || null;
  renderElements();
  scheduleSave();
}
// Dual Dialogue: en karaktärsreplik (karaktär + ev. parentes + dialog) markerad
// dual=True. En sammanhängande följd av sådana element exporteras sida vid sida
// i FDX (se app/fdx.py). Togglar hela repliken, inte bara karaktärsraden.
function dualSpeechBlock(charEl) {
  const i = project.elements.indexOf(charEl);
  const block = [charEl];
  for (let j = i + 1; j < project.elements.length; j++) {
    const t = project.elements[j].type;
    if (t === "dialogue" || t === "parenthetical") block.push(project.elements[j]);
    else break;
  }
  return block;
}
function toggleDual(charEl) {
  const turnOn = !charEl.dual;
  dualSpeechBlock(charEl).forEach((e) => { e.dual = turnOn; });
  renderElements();
  scheduleSave();
}
// ---- scen-navigator: hoppa mellan scener + dra för att flytta hela scener ----
function renderOutline() {
  const ol = $("sceneOutline");
  const groups = groupScenes();
  const headedCount = groups.filter((g) => g.heading).length;
  ol.innerHTML = "";
  if (!headedCount) {  // scenlistan i sidofältet är alltid synlig – visa tom-hint
    ol.innerHTML = '<li class="outline-empty">Inga scener än</li>';
    return;
  }
  let runningLines = 0;
  let sceneNo = 0;
  for (const g of groups) {
    const startPage = Math.floor(runningLines / LINES_PER_PAGE) + 1;
    if (g.heading) {
      sceneNo += 1;
      const no = sceneNo;  // positionsindex – används av drag/drop-omsorteringen, INTE ett ev. låst scennummer
      const displayNo = g.heading.scene_number || no;
      const li = document.createElement("li");
      li.className = "outline-item";
      li.draggable = true;
      li.innerHTML = `<span class="ol-num">${displayNo}</span>` +
        `<span class="ol-title">${esc(g.heading.text || "(ny scenrubrik)")}</span>` +
        `<span class="ol-page">s. ${startPage}</span>`;
      li.onclick = () => highlightElement(g.heading.id);
      li.ondragstart = (e) => { e.dataTransfer.setData("text/plain", String(no)); li.classList.add("dragging"); };
      li.ondragend = () => li.classList.remove("dragging");
      li.ondragover = (e) => { e.preventDefault(); li.classList.add("dragover"); };
      li.ondragleave = () => li.classList.remove("dragover");
      li.ondrop = (e) => {
        e.preventDefault();
        li.classList.remove("dragover");
        reorderScene(parseInt(e.dataTransfer.getData("text/plain"), 10), no);
      };
      ol.appendChild(li);
    }
    for (const el of g.items) runningLines += linesFor(el);
  }
}
function reorderScene(from, to) {
  if (!from || !to || from === to) return;
  const groups = groupScenes();
  const lead = (groups[0] && !groups[0].heading) ? groups[0].items : [];
  const headed = groups.filter((g) => g.heading);
  const fromIdx = from - 1, toIdx = to - 1;
  if (fromIdx < 0 || fromIdx >= headed.length || toIdx < 0 || toIdx >= headed.length) return;
  const [moved] = headed.splice(fromIdx, 1);
  headed.splice(toIdx, 0, moved);
  const next = [...lead];
  for (const g of headed) next.push(...g.items);
  project.elements = next;
  renderElements();
  scheduleSave();
  highlightElement(moved.heading.id);
}

// ---- korktavla (scener som index-kort, egen sektion) ----
function scenePreview(headingId) {
  const els = project.elements;
  const i = els.findIndex((e) => e.id === headingId);
  if (i < 0) return "";
  let firstAny = "";
  for (let j = i + 1; j < els.length; j++) {
    if (els[j].type === "scene_heading") break;
    const t = (els[j].text || "").trim();
    if (!t) continue;
    if (els[j].type === "action") return t;       // helst första action-raden
    if (!firstAny) firstAny = t;
  }
  return firstAny;
}
function renderCorkboard() {
  const box = $("corkboard");
  box.innerHTML = "";
  const scenes = sceneReport();
  if (!scenes.length) {
    box.innerHTML = '<p class="hint">Inga scener än – skriv scenrubriker i manuset (eller diktera) först.</p>';
    return;
  }
  for (const s of scenes) {
    const card = document.createElement("div");
    card.className = "card";
    card.draggable = true;
    card.dataset.no = s.no;
    card.innerHTML =
      `<div class="card-head"><span class="card-no">${s.no}</span><span class="card-page">s. ${s.page}</span></div>` +
      `<div class="card-title">${esc(s.heading)}</div>` +
      `<div class="card-body">${esc(scenePreview(s.id))}</div>` +
      `<div class="card-foot">${s.rows} rader${s.chars.length ? " · " + esc(s.chars.join(", ")) : ""}</div>`;
    card.onclick = () => { showSection("manus"); highlightElement(s.id); };
    card.ondragstart = (e) => { e.dataTransfer.setData("text/plain", String(s.no)); card.classList.add("dragging"); };
    card.ondragend = () => card.classList.remove("dragging");
    card.ondragover = (e) => { e.preventDefault(); card.classList.add("dragover"); };
    card.ondragleave = () => card.classList.remove("dragover");
    card.ondrop = (e) => {
      e.preventDefault();
      card.classList.remove("dragover");
      reorderScene(parseInt(e.dataTransfer.getData("text/plain"), 10), s.no);
      renderCorkboard();  // bygg om brädet i den nya ordningen
    };
    box.appendChild(card);
  }
}

// ---- levande "Sida X / Y" medan man scrollar ----
let pageScrollBound = false;
function bindPageScroll() {
  if (pageScrollBound) return;
  pageScrollBound = true;
  let ticking = false;
  window.addEventListener("scroll", () => {
    hideAutocomplete();  // menyn är fast positionerad – följ inte med vid scroll
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
  scheduleSave();
}
function deleteElement(el) {
  const i = project.elements.indexOf(el);
  if (i < 0) return;
  if (!confirm(`Ta bort raden?\n\n${(el.text || "").slice(0, 80)}`)) return;
  project.elements.splice(i, 1);
  renderElements();
  scheduleSave();
}
// ---- manuellt lägga till rader (som i Final Draft) ----
function newBlankElement(type) {
  const id = project.elements.reduce((m, e) => Math.max(m, e.id), -1) + 1;
  return { id, type: type || "action", text: "", confidence: "high", is_gap: false };
}
function focusElement(id, pos) {
  const ta = document.querySelector(`.fel[data-id="${id}"] .fel-text`);
  if (!ta) return;
  ta.focus();
  autogrow(ta);
  if (pos != null) ta.selectionStart = ta.selectionEnd = pos;
}
// Tab/Shift+Tab växlar elementets typ i tur och ordning (TYPES-ordningen).
function cycleType(el, dir) {
  const i = TYPES.indexOf(el.type);
  el.type = TYPES[(i + dir + TYPES.length) % TYPES.length];
  renderElements();  // typbyte kan ändra scenindelningen
  focusElement(el.id, (el.text || "").length);
  scheduleSave();
}

// ---- SmartType: känn igen scenrubrik/övergång på textens form ----
function detectType(el) {
  // Bara fritt typade rader får auto-typas om – aldrig redan satta typer.
  if (el.type !== "action" && el.type !== "general") return null;
  const t = (el.text || "").trim();
  if (!t) return null;
  if (/^(INT|EXT|INT\.?\/EXT|EXT\.?\/INT|I\/E)[.\s]/i.test(t)) return "scene_heading";
  if (/^FADE IN:/i.test(t) || /\b(CUT TO|DISSOLVE TO|SMASH CUT TO|MATCH CUT TO|FADE TO|FADE OUT)\b:?\.?\s*$/i.test(t)) return "transition";
  return null;
}
function applyAutoType(el) {
  const nt = detectType(el);
  if (nt && nt !== el.type) { el.type = nt; return true; }
  return false;
}

// ---- Autocomplete (SmartType): komplettera namn/scenrubriker/övergångar ----
let acMenu = null, acItems = [], acIndex = -1, acTa = null, acEl = null;
const AC_TYPES = new Set(["character", "scene_heading", "transition"]);
const AC_TRANSITIONS = ["CUT TO:", "DISSOLVE TO:", "SMASH CUT TO:", "MATCH CUT TO:", "FADE OUT.", "FADE IN:"];
function acOpen() { return !!acMenu && !acMenu.hidden; }
function acPool(type) {
  const set = new Set();
  if (type === "character") {
    for (const c of (project.story_bible?.characters || [])) {
      if (c.name) set.add(c.name.toUpperCase());
      for (const a of (c.aliases || [])) if (a) set.add(a.toUpperCase());
    }
  } else if (type === "scene_heading") {
    for (const l of (project.story_bible?.locations || [])) if (l) set.add(l);
  } else if (type === "transition") {
    for (const x of AC_TRANSITIONS) set.add(x);
  }
  for (const el of project.elements) {
    if (el.type === type && (el.text || "").trim()) {
      set.add(type === "character" ? el.text.trim().toUpperCase() : el.text.trim());
    }
  }
  return [...set];
}
function updateAutocomplete(el, ta) {
  if (!AC_TYPES.has(el.type)) { hideAutocomplete(); return; }
  const val = ta.value.trim();
  if (!val) { hideAutocomplete(); return; }
  const ci = el.type === "character";
  const needle = ci ? val.toUpperCase() : val.toLowerCase();
  const matches = acPool(el.type).filter((x) => {
    const cmp = ci ? x.toUpperCase() : x.toLowerCase();
    return cmp.startsWith(needle) && cmp !== needle;
  }).slice(0, 6);
  if (!matches.length) { hideAutocomplete(); return; }
  showAutocomplete(el, ta, matches);
}
function showAutocomplete(el, ta, matches) {
  if (!acMenu) {
    acMenu = document.createElement("div");
    acMenu.className = "ac-menu";
    document.body.appendChild(acMenu);
  }
  acItems = matches; acIndex = 0; acTa = ta; acEl = el;
  acMenu.innerHTML = "";
  matches.forEach((m, i) => {
    const item = document.createElement("div");
    item.className = "ac-item" + (i === 0 ? " active" : "");
    item.textContent = m;
    item.onmousedown = (e) => { e.preventDefault(); acceptAutocomplete(i); };  // behåll fokus
    acMenu.appendChild(item);
  });
  const r = ta.getBoundingClientRect();
  acMenu.style.left = r.left + "px";
  acMenu.style.top = (r.bottom + 2) + "px";
  acMenu.style.minWidth = Math.max(140, r.width) + "px";
  acMenu.hidden = false;
}
function hideAutocomplete() {
  if (acMenu) acMenu.hidden = true;
  acItems = []; acIndex = -1; acTa = null; acEl = null;
}
function acMove(d) {
  if (!acOpen()) return;
  acIndex = (acIndex + d + acItems.length) % acItems.length;
  [...acMenu.children].forEach((c, i) => c.classList.toggle("active", i === acIndex));
}
function acceptAutocomplete(i) {
  const val = acItems[i != null ? i : acIndex];
  const ta = acTa, el = acEl;
  hideAutocomplete();
  if (val == null || !el || !ta) return;
  el.text = val;
  ta.value = val;
  autogrow(ta);
  ta.focus();
  ta.selectionStart = ta.selectionEnd = val.length;
  scheduleSave();
}
// ---- Typvalsmeny (radens verktygsrail): visar hela namnet (Dialog, Action, ...) i den
// öppna listan även om knappen bara visar bokstaven (S/A/K/D/P/Ö/G) hopfälld. Bokstaven
// hoppar direkt till rätt rad i listan (som webbläsarens inbyggda val i en <select>).
let typeMenu = null, typeMenuEl = null, typeMenuBtn = null, typeMenuIndex = -1;
function typeMenuOpen() { return !!typeMenu && !typeMenu.hidden; }
function toggleTypeMenu(el, btn) {
  if (typeMenuOpen() && typeMenuBtn === btn) { hideTypeMenu(); return; }
  openTypeMenu(el, btn);
}
function openTypeMenu(el, btn) {
  if (!typeMenu) {
    typeMenu = document.createElement("div");
    typeMenu.className = "ac-menu";
    document.body.appendChild(typeMenu);
  }
  typeMenuEl = el; typeMenuBtn = btn; typeMenuIndex = TYPES.indexOf(el.type);
  typeMenu.innerHTML = "";
  TYPES.forEach((t, i) => {
    const item = document.createElement("div");
    item.className = "ac-item" + (i === typeMenuIndex ? " active" : "");
    item.textContent = `${TYPE_ABBR[t]} — ${TYPE_LABELS[t]}`;
    item.onmousedown = (e) => { e.preventDefault(); applyType(t); };  // behåll fokus på knappen
    typeMenu.appendChild(item);
  });
  const r = btn.getBoundingClientRect();
  typeMenu.style.left = r.left + "px";
  typeMenu.style.top = (r.bottom + 2) + "px";
  typeMenu.style.minWidth = Math.max(150, r.width) + "px";
  typeMenu.hidden = false;
}
function hideTypeMenu() {
  if (typeMenu) typeMenu.hidden = true;
  typeMenuEl = null; typeMenuBtn = null; typeMenuIndex = -1;
}
function highlightTypeMenu() {
  [...typeMenu.children].forEach((c, i) => c.classList.toggle("active", i === typeMenuIndex));
}
function moveTypeMenu(d) {
  typeMenuIndex = (typeMenuIndex + d + TYPES.length) % TYPES.length;
  highlightTypeMenu();
}
function applyType(t) {
  const el = typeMenuEl;
  hideTypeMenu();
  if (!el || el.type === t) return;
  el.type = t;
  renderElements();  // typbyte kan ändra scenindelning
  scheduleSave();
}
function typeBtnKeydown(e, el, btn) {
  if (typeMenuOpen() && typeMenuBtn === btn) {
    if (e.key === "ArrowDown") { e.preventDefault(); moveTypeMenu(1); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); moveTypeMenu(-1); return; }
    if (e.key === "Enter" || e.key === "Tab" || e.key === " ") {
      e.preventDefault();
      applyType(TYPES[typeMenuIndex]);
      return;
    }
    if (e.key === "Escape") { e.preventDefault(); hideTypeMenu(); return; }
  } else if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    openTypeMenu(el, btn);
    return;
  }
  // Bokstavsgenväg: S/A/K/D/P/Ö/G hoppar direkt till den raden i listan (öppnar den om stängd).
  if (e.key.length === 1) {
    const idx = TYPES.findIndex((t) => TYPE_ABBR[t] === e.key.toUpperCase());
    if (idx !== -1) {
      e.preventDefault();
      if (!typeMenuOpen()) openTypeMenu(el, btn);
      typeMenuIndex = idx;
      highlightTypeMenu();
    }
  }
}
function insertElementAfter(el, type, text = "") {
  const i = project.elements.indexOf(el);
  if (i < 0) return null;
  const ne = newBlankElement(type || NEXT_TYPE[el.type] || "action");
  ne.text = text;
  project.elements.splice(i + 1, 0, ne);
  renderElements();
  focusElement(ne.id);
  scheduleSave();
  return ne;
}
function appendElement() {
  if (!project) return;
  const ne = newBlankElement(project.elements.length ? "action" : "scene_heading");
  project.elements.push(ne);
  renderElements();
  focusElement(ne.id);
  scheduleSave();
}
function highlightElement(id) {
  showSection("manus");
  const row = document.querySelector(`.fel[data-id="${id}"]`);
  if (!row) return;
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  row.classList.add("flash");
  setTimeout(() => row.classList.remove("flash"), 1500);
}

// ---- autospar (debounce + vid blur/navigering) ----
let saveTimer = null;
function setSaveState(state, msg) {
  const el = $("saveState");
  if (!el) return;
  el.className = "savestate" + (state === "saved" ? " ok" : state === "error" ? " err" : "");
  el.textContent = state === "dirty" ? "Osparat …"
    : state === "saving" ? "Sparar …"
    : state === "saved" ? "Sparat ✓"
    : state === "error" ? "Kunde inte spara: " + (msg || "")
    : "";
}
function scheduleSave() {
  if (!project) return;
  setSaveState("dirty");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 900);
}
async function saveNow() {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (!project) return;
  setSaveState("saving");
  try {
    // Sparar bara elementen; behåller lokala element-objekt (DOM-bindningarna) intakta.
    await api("PUT", `/api/projects/${project.id}`, { elements: project.elements });
    setSaveState("saved");
  } catch (e) {
    setSaveState("error", e.message);
  }
}
function flushSave() {
  if (saveTimer) saveNow();
}

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

// ---- importera befintligt manus (FDX / Fountain) ----
$("importFile").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (project.elements.length &&
      !confirm(`Importera "${file.name}" och lägga till sist i manuset?\n\n(En version sparas automatiskt så du kan ångra.)`)) {
    e.target.value = "";
    return;
  }
  setStatus(`Importerar ${file.name} ...`, true);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${project.id}/import`, { method: "POST", body: fd });
    if (!res.ok) {
      let msg = await res.text();
      try { msg = JSON.parse(msg).detail || msg; } catch (_) { /* visa råtext */ }
      throw new Error(msg);
    }
    const data = await res.json();
    project = data.project;
    renderElements();
    setStatus(`Importerade ${data.added} rader från ${file.name} ✓`);
  } catch (err) {
    setStatus("Kunde inte importera: " + err.message);
  }
  e.target.value = "";
};

// ---- versionshistorik ----
async function loadVersions() {
  if (!project) return;
  try {
    renderVersions((await api("GET", `/api/projects/${project.id}/versions`)).versions || []);
  } catch (_) { /* tyst */ }
}
function renderVersions(versions) {
  const box = $("versionsList");
  box.innerHTML = "";
  if (!versions.length) {
    box.innerHTML = '<p class="hint">Inga sparade versioner än.</p>';
    return;
  }
  for (const v of versions) {
    const label = v.label || "Auto";
    const row = document.createElement("div");
    row.className = "version-row";
    row.innerHTML = `<span class="ver-label">${esc(label)}</span>` +
      `<span class="ver-meta">${esc(v.ts)} · ${v.scenes} scener · ${v.rows} rader</span>`;
    const btn = document.createElement("button");
    btn.className = "linkbtn";
    btn.textContent = "Återställ";
    btn.onclick = () => restoreVersion(v.id, label);
    row.appendChild(btn);
    box.appendChild(row);
  }
}
$("saveVersionBtn").onclick = async () => {
  setVersionStatus("Sparar ...", true);
  try {
    const data = await api("POST", `/api/projects/${project.id}/versions`, { label: $("versionLabel").value });
    $("versionLabel").value = "";
    renderVersions(data.versions || []);
    setVersionStatus("Version sparad ✓");
  } catch (e) {
    setVersionStatus("Kunde inte spara: " + e.message);
  }
};
async function restoreVersion(vid, label) {
  if (!confirm(`Återställ manuset till "${label}"?\n\nNuvarande version sparas automatiskt så du kan ångra.`)) return;
  setVersionStatus("Återställer ...", true);
  try {
    const data = await api("POST", `/api/projects/${project.id}/versions/${vid}/restore`, {});
    project = data.project;
    renderElements();
    renderVersions(data.versions || []);
    setVersionStatus("Återställd ✓");
  } catch (e) {
    setVersionStatus("Kunde inte återställa: " + e.message);
  }
}

// ---- kommentarer ----
function sceneHeadingId(n) {
  let i = 0;
  for (const el of project.elements) {
    if (el.type === "scene_heading") { i += 1; if (i === n) return el.id; }
  }
  return null;
}
async function loadComments() {
  if (!project) return;
  try {
    renderComments((await api("GET", `/api/projects/${project.id}/comments`)).comments || []);
  } catch (_) { /* tyst */ }
}
function renderComments(comments) {
  const box = $("commentsList");
  box.innerHTML = "";
  if (!comments.length) { box.innerHTML = '<p class="hint">Inga kommentarer än.</p>'; return; }
  for (const c of comments) {
    const row = document.createElement("div");
    row.className = "comment-row";
    const head = document.createElement("div");
    head.className = "comment-head";
    head.innerHTML = `<span class="c-author">${esc(c.author || "")}</span><span class="c-meta">${esc(c.ts || "")}</span>`;
    if (c.scene) {
      const tag = document.createElement("button");
      tag.className = "linkbtn c-scene";
      tag.textContent = `Scen ${c.scene}`;
      tag.onclick = () => { const id = sceneHeadingId(c.scene); if (id != null) highlightElement(id); };
      head.appendChild(tag);
    }
    const del = document.createElement("button");
    del.className = "linkbtn danger";
    del.textContent = "✕";
    del.title = "Ta bort";
    del.onclick = () => deleteComment(c.id);
    head.appendChild(del);
    const body = document.createElement("div");
    body.className = "comment-text";
    body.textContent = c.text;
    row.append(head, body);
    box.appendChild(row);
  }
}
$("addCommentBtn").onclick = async () => {
  const text = $("commentText").value.trim();
  if (!text) { setCommentStatus("Skriv en kommentar först."); return; }
  const sceneVal = parseInt($("commentScene").value, 10);
  setCommentStatus("Sparar ...", true);
  try {
    const data = await api("POST", `/api/projects/${project.id}/comments`, {
      text, scene: Number.isFinite(sceneVal) ? sceneVal : null,
    });
    $("commentText").value = "";
    $("commentScene").value = "";
    renderComments(data.comments || []);
    setCommentStatus("");
  } catch (e) {
    setCommentStatus("Fel: " + e.message);
  }
};
$("commentText").onkeydown = (e) => { if (e.key === "Enter") $("addCommentBtn").click(); };
async function deleteComment(cid) {
  try {
    renderComments((await api("DELETE", `/api/projects/${project.id}/comments/${cid}`)).comments || []);
  } catch (e) {
    setCommentStatus("Fel: " + e.message);
  }
}

// ---- rapporter (karaktärer & scener) ----
function cleanCue(text) {
  // "ANNA (CONT'D)" / "ANNA (V.O.)" → "ANNA"
  return (text || "").trim().toUpperCase().replace(/\s*\(.*\)\s*$/, "").trim();
}
function characterReport() {
  const stats = {};
  let sceneNo = 0, cur = null;
  for (const el of project.elements) {
    if (el.type === "scene_heading") { sceneNo++; cur = null; continue; }
    if (el.type === "character") {
      const name = cleanCue(el.text);
      cur = name || null;
      if (!name) continue;
      const s = stats[name] || (stats[name] = { speeches: 0, words: 0, scenes: new Set() });
      s.speeches++;
      if (sceneNo) s.scenes.add(sceneNo);
    } else if (el.type === "dialogue" && cur) {
      const s = stats[cur];
      if (s) {
        s.words += (el.text || "").trim().split(/\s+/).filter(Boolean).length;
        if (sceneNo) s.scenes.add(sceneNo);
      }
    } else if (el.type === "action") {
      cur = null;  // handling bryter repliken
    }
  }
  return Object.entries(stats)
    .map(([name, s]) => ({ name, speeches: s.speeches, words: s.words, scenes: [...s.scenes].sort((a, b) => a - b) }))
    .sort((a, b) => b.speeches - a.speeches || b.words - a.words);
}
function sceneReport() {
  const groups = groupScenes();
  let runningLines = 0, no = 0;
  const rows = [];
  for (const g of groups) {
    const startPage = Math.floor(runningLines / LINES_PER_PAGE) + 1;
    const chars = new Set();
    for (const el of g.items) if (el.type === "character") { const nm = cleanCue(el.text); if (nm) chars.add(nm); }
    runningLines += g.items.reduce((m, el) => m + linesFor(el), 0);
    if (g.heading) {
      no++;
      rows.push({ no, heading: g.heading.text || "(scenrubrik)", id: g.heading.id, page: startPage, rows: g.items.length, chars: [...chars] });
    }
  }
  return rows;
}
function renderReports() {
  const box = $("reportBody");
  box.innerHTML = "";
  if (!project.elements.length) { box.innerHTML = '<p class="hint">Inget manus än.</p>'; return; }

  const chars = characterReport();
  const ch = document.createElement("div");
  ch.className = "report-section";
  ch.innerHTML = `<div class="report-title">Karaktärer (${chars.length})</div>`;
  if (!chars.length) {
    ch.innerHTML += '<p class="hint">Inga karaktärer med repliker än.</p>';
  } else {
    const list = document.createElement("div");
    list.className = "report-list";
    for (const c of chars) {
      const row = document.createElement("div");
      row.className = "report-row";
      row.innerHTML = `<span class="rr-name">${esc(c.name)}</span>` +
        `<span class="rr-meta">${c.speeches} repliker · ${c.words} ord · ${c.scenes.length} ${c.scenes.length === 1 ? "scen" : "scener"}</span>`;
      list.appendChild(row);
    }
    ch.appendChild(list);
  }
  box.appendChild(ch);

  const scenes = sceneReport();
  const sc = document.createElement("div");
  sc.className = "report-section";
  sc.innerHTML = `<div class="report-title">Scener (${scenes.length})</div>`;
  if (!scenes.length) {
    sc.innerHTML += '<p class="hint">Inga scenrubriker än.</p>';
  } else {
    const list = document.createElement("div");
    list.className = "report-list";
    for (const s of scenes) {
      const row = document.createElement("div");
      row.className = "report-row clickable";
      row.innerHTML = `<span class="rr-no">${s.no}</span>` +
        `<span class="rr-heading">${esc(s.heading)}</span>` +
        `<span class="rr-meta">s. ${s.page} · ${s.rows} rader${s.chars.length ? " · " + esc(s.chars.join(", ")) : ""}</span>`;
      row.onclick = () => highlightElement(s.id);
      list.appendChild(row);
    }
    sc.appendChild(list);
  }
  box.appendChild(sc);
}

// ---- sök & ersätt (i hela manuset) ----
const setFindStatus = mkStatus("findStatus");
let findCursor = -1;  // index i den senaste träfflistan (för "Nästa träff")
function findAllMatches(needle, matchCase) {
  const out = [];
  if (!needle) return out;
  const n = matchCase ? needle : needle.toLowerCase();
  project.elements.forEach((el, ei) => {
    const hay = matchCase ? (el.text || "") : (el.text || "").toLowerCase();
    let from = 0, k;
    while ((k = hay.indexOf(n, from)) !== -1) { out.push({ ei, start: k }); from = k + n.length; }
  });
  return out;
}
function replaceInString(hay, needle, repl, matchCase) {
  let out = "", count = 0, from = 0, k;
  const h = matchCase ? hay : hay.toLowerCase();
  const n = matchCase ? needle : needle.toLowerCase();
  while ((k = h.indexOf(n, from)) !== -1) {
    out += hay.slice(from, k) + repl;
    from = k + needle.length;
    count++;
  }
  return { text: out + hay.slice(from), count };
}
function findNext() {
  const needle = $("findText").value;
  const matchCase = $("findMatchCase").checked;
  const matches = findAllMatches(needle, matchCase);
  if (!matches.length) { setFindStatus(needle ? "Inga träffar" : ""); findCursor = -1; return; }
  findCursor = (findCursor + 1) % matches.length;
  setFindStatus(`Träff ${findCursor + 1} av ${matches.length}`);
  const m = matches[findCursor];
  const el = project.elements[m.ei];
  highlightElement(el.id);
  const ta = document.querySelector(`.fel[data-id="${el.id}"] .fel-text`);
  if (ta) { ta.focus(); ta.selectionStart = m.start; ta.selectionEnd = m.start + needle.length; }
}
function replaceOne() {
  const needle = $("findText").value;
  if (!needle) return;
  const repl = $("findReplace").value;
  const matchCase = $("findMatchCase").checked;
  const ta = document.activeElement;
  if (ta && ta.classList && ta.classList.contains("fel-text")) {
    const sel = ta.value.slice(ta.selectionStart, ta.selectionEnd);
    const eq = matchCase ? sel === needle : sel.toLowerCase() === needle.toLowerCase();
    if (eq) {
      const row = ta.closest(".fel");
      const el = row && project.elements.find((x) => x.id === +row.dataset.id);
      if (el) {
        const s = ta.selectionStart;
        el.text = ta.value.slice(0, s) + repl + ta.value.slice(ta.selectionEnd);
        ta.value = el.text;
        autogrow(ta);
        scheduleSave();
      }
    }
  }
  findNext();  // hoppa till nästa träff
}
function replaceAll() {
  const needle = $("findText").value;
  if (!needle) { setFindStatus("Skriv något att söka efter."); return; }
  const repl = $("findReplace").value;
  const matchCase = $("findMatchCase").checked;
  let count = 0;
  for (const el of project.elements) {
    const r = replaceInString(el.text || "", needle, repl, matchCase);
    if (r.count) { el.text = r.text; count += r.count; }
  }
  findCursor = -1;
  if (count) { renderElements(); scheduleSave(); }
  setFindStatus(count ? `Ersatte ${count} förekomst(er)` : "Inga träffar");
}
$("findNextBtn").onclick = findNext;
$("replaceOneBtn").onclick = replaceOne;
$("replaceAllBtn").onclick = replaceAll;
$("findText").oninput = () => { findCursor = -1; };
$("findText").onkeydown = (e) => { if (e.key === "Enter") findNext(); };
$("findReplace").onkeydown = (e) => { if (e.key === "Enter") replaceAll(); };

// ---- skrivskyddad delning (ägarsidan) ----
function shareUrl(token) {
  return location.origin + location.pathname + "?share=" + token;
}
function renderShareState(token) {
  const has = !!token;
  if (has) $("shareLink").value = shareUrl(token);
  $("shareLinkRow").hidden = !has;
  $("revokeShareBtn").hidden = !has;
  $("createShareBtn").disabled = has;
  $("createShareBtn").textContent = has ? "🔗 Delningslänk aktiv" : "🔗 Skapa delningslänk";
}
async function loadShareStatus() {
  if (!project) return;
  try {
    renderShareState((await api("GET", `/api/projects/${project.id}/share`)).token);
  } catch (_) { /* tyst */ }
}
$("createShareBtn").onclick = async () => {
  setShareStatus("Skapar länk ...", true);
  try {
    renderShareState((await api("POST", `/api/projects/${project.id}/share`)).token);
    setShareStatus("Länk skapad ✓ – vem som helst med länken kan läsa och kommentera.");
  } catch (e) { setShareStatus("Kunde inte skapa: " + e.message); }
};
$("revokeShareBtn").onclick = async () => {
  if (!confirm("Sluta dela? Den befintliga länken slutar fungera direkt.")) return;
  setShareStatus("Återkallar ...", true);
  try {
    await api("DELETE", `/api/projects/${project.id}/share`);
    renderShareState(null);
    setShareStatus("Delningen är avslutad.");
  } catch (e) { setShareStatus("Kunde inte avsluta: " + e.message); }
};
$("copyShareBtn").onclick = async () => {
  const link = $("shareLink").value;
  try {
    await navigator.clipboard.writeText(link);
  } catch (_) {
    $("shareLink").select();
    document.execCommand("copy");
  }
  setShareStatus("Länk kopierad ✓");
};

// ---- skrivskyddad tittarvy (delningslänk öppnad med ?share=…) ----
function renderSharedScript(elements) {
  const box = $("sharedScript");
  box.innerHTML = "";
  if (!elements.length) { box.innerHTML = '<p class="hint">Manuset är tomt än.</p>'; return; }
  let sceneNo = 0;
  let body = null;
  for (const el of elements) {
    const displayNo = el.scene_number || sceneNo + 1;
    if (el.type === "scene_heading" || body === null) {
      const scene = document.createElement("div");
      scene.className = "scene ro-scene";
      body = document.createElement("div");
      body.className = "scene-body";
      if (el.type === "scene_heading") scene.dataset.scene = displayNo;
      scene.appendChild(body);
      box.appendChild(scene);
    }
    const d = document.createElement("div");
    d.className = "ro-el ro-" + el.type;
    if (el.type === "scene_heading") {
      sceneNo += 1;
      d.innerHTML = `<span class="ro-scene-num">${displayNo}</span>` +
        `<span class="ro-scene-text">${esc(el.text || "(scenrubrik)")}</span>`;
    } else {
      d.textContent = el.text || "";
    }
    body.appendChild(d);
  }
}
function renderSharedComments(comments) {
  const box = $("sharedCommentsList");
  box.innerHTML = "";
  if (!comments.length) { box.innerHTML = '<p class="hint">Inga kommentarer än – bli först!</p>'; return; }
  for (const c of comments) {
    const row = document.createElement("div");
    row.className = "comment-row";
    const head = document.createElement("div");
    head.className = "comment-head";
    head.innerHTML = `<span class="c-author">${esc(c.author || "")}</span><span class="c-meta">${esc(c.ts || "")}</span>`;
    if (c.scene) {
      const tag = document.createElement("button");
      tag.className = "linkbtn c-scene";
      tag.textContent = `Scen ${c.scene}`;
      tag.onclick = () => {
        const card = document.querySelector(`#sharedScript .ro-scene[data-scene="${c.scene}"]`);
        if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
      };
      head.appendChild(tag);
    }
    const body = document.createElement("div");
    body.className = "comment-text";
    body.textContent = c.text;
    row.append(head, body);
    box.appendChild(row);
  }
}
async function openShared(token) {
  sharedToken = token;
  $("loginOverlay").hidden = true;
  const nav = document.querySelector(".topnav");
  if (nav) nav.hidden = true;
  $("sharedCommentAuthor").value = localStorage.getItem("scriptvoice_guest_name") || "";
  try {
    const data = await api("GET", `/api/shared/${token}`);
    $("sharedTitle").textContent = data.title || "Manus";
    $("sharedAuthor").textContent = data.author ? "av " + data.author : "";
    renderSharedScript(data.elements || []);
    renderSharedComments(data.comments || []);
    showView("shared");
    applyPaper();
  } catch (e) {
    showView("shared");
    $("sharedTitle").textContent = "Länken fungerar inte";
    $("sharedAuthor").textContent = "";
    $("sharedScript").innerHTML = `<p class="hint">${esc(e.message)}</p>`;
    $("sharedCommentsList").innerHTML = "";
    const addRow = document.querySelector("#view-shared .comment-add");
    if (addRow) addRow.style.display = "none";
  }
}
$("sharedAddCommentBtn").onclick = async () => {
  const text = $("sharedCommentText").value.trim();
  if (!text) { setSharedCommentStatus("Skriv en kommentar först."); return; }
  const sceneVal = parseInt($("sharedCommentScene").value, 10);
  const author = $("sharedCommentAuthor").value.trim();
  setSharedCommentStatus("Sparar ...", true);
  try {
    const data = await api("POST", `/api/shared/${sharedToken}/comments`, {
      author, text, scene: Number.isFinite(sceneVal) ? sceneVal : null,
    });
    $("sharedCommentText").value = "";
    $("sharedCommentScene").value = "";
    if (author) localStorage.setItem("scriptvoice_guest_name", author);
    renderSharedComments(data.comments || []);
    setSharedCommentStatus("Tack för din kommentar ✓");
  } catch (e) { setSharedCommentStatus("Fel: " + e.message); }
};
$("sharedCommentText").onkeydown = (e) => { if (e.key === "Enter") $("sharedAddCommentBtn").click(); };
$("sharedPaperToggle").onclick = () => {
  localStorage.setItem(PAPER_KEY, localStorage.getItem(PAPER_KEY) === "1" ? "0" : "1");
  applyPaper();
};

// ---- fråga manuset (AI-assistent) ----
$("askBtn").onclick = async () => {
  const q = $("askInput").value.trim();
  if (!q) { setAskStatus("Skriv en fråga först."); return; }
  setAskStatus("Tänker ...", true);
  $("askAnswer").hidden = true;
  try {
    const data = await api("POST", `/api/projects/${project.id}/ask`, { question: q, provider: $("aiEngine").value });
    $("askAnswer").textContent = data.answer || "(tomt svar)";
    $("askAnswer").hidden = false;
    setAskStatus("");
  } catch (e) {
    setAskStatus("Fel: " + e.message);
  }
};
$("askInput").onkeydown = (e) => { if (e.key === "Enter") $("askBtn").click(); };

// ---- skriv ut / spara som PDF (fristående, manusformaterat dokument) ----
$("printBtn").onclick = () => {
  if (!project || !project.elements.length) { setStatus("Inget manus att skriva ut än."); return; }
  const css = `
    @page { size: Letter; margin: 1in 1in 1in 1.5in; }
    * { margin: 0; }
    body { font: 12pt/1.0 "Courier New", Courier, monospace; color: #000; }
    .title-page { height: 8.5in; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; page-break-after: always; }
    .title-page .t { text-transform: uppercase; text-decoration: underline; }
    .title-page .by { margin-top: 1.5em; }
    .contact { margin-top: 3in; align-self: flex-start; text-align: left; white-space: pre-line; }
    .el { white-space: pre-wrap; }
    .scene_heading { text-transform: uppercase; font-weight: bold; margin-top: 1.5em; }
    .action, .general { margin-top: 1em; }
    .character { text-transform: uppercase; margin-top: 1em; margin-left: 2.2in; }
    .dialogue { margin-left: 1in; max-width: 3.5in; }
    .parenthetical { margin-left: 1.6in; max-width: 2.5in; }
    .transition { text-transform: uppercase; text-align: right; margin-top: 1em; }
    .character, .parenthetical, .dialogue { page-break-inside: avoid; }
  `;
  const t = (project.title || "").trim(), a = (project.author || "").trim(), c = (project.contact || "").trim();
  let body = "";
  if (t || a || c) {
    body += `<div class="title-page"><div class="t">${esc(t.toUpperCase())}</div>`;
    if (a) body += `<div class="by">Written by</div><div>${esc(a)}</div>`;
    if (c) body += `<div class="contact">${esc(c)}</div>`;
    body += `</div>`;
  }
  for (const el of project.elements) body += `<div class="el ${el.type}">${esc(el.text || "")}</div>`;
  const w = window.open("", "_blank");
  if (!w) { setStatus("Tillåt popup-fönster för att skriva ut / spara som PDF."); return; }
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(t || "Manus")}</title><style>${css}</style></head><body onload="window.focus();window.print();">${body}</body></html>`);
  w.document.close();
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
  if (SHARE_TOKEN) { openShared(SHARE_TOKEN); return; }  // delningslänk: skrivskyddad vy, ingen inloggning
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
  const label = on ? "🌙 Mörkt läge" : "📄 Vitt papper";
  $("elements").classList.toggle("paper", on);
  $("paperToggle").textContent = label;
  const ss = $("sharedScript");
  if (ss) ss.classList.toggle("paper", on);
  const sp = $("sharedPaperToggle");
  if (sp) sp.textContent = label;
}
$("paperToggle").onclick = () => {
  localStorage.setItem(PAPER_KEY, localStorage.getItem(PAPER_KEY) === "1" ? "0" : "1");
  applyPaper();
};

// ---- init ----
applyPaper();
boot();
