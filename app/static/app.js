"use strict";
const $ = (id) => document.getElementById(id);
const TYPES = ["scene_heading", "action", "character", "dialogue", "parenthetical", "transition", "general"];
let project = null;
let currentRulesFilename = "";

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error((await res.text()) || res.status);
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

// ---- vy-navigering ----
function showView(name) {
  for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== "view-" + name;
  $("navProjects").classList.toggle("active", name === "projects");
  $("navSettings").classList.toggle("active", name === "settings");
}
$("navProjects").onclick = async () => {
  await loadProjectList();
  showView("projects");
};
$("navSettings").onclick = () => showView("settings");

// ---- bas-AI (globala regler) ----
async function loadGlobal() {
  const s = await api("GET", "/api/settings");
  $("globalDirectives").value = s.directives || "";
  currentRulesFilename = s.rules_filename || "";
  renderActiveRules();
}
function renderActiveRules(pending) {
  const chars = $("globalDirectives").value.length;
  const box = $("rulesActive");
  if (currentRulesFilename) {
    box.innerHTML = `Aktiv regelbok: <strong>${esc(currentRulesFilename)}</strong> · ${chars} tecken` +
      (pending ? ' <em>(ej sparad)</em>' : "");
  } else if (chars > 0) {
    box.innerHTML = `Inskriven text · ${chars} tecken` + (pending ? ' <em>(ej sparad)</em>' : "");
  } else {
    box.textContent = "Ingen regelbok uppladdad än.";
  }
}
$("saveGlobalBtn").onclick = async () => {
  setGlobalStatus("Sparar ...", true);
  try {
    await api("PUT", "/api/settings", {
      directives: $("globalDirectives").value,
      rules_filename: currentRulesFilename,
    });
    renderActiveRules();
    setGlobalStatus("Bas-AI sparad ✓");
  } catch (e) {
    setGlobalStatus("Kunde inte spara: " + e.message);
  }
};
$("rulesFile").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  setGlobalStatus(`Läser in ${file.name} ...`, true);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/extract-text", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.text()) || res.status);
    const { text } = await res.json();
    const existing = $("globalDirectives").value.trim();
    $("globalDirectives").value = existing ? existing + "\n\n" + text : text;
    currentRulesFilename = file.name;
    renderActiveRules(true);
    setGlobalStatus(`Inläst ${file.name} (${text.length} tecken) – tryck "Spara bas-AI".`);
  } catch (err) {
    setGlobalStatus("Kunde inte läsa filen: " + err.message);
  }
  e.target.value = "";
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
    });
    $("projHeadTitle").textContent = project.title;
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

// ---- story-bibel ----
function renderBible() {
  const b = project.story_bible || { characters: [], locations: [], notes: [] };
  const chars =
    b.characters
      .map((c) => {
        const extra = [c.aliases?.length ? c.aliases.join(", ") : null, c.languages?.length ? c.languages.join("/") : null]
          .filter(Boolean)
          .join("; ");
        return esc(c.name) + (extra ? ` <em>(${esc(extra)})</em>` : "");
      })
      .join(", ") || "–";
  $("storyBible").innerHTML =
    `<div><strong>Karaktärer:</strong> ${chars}</div>` +
    `<div><strong>Platser:</strong> ${esc(b.locations.join(", ") || "–")}</div>` +
    `<div><strong>Anteckningar:</strong> ${esc(b.notes.join("; ") || "–")}</div>`;
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
    setStatus("Klistra in eller transkribera text först.");
    return;
  }
  setStatus("Analyserar ...", true);
  try {
    const data = await api("POST", `/api/projects/${project.id}/analyze`, { text });
    project = data.project;
    renderBible();
    renderElements();
    renderClarifications(data.clarifications || []);
    $("inputText").value = "";
    setStatus(`Klart. ${(data.clarifications || []).length} sak(er) att ev. förtydliga.`);
  } catch (e) {
    setStatus("Fel: " + e.message);
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
function renderElements() {
  const box = $("elements");
  box.innerHTML = "";
  if (!project.elements.length) {
    box.innerHTML = '<p class="hint">Inget manus än – transkribera/klistra in text och tryck Analysera.</p>';
    return;
  }
  for (const el of project.elements) {
    const row = document.createElement("div");
    row.className = "el el-" + el.type + (el.confidence !== "high" ? " low-conf" : "");
    row.dataset.id = el.id;

    const sel = document.createElement("select");
    for (const t of TYPES) {
      const o = document.createElement("option");
      o.value = t;
      o.textContent = t;
      if (t === el.type) o.selected = true;
      sel.appendChild(o);
    }
    sel.onchange = () => {
      el.type = sel.value;
      row.className = "el el-" + el.type;
    };

    const inp = document.createElement("textarea");
    inp.value = el.text;
    inp.rows = 1;
    inp.oninput = () => {
      el.text = inp.value;
    };

    row.appendChild(sel);
    row.appendChild(inp);
    if (el.is_gap) {
      const tag = document.createElement("span");
      tag.className = "gap";
      tag.textContent = "LUCKA";
      row.appendChild(tag);
    }

    const actions = document.createElement("div");
    actions.className = "el-actions";
    const mkBtn = (label, title, fn) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "iconbtn";
      b.textContent = label;
      b.title = title;
      b.onclick = fn;
      return b;
    };
    actions.appendChild(mkBtn("↑", "Flytta upp", () => moveElement(el, -1)));
    actions.appendChild(mkBtn("↓", "Flytta ner", () => moveElement(el, 1)));
    actions.appendChild(mkBtn("✕", "Ta bort raden", () => deleteElement(el)));
    row.appendChild(actions);

    box.appendChild(row);
  }
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
function highlightElement(id) {
  showProjectTab("manus");
  const row = document.querySelector(`.el[data-id="${id}"]`);
  if (!row) return;
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

// ---- init ----
showView("projects");
loadGlobal();
loadProjectList();
