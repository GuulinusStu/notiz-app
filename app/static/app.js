"use strict";

const $ = (sel) => document.querySelector(sel);
const KIND = { pdf: "📄 PDF", lesson: "🎙️ Lektion", text: "📝 Text" };
const STATUS = { pending: "wartet", processing: "in Arbeit", ready: "bereit", error: "Fehler" };

let docs = [];
let currentDoc = null;
let pollTimer = null;

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

const postJSON = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Sehr einfacher Markdown-Renderer (Überschriften, Listen, fett) – Text wird vorher escaped.
function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (const line of lines) {
    const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
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

function show(view) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  document.querySelectorAll("nav button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view || (view === "doc" && b.dataset.view === "notes")));
  if (view === "cards") loadCards();
}

// --- Status ------------------------------------------------------------------

async function loadStatus() {
  try {
    const s = await api("/api/status");
    const bar = $("#status-bar");
    const problems = [];
    if (!s.ollama) problems.push("Ollama läuft nicht. Starte die Ollama-App, sonst funktionieren Fragen, Quiz und Zusammenfassungen nicht.");
    else {
      if (!s.chat_model_ok) problems.push(`KI-Modell fehlt: <code>ollama pull ${esc(s.chat_model)}</code>`);
      if (!s.embed_model_ok) problems.push(`Suchmodell fehlt: <code>ollama pull ${esc(s.embed_model)}</code>`);
    }
    bar.hidden = problems.length === 0;
    bar.innerHTML = problems.join("<br>");
    const badge = $("#due-badge");
    badge.hidden = !s.flashcards.due;
    badge.textContent = s.flashcards.due;
  } catch { /* Server neu gestartet */ }
}

// --- Notizen -----------------------------------------------------------------

async function loadDocs() {
  docs = await api("/api/documents");
  renderDocs();
  const working = docs.some((d) => d.status === "pending" || d.status === "processing");
  clearTimeout(pollTimer);
  if (working) pollTimer = setTimeout(refresh, 3000);
}

async function refresh() {
  await loadDocs();
  if (currentDoc && !$("#view-doc").hidden) openDoc(currentDoc.id, { keepOutput: true });
}

function renderDocs() {
  const q = $("#filter").value.toLowerCase();
  const list = $("#doc-list");
  const shown = docs.filter((d) => (d.title + " " + d.subject).toLowerCase().includes(q));
  if (!shown.length) {
    list.innerHTML = `<li class="hint">${docs.length ? "Nichts gefunden." : "Noch keine Notizen. Lade oben PDFs hoch oder nimm eine Lektion auf."}</li>`;
    return;
  }
  list.innerHTML = shown.map((d) => `
    <li data-id="${d.id}">
      <div>
        <div class="title">${esc(d.title)}</div>
        <div class="hint">${KIND[d.kind] || d.kind}${d.subject ? " · " + esc(d.subject) : ""} · ${new Date(d.created_at * 1000).toLocaleDateString("de-CH")}
          ${d.progress ? " · " + esc(d.progress) : ""}</div>
      </div>
      <span class="pill ${d.status}">${STATUS[d.status] || d.status}</span>
    </li>`).join("");
}

$("#doc-list").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) openDoc(Number(li.dataset.id));
});
$("#filter").addEventListener("input", renderDocs);

async function openDoc(id, { keepOutput = false } = {}) {
  const doc = await api(`/api/documents/${id}`);
  currentDoc = doc;
  show("doc");
  $("#doc-title").textContent = doc.title;
  const meta = [KIND[doc.kind], doc.subject, STATUS[doc.status]];
  if (doc.progress) meta.push(doc.progress);
  $("#doc-meta").innerHTML = esc(meta.filter(Boolean).join(" · ")) +
    (doc.error ? `<br><span class="error-text">${esc(doc.error)}</span>` : "");
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

$("#back").addEventListener("click", () => { currentDoc = null; show("notes"); loadDocs(); });

$("#doc-delete").addEventListener("click", async () => {
  if (!confirm(`„${currentDoc.title}“ wirklich löschen? Das kann nicht rückgängig gemacht werden.`)) return;
  await api(`/api/documents/${currentDoc.id}`, { method: "DELETE" });
  currentDoc = null;
  show("notes");
  loadDocs();
});

$("#doc-retry").addEventListener("click", async () => {
  await postJSON(`/api/documents/${currentDoc.id}/retry`);
  refresh();
});

$("#doc-summary").addEventListener("click", (e) => busy(e.target, async () => {
  $("#doc-output").innerHTML = `<p class="spinner">Die KI fasst zusammen. Das kann ein paar Minuten dauern…</p>`;
  const { summary } = await postJSON(`/api/documents/${currentDoc.id}/summary`);
  $("#doc-output").innerHTML = "";
  $("#doc-summary-box").innerHTML = `<div class="md">${md(summary)}</div>`;
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
  renderQuiz(fragen);
}));

function renderQuiz(questions) {
  let score = 0, answered = 0;
  const box = $("#doc-output");
  box.innerHTML = `<h3>Quiz</h3><p id="quiz-score" class="hint"></p>` + questions.map((q, i) => `
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
      const pick = Number(btn.dataset.j);
      if (pick === q.richtig) score++;
      else btn.classList.add("wrong");
      div.querySelectorAll("button")[q.richtig].classList.add("right");
      div.querySelector(".expl").hidden = !q.erklaerung;
      $("#quiz-score").textContent = `${score} von ${answered} richtig` +
        (answered === questions.length ? ` – fertig! ${score === questions.length ? "🎉" : ""}` : "");
    });
  });
}

// --- OneNote -----------------------------------------------------------------

let onenoteTimer = null;

function renderOnenote(s) {
  $("#onenote-box").hidden = !s.available;
  $("#pdf-manual").open = !s.available;
  $("#onenote-sync").disabled = s.running;
  let text = "";
  if (s.running) text = s.message || "Läuft…";
  else if (s.error) text = "⚠️ " + s.error;
  else if (s.result) {
    const r = s.result;
    text = `✅ ${r.sections} Abschnitte: ${r.new} neu, ${r.updated} aktualisiert, ${r.unchanged} unverändert`;
    if (r.failed.length) text += `, ${r.failed.length} fehlgeschlagen (${r.failed.map((f) => f.path).join(", ")})`;
  }
  $("#onenote-status").textContent = text;
  clearTimeout(onenoteTimer);
  if (s.running) onenoteTimer = setTimeout(loadOnenote, 2000);
  else if (s.result) loadDocs();
}

async function loadOnenote() {
  try { renderOnenote(await api("/api/onenote")); } catch { /* ignorieren */ }
}

$("#onenote-sync").addEventListener("click", async () => {
  try { renderOnenote(await postJSON("/api/onenote/sync")); } catch (e) { alert(e.message); }
});

// --- Upload ------------------------------------------------------------------

$("#pdf-upload").addEventListener("click", (e) => busy(e.target, async () => {
  const files = $("#pdf-files").files;
  if (!files.length) throw new Error("Bitte zuerst PDF-Dateien auswählen.");
  const form = new FormData();
  for (const f of files) form.append("files", f);
  form.append("subject", $("#pdf-subject").value);
  await api("/api/upload/pdf", { method: "POST", body: form });
  $("#pdf-files").value = "";
  loadDocs();
}));

$("#note-save").addEventListener("click", (e) => busy(e.target, async () => {
  await postJSON("/api/notes", { title: $("#note-title").value || "Notiz", text: $("#note-text").value, subject: "" });
  $("#note-title").value = $("#note-text").value = "";
  loadDocs();
}));

async function uploadAudio(blob, filename) {
  const form = new FormData();
  form.append("file", blob, filename);
  form.append("title", $("#rec-title").value);
  form.append("subject", $("#rec-subject").value);
  await api("/api/upload/audio", { method: "POST", body: form });
  $("#rec-title").value = "";
  loadDocs();
}

$("#audio-upload").addEventListener("click", (e) => busy(e.target, async () => {
  const file = $("#audio-file").files[0];
  if (!file) throw new Error("Bitte zuerst eine Audiodatei auswählen.");
  await uploadAudio(file, file.name);
  $("#audio-file").value = "";
}));

// --- Aufnahme ----------------------------------------------------------------

let recorder = null, recChunks = [], recStart = 0, recTimer = null, wakeLock = null;

$("#rec-start").addEventListener("click", async () => {
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

// --- Fragen ------------------------------------------------------------------

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#ask-input");
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  const chat = $("#chat");
  chat.insertAdjacentHTML("beforeend", `<div class="msg me">${esc(question)}</div>`);
  const pending = document.createElement("div");
  pending.className = "msg bot spinner";
  pending.textContent = "Suche in deinen Notizen…";
  chat.append(pending);
  try {
    const { answer, sources } = await postJSON("/api/ask", { question });
    pending.className = "msg bot";
    const seen = new Map();
    sources.forEach((s) => seen.has(s.doc_id) || seen.set(s.doc_id, s));
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
  cardQueue = await api("/api/flashcards/due");
  showCard();
}

function showCard() {
  const area = $("#flashcard-area");
  const card = cardQueue[0];
  if (!card) {
    area.innerHTML = `<p>🎉 Keine Karten fällig. Erstelle neue Karten bei einer Notiz mit „Karteikarten erstellen“.</p>`;
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

// --- Start -------------------------------------------------------------------

document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => { currentDoc = null; show(b.dataset.view); }));
loadDocs();
loadStatus();
loadOnenote();
setInterval(loadStatus, 30000);
