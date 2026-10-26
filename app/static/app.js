"use strict";

const $ = (sel) => document.querySelector(sel);
const KIND = { pdf: "📄", lesson: "🎙️", text: "📝" };
const STATUS = { pending: "wartet", processing: "in Arbeit", ready: "bereit", error: "Fehler" };
const NO_SUBJECT = "Ohne Fach";

let docs = [];
let subjects = [];
let subject = loadSubject();   // null = alle Fächer
let currentDoc = null;
let pollTimer = null;
let dueBySubject = {};

// --- Hilfsfunktionen ---------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail;
    throw new Error(detail || `Fehler ${res.status}`);
  }
  return data;
}

const postJSON = (path, body, method = "POST") =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Sehr einfacher Markdown-Renderer (Überschriften, Listen, fett) – Text wird vorher escaped.
function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  for (const line of lines) {
    const item = line.match(/^\s*[-*•]\s+(.*)/);
    if (item) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(item[1])}</li>`;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    const h = line.match(/^(#{1,3})\s+(.*)/);
    if (h) html += `<h3>${inline(h[2])}</h3>`;
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}

async function busy(button, fn) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Bitte warten…";
  try { return await fn(); }
  catch (e) { alert(e.message); }
  finally { button.disabled = false; button.textContent = label; }
}

function loadSubject() {
  try { const v = localStorage.getItem("subject"); return v === null ? null : JSON.parse(v); } catch { return null; }
}

function saveSubject() {
  try { localStorage.setItem("subject", JSON.stringify(subject)); } catch { /* egal */ }
}

const subjectName = (s) => (s === null ? "Alle Fächer" : s || NO_SUBJECT);
const subjectQuery = () => (subject === null ? "" : `?subject=${encodeURIComponent(subject)}`);

function show(view) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  document.querySelectorAll("nav button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view || (view === "doc" && b.dataset.view === "notes")));
  if (view === "cards") loadCards();
  if (view === "settings") loadSettings();
  if (view === "record") fillSubjectSelect($("#rec-subject"), subject ?? "", true);
}

// --- Status ------------------------------------------------------------------

async function loadStatus() {
  try {
    const s = await api("/api/status");
    const bar = $("#status-bar");
    const problems = [];
    if (!s.key_set) problems.push('Die KI ist noch nicht eingerichtet. <a href="#" data-go="settings">Jetzt OpenRouter-Schlüssel eintragen</a>.');
    else if (!s.chat_model) problems.push('Bitte ein KI-Modell wählen: <a href="#" data-go="settings">Einstellungen</a>.');
    bar.hidden = problems.length === 0;
    bar.innerHTML = problems.join("<br>");
    dueBySubject = s.flashcards.due_by_subject || {};
    const due = subject === null ? s.flashcards.due : (dueBySubject[subject] || 0);
    const badge = $("#due-badge");
    badge.hidden = !due;
    badge.textContent = due;
    renderSubjects();
  } catch { /* Server neu gestartet */ }
}

$("#status-bar").addEventListener("click", (e) => {
  const a = e.target.closest("[data-go]");
  if (a) { e.preventDefault(); show(a.dataset.go); }
});

// --- Fächer ------------------------------------------------------------------

async function loadSubjects() {
  subjects = await api("/api/subjects");
  if (subject !== null && !subjects.some((s) => s.subject === subject)) { subject = null; saveSubject(); }
  renderSubjects();
}

function renderSubjects() {
  const total = subjects.reduce((n, s) => n + s.count, 0);
  const items = [{ subject: null, count: total }, ...subjects];
  $("#subject-list").innerHTML = items.map((s, i) => {
    const due = s.subject === null ? 0 : dueBySubject[s.subject] || 0;
    return `<li data-i="${i}" class="${s.subject === subject ? "active" : ""}">
      <span class="name">${esc(subjectName(s.subject))}</span>
      <span class="count">${s.count}${due ? ` · 🃏${due}` : ""}</span>
      ${s.subject ? `<button class="icon" data-rename="${i}" title="Umbenennen">✏️</button>` : ""}
    </li>`;
  }).join("");
  $("#subject-list").onclick = async (e) => {
    const rename = e.target.closest("[data-rename]");
    if (rename) {
      const old = items[rename.dataset.rename].subject;
      const name = prompt(`Neuer Name für „${old}“ (gleicher Name wie ein anderes Fach = zusammenlegen):`, old);
      if (name && name.trim() && name.trim() !== old) {
        await postJSON("/api/subjects/rename", { old, new: name.trim() });
        if (subject === old) { subject = name.trim(); saveSubject(); }
        await refreshAll();
      }
      return;
    }
    const li = e.target.closest("li[data-i]");
    if (!li) return;
    subject = items[li.dataset.i].subject;
    saveSubject();
    renderSubjects();
    renderDocs();
    updateHeadings();
    loadStatus();
    if (!$("#view-cards").hidden) loadCards();
    if (!$("#view-doc").hidden) show("notes");
  };
}

function updateHeadings() {
  $("#notes-heading").textContent = subject === null ? "Alle Notizen" : subjectName(subject);
  $("#learn-heading").textContent = `💬 Lernen: ${subjectName(subject)}`;
  $("#cards-heading").textContent = `🃏 Karteikarten: ${subjectName(subject)}`;
  $("#subject-quiz").disabled = $("#subject-cards").disabled = subject === null;
  $("#subject-quiz").title = subject === null ? "Wähle links zuerst ein Fach" : "";
}

function fillSubjectSelect(select, selected, allowNew = false) {
  const names = subjects.map((s) => s.subject);
  if (selected && !names.includes(selected)) names.push(selected);
  select.innerHTML = names.map((n) => `<option value="${esc(n)}" ${n === selected ? "selected" : ""}>${esc(n || NO_SUBJECT)}</option>`).join("") +
    (allowNew ? `<option value="__new__">➕ Neues Fach…</option>` : "");
  if (allowNew) select.onchange = () => ($("#rec-subject-new").hidden = select.value !== "__new__");
}

// --- Notizen -----------------------------------------------------------------

async function loadDocs() {
  docs = await api("/api/documents");
  renderDocs();
  const working = docs.some((d) => d.status === "pending" || d.status === "processing");
  clearTimeout(pollTimer);
  if (working) pollTimer = setTimeout(refresh, 3000);
}

async function refreshAll() {
  await Promise.all([loadSubjects(), loadDocs()]);
  updateHeadings();
  loadStatus();
}

async function refresh() {
  await loadDocs();
  if (currentDoc && !$("#view-doc").hidden) openDoc(currentDoc.id, { keepOutput: true });
}

// Ordnet die Notizen eines Fachs: OneNote-Abschnitte nach Notizbuch/Abschnittsgruppe, dann Lektionen und eigene Notizen.
function groupDocs(list) {
  const groups = new Map();
  const add = (key, doc, label) => {
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({ doc, label });
  };
  for (const d of list) {
    if (d.external_id && d.title.includes(" / ")) {
      const parts = d.title.split(" / ");
      add("📓 " + parts.slice(0, -1).join(" / "), d, parts[parts.length - 1]);
    } else if (d.kind === "lesson") add("🎙️ Lektionen", d, d.title);
    else add("📝 Eigene Notizen und PDFs", d, d.title);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b, "de"));
}

function renderDocs() {
  const q = $("#filter").value.toLowerCase();
  const shown = docs.filter((d) => (subject === null || d.subject === subject) && d.title.toLowerCase().includes(q));
  const tree = $("#doc-tree");
  if (!shown.length) {
    tree.innerHTML = `<p class="hint">${docs.length ? "Keine Notizen gefunden." : "Noch keine Notizen. Synchronisiere OneNote oder nimm eine Lektion auf."}</p>`;
    return;
  }
  tree.innerHTML = groupDocs(shown).map(([group, items]) => `
    <details class="group" open>
      <summary><span>${esc(group)}</span><span class="hint">${items.length}</span></summary>
      <ul class="doc-list">
        ${items.sort((a, b) => a.label.localeCompare(b.label, "de")).map(({ doc: d, label }) => `
          <li data-id="${d.id}">
            <span class="title">${KIND[d.kind] || ""} ${esc(label)}
              ${subject === null && d.subject ? `<span class="tag">${esc(d.subject)}</span>` : ""}
              ${d.progress ? `<span class="hint small"> · ${esc(d.progress)}</span>` : ""}</span>
            <span class="row">
              ${d.has_file && d.kind === "pdf" ? `<a href="/api/documents/${d.id}/file" target="_blank" class="pdf-link" title="PDF öffnen">PDF</a>` : ""}
              <span class="pill ${d.status}">${STATUS[d.status] || d.status}</span>
            </span>
          </li>`).join("")}
      </ul>
    </details>`).join("");
}

$("#doc-tree").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const li = e.target.closest("li[data-id]");
  if (li) openDoc(Number(li.dataset.id));
});
$("#filter").addEventListener("input", renderDocs);

async function openDoc(id, { keepOutput = false } = {}) {
  const doc = await api(`/api/documents/${id}`);
  currentDoc = doc;
  show("doc");
  $("#doc-title").textContent = doc.title;
  const meta = [KIND[doc.kind], STATUS[doc.status]];
  if (doc.progress) meta.push(doc.progress);
  $("#doc-meta").innerHTML = esc(meta.filter(Boolean).join(" · ")) +
    (doc.error ? `<br><span class="error-text">${esc(doc.error)}</span>` : "");
  fillSubjectSelect($("#doc-subject"), doc.subject);
  const file = $("#doc-file");
  file.hidden = !doc.source_path;
  file.href = `/api/documents/${doc.id}/file`;
  file.textContent = doc.kind === "lesson" ? "🔊 Aufnahme anhören" : "📄 Original öffnen";
  const ready = doc.status === "ready";
  ["#doc-summary", "#doc-quiz", "#doc-cards"].forEach((s) => ($(s).disabled = !ready));
  $("#doc-retry").hidden = doc.status !== "error";
  $("#doc-summary-box").innerHTML = doc.summary ? `<div class="md">${md(doc.summary)}</div>` : "";
  $("#doc-clean").textContent = doc.clean_text;
  $("#doc-raw").textContent = doc.raw_text;
  $("#doc-clean-box").hidden = !doc.clean_text || doc.clean_text === doc.raw_text;
  $("#doc-raw-box").hidden = !doc.raw_text;
  if (!keepOutput) $("#doc-output").innerHTML = "";
}

$("#doc-subject").addEventListener("change", async (e) => {
  await postJSON(`/api/documents/${currentDoc.id}`, { subject: e.target.value }, "PATCH");
  currentDoc.subject = e.target.value;
  refreshAll();
});

$("#back").addEventListener("click", () => { currentDoc = null; show("notes"); loadDocs(); });

$("#doc-delete").addEventListener("click", async () => {
  if (!confirm(`„${currentDoc.title}“ wirklich löschen? Das kann nicht rückgängig gemacht werden.`)) return;
  await api(`/api/documents/${currentDoc.id}`, { method: "DELETE" });
  currentDoc = null;
  show("notes");
  refreshAll();
});

$("#doc-retry").addEventListener("click", async () => {
  await postJSON(`/api/documents/${currentDoc.id}/retry`);
  refresh();
});

$("#doc-summary").addEventListener("click", (e) => busy(e.target, async () => {
  $("#doc-output").innerHTML = `<p class="spinner">Die KI fasst zusammen…</p>`;
  try {
    const { summary } = await postJSON(`/api/documents/${currentDoc.id}/summary`);
    $("#doc-summary-box").innerHTML = `<div class="md">${md(summary)}</div>`;
  } finally { $("#doc-output").innerHTML = ""; }
}));

$("#doc-cards").addEventListener("click", (e) => busy(e.target, async () => {
  $("#doc-output").innerHTML = `<p class="spinner">Erstelle Karteikarten…</p>`;
  const { created } = await postJSON(`/api/documents/${currentDoc.id}/flashcards`);
  $("#doc-output").innerHTML = `<p>✅ ${created} Karteikarten erstellt. Du findest sie unter „Karteikarten“.</p>`;
  loadStatus();
}));

$("#doc-quiz").addEventListener("click", (e) => busy(e.target, async () => {
  $("#doc-output").innerHTML = `<p class="spinner">Erstelle Quiz…</p>`;
  const { fragen } = await postJSON(`/api/documents/${currentDoc.id}/quiz`);
  renderQuiz($("#doc-output"), fragen);
}));

function renderQuiz(box, questions) {
  let score = 0, answered = 0;
  box.innerHTML = `<h3>Quiz</h3><p class="hint quiz-score"></p>` + questions.map((q, i) => `
    <div class="quiz-q" data-i="${i}">
      <strong>${i + 1}. ${esc(q.frage)}</strong>
      ${q.optionen.map((o, j) => `<button data-j="${j}">${esc(o)}</button>`).join("")}
      <p class="hint expl" hidden>${esc(q.erklaerung)}</p>
    </div>`).join("");
  box.querySelectorAll(".quiz-q").forEach((div) => {
    const q = questions[div.dataset.i];
    div.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn || div.dataset.done) return;
      div.dataset.done = "1";
      answered++;
      if (Number(btn.dataset.j) === q.richtig) score++;
      else btn.classList.add("wrong");
      div.querySelectorAll("button")[q.richtig].classList.add("right");
      div.querySelector(".expl").hidden = !q.erklaerung;
      box.querySelector(".quiz-score").textContent = `${score} von ${answered} richtig` +
        (answered === questions.length ? ` – fertig! ${score === questions.length ? "🎉" : ""}` : "");
    });
  });
}

// --- OneNote -----------------------------------------------------------------

let onenoteTimer = null;

function renderOnenote(s) {
  $("#onenote-box").hidden = !s.available;
  $("#onenote-sync").disabled = s.running;
  let text = "";
  if (s.running) text = s.message || "Läuft…";
  else if (s.error) text = "⚠️ " + s.error;
  else if (s.result) {
    const r = s.result;
    text = `✅ ${r.sections} Abschnitte: ${r.new} neu, ${r.updated} aktualisiert, ${r.unchanged} unverändert, ${r.empty ?? 0} leer`;
    if (r.failed.length) text += `, ${r.failed.length} fehlgeschlagen (${r.failed.map((f) => f.path).join(", ")})`;
  }
  $("#onenote-status").textContent = text;
  clearTimeout(onenoteTimer);
  if (s.running) onenoteTimer = setTimeout(loadOnenote, 2000);
  else if (s.result) refreshAll();
}

async function loadOnenote() {
  try { renderOnenote(await api("/api/onenote")); } catch { /* ignorieren */ }
}

$("#onenote-sync").addEventListener("click", async () => {
  try { renderOnenote(await postJSON("/api/onenote/sync")); } catch (e) { alert(e.message); }
});

// --- Hinzufügen --------------------------------------------------------------

$("#pdf-upload").addEventListener("click", (e) => busy(e.target, async () => {
  const files = $("#pdf-files").files;
  if (!files.length) throw new Error("Bitte zuerst PDF-Dateien auswählen.");
  const form = new FormData();
  for (const f of files) form.append("files", f);
  form.append("subject", subject ?? "");
  await api("/api/upload/pdf", { method: "POST", body: form });
  $("#pdf-files").value = "";
  refreshAll();
}));

$("#note-save").addEventListener("click", (e) => busy(e.target, async () => {
  await postJSON("/api/notes", { title: $("#note-title").value || "Notiz", text: $("#note-text").value, subject: subject ?? "" });
  $("#note-title").value = $("#note-text").value = "";
  refreshAll();
}));

function recordSubject() {
  const sel = $("#rec-subject").value;
  return sel === "__new__" ? $("#rec-subject-new").value.trim() : sel;
}

async function uploadAudio(blob, filename) {
  const form = new FormData();
  form.append("file", blob, filename);
  form.append("title", $("#rec-title").value);
  form.append("subject", recordSubject());
  await api("/api/upload/audio", { method: "POST", body: form });
  $("#rec-title").value = "";
  refreshAll();
}

$("#audio-upload").addEventListener("click", (e) => busy(e.target, async () => {
  const file = $("#audio-file").files[0];
  if (!file) throw new Error("Bitte zuerst eine Audiodatei auswählen.");
  await uploadAudio(file, file.name);
  $("#audio-file").value = "";
  alert("Hochgeladen. Die Lektion wird jetzt im Hintergrund verarbeitet.");
}));

// --- Aufnahme ----------------------------------------------------------------

let recorder = null, recChunks = [], recStart = 0, recTimer = null, wakeLock = null;

$("#rec-start").addEventListener("click", async () => {
  if ($("#rec-subject").value === "__new__" && !recordSubject()) { alert("Bitte einen Namen für das neue Fach eingeben."); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true } });
    const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"].find((m) => MediaRecorder.isTypeSupported(m));
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 48000 } : undefined);
    recChunks = [];
    recorder.ondataavailable = (e) => e.data.size && recChunks.push(e.data);
    recorder.start(10000); // alle 10 s ein Stück, damit bei einem Absturz weniger verloren geht
    recStart = Date.now();
    recTimer = setInterval(() => {
      const s = Math.floor((Date.now() - recStart) / 1000);
      $("#rec-time").textContent = `🔴 ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }, 1000);
    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* nicht unterstützt */ }
    $("#rec-start").hidden = true;
    $("#rec-stop").hidden = false;
  } catch (e) {
    alert("Mikrofon konnte nicht gestartet werden: " + e.message);
  }
});

$("#rec-stop").addEventListener("click", () => {
  if (!recorder) return;
  recorder.onstop = async () => {
    recorder.stream.getTracks().forEach((t) => t.stop());
    clearInterval(recTimer);
    wakeLock?.release().catch(() => {});
    $("#rec-time").textContent = "Speichere…";
    const type = recorder.mimeType || "audio/webm";
    const ext = type.includes("ogg") ? "ogg" : "webm";
    const blob = new Blob(recChunks, { type });
    try {
      await uploadAudio(blob, `aufnahme-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${ext}`);
      $("#rec-time").textContent = "✅ Gespeichert, wird transkribiert";
    } catch (e) {
      // Aufnahme nicht verlieren: als Datei anbieten
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `aufnahme.${ext}`;
      a.click();
      alert("Hochladen fehlgeschlagen (" + e.message + "). Die Aufnahme wurde als Datei heruntergeladen.");
      $("#rec-time").textContent = "";
    }
    recorder = null;
    $("#rec-start").hidden = false;
    $("#rec-stop").hidden = true;
  };
  recorder.stop();
});

window.addEventListener("beforeunload", (e) => { if (recorder) { e.preventDefault(); e.returnValue = ""; } });

// --- Lernen ------------------------------------------------------------------

$("#subject-quiz").addEventListener("click", (e) => busy(e.target, async () => {
  $("#learn-output").innerHTML = `<p class="spinner">Erstelle Quiz über ${esc(subjectName(subject))}…</p>`;
  try {
    const { fragen } = await postJSON("/api/subjects/quiz", { subject });
    renderQuiz($("#learn-output"), fragen);
  } catch (err) { $("#learn-output").innerHTML = ""; throw err; }
}));

$("#subject-cards").addEventListener("click", (e) => busy(e.target, async () => {
  $("#learn-output").innerHTML = `<p class="spinner">Erstelle Karteikarten…</p>`;
  try {
    const { created } = await postJSON("/api/subjects/flashcards", { subject });
    $("#learn-output").innerHTML = `<p>✅ ${created} Karteikarten erstellt. Du findest sie unter „Karteikarten“.</p>`;
    loadStatus();
  } catch (err) { $("#learn-output").innerHTML = ""; throw err; }
}));

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#ask-input");
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  const chat = $("#chat");
  chat.insertAdjacentHTML("beforeend", `<div class="msg me">${esc(question)}<div class="hint small">${esc(subjectName(subject))}</div></div>`);
  const pending = document.createElement("div");
  pending.className = "msg bot spinner";
  pending.textContent = "Suche in deinen Notizen…";
  chat.append(pending);
  try {
    const { answer, sources } = await postJSON("/api/ask", { question, subject });
    pending.className = "msg bot";
    pending.innerHTML = `<div class="md">${md(answer)}</div>` + (sources.length ? `<div class="sources">Quellen: ${
      sources.map((s) => `<a data-id="${s.doc_id}">[${s.n}] ${esc(s.title)}</a>`).join(" · ")}</div>` : "");
  } catch (err) {
    pending.className = "msg bot error-text";
    pending.textContent = err.message;
  }
});

$("#chat").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-id]");
  if (a) openDoc(Number(a.dataset.id));
});

// --- Karteikarten ------------------------------------------------------------

let cardQueue = [];

async function loadCards() {
  cardQueue = await api("/api/flashcards/due" + subjectQuery());
  showCard();
}

function showCard() {
  const area = $("#flashcard-area");
  const card = cardQueue[0];
  if (!card) {
    area.innerHTML = `<p>🎉 Keine Karten fällig. Erstelle neue Karten unter „Lernen“ oder bei einer Notiz.</p>`;
    loadStatus();
    return;
  }
  area.innerHTML = `
    <p class="hint">${cardQueue.length} fällig${card.doc_title ? " · aus „" + esc(card.doc_title) + "“" : ""}</p>
    <div class="flashcard"><div>${esc(card.front)}</div><div class="back" hidden>${esc(card.back)}</div></div>
    <div class="grades" id="reveal-row"><button class="primary" id="reveal">Antwort zeigen</button></div>
    <div class="grades" id="grade-row" hidden>
      <button data-g="0">Nochmal</button><button data-g="1">Schwer</button>
      <button data-g="2" class="primary">Gut</button><button data-g="3">Einfach</button>
    </div>`;
  $("#reveal").addEventListener("click", () => {
    area.querySelector(".back").hidden = false;
    $("#reveal-row").hidden = true;
    $("#grade-row").hidden = false;
  });
  $("#grade-row").addEventListener("click", async (e) => {
    const g = e.target.dataset.g;
    if (g === undefined) return;
    await postJSON(`/api/flashcards/${card.id}/review`, { grade: Number(g) });
    cardQueue.shift();
    if (Number(g) === 0) cardQueue.push(card); // gleich nochmal am Ende
    showCard();
  });
}

// --- Einstellungen -----------------------------------------------------------

let allModels = [];

async function loadSettings() {
  const s = await api("/api/settings");
  $("#key-hint").textContent = s.key_set ? `(gespeichert ${s.key_hint})` : "(noch keiner)";
  $("#set-key").value = "";
  try {
    const { models, suggested, suggested_vision } = await api("/api/models");
    allModels = models;
    renderModels(s.chat_model || suggested || "", s.vision_model || suggested_vision || "");
  } catch (e) {
    $("#set-model").innerHTML = `<option value="${esc(s.chat_model)}">${esc(s.chat_model || "Modell-Liste nicht erreichbar")}</option>`;
    $("#set-vision").innerHTML = `<option value="${esc(s.vision_model)}">${esc(s.vision_model || "–")}</option>`;
  }
}

function renderModels(selected, selectedVision) {
  const onlyFree = $("#only-free").checked;
  const price = (m) => (m.free ? "gratis" : `$${(Number(m.price_in) * 1e6).toFixed(2)} / Mio. Tokens`);
  const options = (list, sel) => list.map((m) =>
    `<option value="${esc(m.id)}" ${m.id === sel ? "selected" : ""}>${esc(m.name)} – ${price(m)}</option>`).join("");
  $("#set-model").innerHTML = options(allModels.filter((m) => !onlyFree || m.free || m.id === selected), selected);
  $("#set-vision").innerHTML = `<option value="">– keins (nur getippten Text lesen) –</option>` +
    options(allModels.filter((m) => m.vision && (!onlyFree || m.free || m.id === selectedVision)), selectedVision);
}

$("#only-free").addEventListener("change", () => renderModels($("#set-model").value, $("#set-vision").value));

$("#set-save").addEventListener("click", (e) => busy(e.target, async () => {
  const body = { chat_model: $("#set-model").value, vision_model: $("#set-vision").value };
  if ($("#set-key").value.trim()) body.openrouter_key = $("#set-key").value.trim();
  await postJSON("/api/settings", body);
  $("#set-result").textContent = "✅ Gespeichert";
  loadSettings();
  loadStatus();
}));

$("#set-test").addEventListener("click", (e) => busy(e.target, async () => {
  $("#set-result").textContent = "Teste…";
  try {
    const { reply } = await postJSON("/api/settings/test");
    $("#set-result").textContent = "✅ KI antwortet: " + reply;
  } catch (err) { $("#set-result").textContent = "⚠️ " + err.message; }
}));

async function reprocess(button, onlyErrors) {
  await busy(button, async () => {
    const { queued } = await postJSON("/api/documents/reprocess", { only_errors: onlyErrors });
    $("#reprocess-result").textContent = `✅ ${queued} Notizen werden im Hintergrund neu verarbeitet.`;
    loadDocs();
  });
}

$("#reprocess-pdfs").addEventListener("click", (e) => {
  if (confirm("Alle PDFs neu einlesen? Das kann eine Weile dauern.")) reprocess(e.target, false);
});
$("#reprocess-errors").addEventListener("click", (e) => reprocess(e.target, true));

// --- Start -------------------------------------------------------------------

document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => { currentDoc = null; show(b.dataset.view); }));
refreshAll();
loadOnenote();
setInterval(loadStatus, 30000);
