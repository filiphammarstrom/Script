"use strict";
const $ = (id) => document.getElementById(id);
const TYPES = ["scene_heading", "action", "character", "dialogue", "parenthetical", "transition", "general"];
let project = null;

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
function setStatus(msg, busy = false) {
  const s = $("status");
  s.textContent = msg;
  s.className = "status" + (busy ? " busy" : "");
}

// ---- bas-AI (globala regler) ----
async function loadGlobal() {
  const s = await api("GET", "/api/settings");
  $("globalDirectives").value = s.directives || "";
}
$("saveGlobalBtn").onclick = async () => {
  await api("PUT", "/api/settings", { directives: $("globalDirectives").value });
  setStatus("Bas-AI sparad.");
};
$("rulesFile").onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const existing = $("globalDirectives").value.trim();
    $("globalDirectives").value = existing ? existing + "\n\n" + reader.result : reader.result;
    setStatus(`Läste in ${file.name} – tryck "Spara bas-AI".`);
  };
  reader.readAsText(file);
  e.target.value = "";
};

// ---- projekt ----
async function loadProjectList() {
  const list = await api("GET", "/api/projects");
  const sel = $("projectSelect");
  const current = sel.value;
  sel.innerHTML = '<option value="">– välj projekt –</option>';
  for (const p of list) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = `${p.title} (${p.scenes} scener)`;
    sel.appendChild(o);
  }
  sel.value = current;
}
$("newProjectBtn").onclick = async () => {
  const title = $("newTitle").value.trim() || "Namnlöst projekt";
  const p = await api("POST", "/api/projects", { title });
  $("newTitle").value = "";
  await loadProjectList();
  $("projectSelect").value = p.id;
  openProject(p);
};
$("projectSelect").onchange = async (e) => {
  if (!e.target.value) {
    $("projectArea").hidden = true;
    return;
  }
  openProject(await api("GET", `/api/projects/${e.target.value}`));
};

function openProject(p) {
  project = p;
  $("projectArea").hidden = false;
  $("projTitle").value = p.title;
  $("projContext").value = p.context;
  $("projDirectives").value = p.directives;
  renderBible();
  renderElements();
  $("clarPanel").hidden = true;
  $("clarifications").innerHTML = "";
}

$("saveProjectBtn").onclick = async () => {
  project = await api("PUT", `/api/projects/${project.id}`, {
    title: $("projTitle").value,
    context: $("projContext").value,
    directives: $("projDirectives").value,
  });
  await loadProjectList();
  setStatus("Projekt sparat.");
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
$("transcribeBtn").onclick = async () => {
  const f = $("audioFile").files[0];
  if (!f) {
    setStatus("Välj en ljudfil först.");
    return;
  }
  setStatus("Transkriberar ljud (kan ta en stund) ...", true);
  try {
    const fd = new FormData();
    fd.append("file", f);
    const res = await fetch(`/api/projects/${project.id}/transcribe`, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.text()) || res.status);
    const data = await res.json();
    const existing = $("inputText").value.trim();
    $("inputText").value = existing ? existing + "\n\n" + data.text : data.text;
    $("audioFile").value = "";
    $("audioName").textContent = "";
    setStatus("Transkribering klar – granska och tryck Analysera.");
  } catch (e) {
    setStatus("Fel vid transkribering: " + e.message);
  }
};

// ---- analys ----
$("analyzeBtn").onclick = async () => {
  const text = $("inputText").value.trim();
  if (!text) return;
  setStatus("AI:n analyserar ...", true);
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
    box.appendChild(row);
  }
}
function highlightElement(id) {
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
loadGlobal();
loadProjectList();
