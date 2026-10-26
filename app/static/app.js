"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const KIND = { pdf: "📄", lesson: "🎙️", text: "📝" };
const KIND_NAME = { pdf: "PDF", lesson: "Lektion", text: "Notiz" };
const STATUS = { pending: "wartet", processing: "in Arbeit", ready: "bereit", error: "Fehler" };
const NO_SUBJECT = "Ohne Fach";
// Reiterfarben wie farbige Register im Ordner
const TAB_COLORS = ["#2f6fdb", "#1f9a8a", "#9b4fc9", "#e0912b", "#3f9b45", "#d4537e", "#5b6b8c", "#c4512c"];

let docs = [];
let subjects = [];
let subject = loadSubject();   // null = alle Fächer
let currentView = "home";
let currentDoc = null;
let docTab = "summary";
let pollTimer = null;
let status = { flashcards: { due: 0, due_by_subject: {} } };
let chatHistory = [];

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

const send = (path, body, method = "POST") =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Einfacher Markdown-Renderer (Überschriften, Listen, fett). Der Text wird vorher escaped.
function md(text) {
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "", inList = false;
  for (const line of esc(text).split("\n")) {
    const item = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
    if (item) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(item[1])}</li>`;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    const h = line.match(/^#{1,4}\s+(.*)/);
    if (h) html += `<h3>${inline(h[1])}</h3>`;
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}

function toast(text, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), isError ? 7000 : 3500);
}

async function busy(button, label, fn) {
  const old = button.innerHTML;
  button.disabled = true;
  button.textContent = label;
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { button.disabled = false; button.innerHTML = old; }
}

function loadSubject() {
  try { const v = localStorage.getItem("subject"); return v === null ? null : JSON.parse(v); } catch { return null; }
}
function saveSubject() {
  try { localStorage.setItem("subject", JSON.stringify(subject)); } catch { /* egal */ }
}

const subjectName = (s) => (s === null ? "Alle Fächer" : s || NO_SUBJECT);
const subjectQuery = () => (subject === null ? "" : `?subject=${encodeURIComponent(subject)}`);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Feste Farbe pro Fach nach Reihenfolge, damit sich nebeneinanderliegende Reiter immer unterscheiden
function tabColor(name) {
  if (name === null) return "var(--text)";
  const i = subjects.findIndex((s) => s.subject === name);
  return TAB_COLORS[(i < 0 ? 0 : i) % TAB_COLORS.length];
}

function dateText(ts) {
  const d = new Date(ts * 1000), today = new Date();
  const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (days === 0) return "heute";
  if (days === 1) return "gestern";
  return d.toLocaleDateString("de-CH", { day: "numeric", month: "short" });
}

// --- Ansichten ---------------------------------------------------------------

function show(view) {
  currentView = view;
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  $$(".views button").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.view === view || (view === "doc" && b.dataset.view === "notes"))));
  if (view === "home") loadHome();
  if (view === "notes") renderDocs();
  if (view === "cards") loadCards();
  if (view === "settings") loadSettings();
  if (view === "record") fillSubjectSelect($("#rec-subject"), subject ?? "", true);
  if (view === "learn") $("#learn-scope").textContent = subject === null
    ? "Ich suche in allen Fächern. Wähle ein Fach, um gezielter zu fragen."
    : `Ich suche nur in ${subjectName(subject)}.`;
}

$$(".views button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
$$("[data-goto]").forEach((b) => b.addEventListener("click", () => show(b.dataset.goto)));
$("#open-settings").addEventListener("click", () => show("settings"));

// --- Status und Fächer -------------------------------------------------------

async function loadStatus() {
  try {
    status = await api("/api/status");
    const banner = $("#banner");
    let msg = "";
    if (!status.key_set) msg = 'Die KI ist noch nicht eingerichtet. <a href="#" data-go="settings">OpenRouter-Schlüssel eintragen</a>';
    else if (!status.chat_model) msg = 'Wähle ein KI-Modell in den <a href="#" data-go="settings">Einstellungen</a>.';
    banner.hidden = !msg;
    banner.innerHTML = msg;
    renderTabs();
  } catch { /* Server startet gerade neu */ }
}

$("#banner").addEventListener("click", (e) => {
  const a = e.target.closest("[data-go]");
  if (a) { e.preventDefault(); show(a.dataset.go); }
});

async function loadSubjects() {
  subjects = await api("/api/subjects");
  if (subject !== null && !subjects.some((s) => s.subject === subject)) { subject = null; saveSubject(); }
  renderTabs();
  renderTitle();
}

function renderTabs() {
  const total = subjects.reduce((n, s) => n + s.count, 0);
  const items = [{ subject: null, count: total }, ...subjects];
  const dueBy = status.flashcards.due_by_subject || {};
  $("#tabs").innerHTML = items.map((s, i) => {
    const due = s.subject === null ? status.flashcards.due : dueBy[s.subject] || 0;
    return `<li><button class="tab" data-i="${i}" style="--tab:${tabColor(s.subject)}" aria-current="${s.subject === subject}"
        title="${esc(subjectName(s.subject))}: ${plural(s.count, "Notiz", "Notizen")}">
      <span class="name">${esc(subjectName(s.subject))}</span>
      ${due ? `<span class="due" title="${plural(due, "Karte", "Karten")} fällig">${due}</span>` : ""}
    </button></li>`;
  }).join("");
  $("#tabs").onclick = (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    subject = items[tab.dataset.i].subject;
    saveSubject();
    chatHistory = [];
    $("#chat").innerHTML = "";
    $("#chat-clear").hidden = true;
    renderTabs();
    renderTitle();
    show(currentView === "doc" || currentView === "settings" ? "home" : currentView);
  };
}

function renderTitle() {
  $("#subject-title").textContent = subjectName(subject);
  const count = subject === null ? subjects.reduce((n, s) => n + s.count, 0) : subjects.find((s) => s.subject === subject)?.count || 0;
  $("#subject-sub").innerHTML = subject
    ? `${plural(count, "Notiz", "Notizen")} <button id="rename" class="link">Fach umbenennen</button>`
    : plural(count, "Notiz", "Notizen") + " in allen Fächern";
  $("#rename")?.addEventListener("click", renameSubject);
  document.title = subject === null ? "Notiz-App" : `${subjectName(subject)} – Notiz-App`;
}

async function renameSubject() {
  const name = prompt(`Neuer Name für „${subject}“.\nGibst du den Namen eines anderen Fachs ein, werden die beiden zusammengelegt.`, subject);
  if (!name || !name.trim() || name.trim() === subject) return;
  await send("/api/subjects/rename", { old: subject, new: name.trim() });
  subject = name.trim();
  saveSubject();
  toast(`Fach heisst jetzt „${subject}“.`);
  await refreshAll();
}

function fillSubjectSelect(select, selected, allowNew = false) {
  const names = subjects.map((s) => s.subject);
  if (selected && !names.includes(selected)) names.push(selected);
  select.innerHTML = names.map((n) => `<option value="${esc(n)}" ${n === selected ? "selected" : ""}>${esc(n || NO_SUBJECT)}</option>`).join("") +
    (allowNew ? `<option value="__new__">Neues Fach…</option>` : "");
  if (allowNew) select.onchange = () => ($("#rec-subject-new").hidden = select.value !== "__new__");
}

async function refreshAll() {
  await Promise.all([loadSubjects(), loadDocs(), loadStatus()]);
  if (currentView === "home") loadHome();
}

// --- Übersicht ---------------------------------------------------------------

async function loadHome() {
  let s;
  try { s = await api("/api/stats" + subjectQuery()); } catch { return; }
  const where = subject === null ? "" : ` in ${subjectName(subject)}`;
  $("#home-lead").innerHTML = s.cards_due
    ? `<span class="mark">${plural(s.cards_due, "Karteikarte wartet", "Karteikarten warten")}</span> heute auf dich${esc(where)}.`
    : s.cards_total ? `Alle Karteikarten${esc(where)} sind für heute erledigt.` : `Noch keine Karteikarten${esc(where)}.`;
  $("#home-cards").disabled = !s.cards_due;
  $("#home-quiz").disabled = subject === null || !s.notes;
  $("#home-hint").textContent = subject === null
    ? "Für ein Quiz wählst du zuerst ein Fach."
    : !s.cards_total ? "Unter „Karteikarten“ kannst du aus deinen Notizen neue Karten erstellen lassen." : "";
  const facts = [
    ["Lernserie", s.streak ? plural(s.streak, "Tag", "Tage") + " in Folge" : "heute starten"],
    ["Heute geübt", plural(s.reviews_today, "Karte", "Karten")],
    ["Quiz richtig", s.quiz_percent === null ? "noch kein Quiz" : `${s.quiz_percent} %`],
    ["Notizen", s.notes + (s.lessons ? `, davon ${plural(s.lessons, "Lektion", "Lektionen")}` : "")],
  ];
  if (s.processing) facts.push(["In Arbeit", plural(s.processing, "Notiz", "Notizen")]);
  if (s.errors) facts.push(["Mit Fehler", plural(s.errors, "Notiz", "Notizen")]);
  $("#home-facts").innerHTML = facts.map(([k, v]) => `<li><span>${k}</span><strong>${esc(v)}</strong></li>`).join("");
  $("#home-recent").innerHTML = s.recent.length ? s.recent.map((d) => docItem(d, d.title.split(" / ").pop(), true)).join("")
    : `<li class="hint">Noch keine Notizen. Synchronisiere OneNote oder nimm eine Lektion auf.</li>`;
}

$("#home-cards").addEventListener("click", () => show("cards"));
$("#home-quiz").addEventListener("click", () => { show("learn"); $("#subject-quiz").click(); });
$("#home-recent").addEventListener("click", openFromList);

// --- Notizen -----------------------------------------------------------------

async function loadDocs() {
  docs = await api("/api/documents");
  if (currentView === "notes") renderDocs();
  const working = docs.some((d) => d.status === "pending" || d.status === "processing");
  clearTimeout(pollTimer);
  if (working) pollTimer = setTimeout(async () => {
    await loadDocs();
    if (currentView === "doc" && currentDoc) openDoc(currentDoc.id, true);
    if (currentView === "home") loadHome();
  }, 3000);
}

function docItem(d, label, showSubject) {
  return `<li data-id="${d.id}" tabindex="0">
    <span class="label">${KIND[d.kind] || ""} ${esc(label)}
      ${showSubject && d.subject && subject === null ? `<span class="tag">${esc(d.subject)}</span>` : ""}
      ${d.progress ? `<span class="progress">${esc(d.progress)}</span>`
        : d.images_missing ? `<span class="progress">${plural(d.images_missing, "Bildseite wird", "Bildseiten werden")} später noch gelesen</span>` : ""}</span>
    <span class="row" style="gap:6px;flex-wrap:nowrap">
      ${d.created_at && showSubject ? `<span class="hint" style="margin:0">${dateText(d.created_at)}</span>` : ""}
      ${d.has_file && d.kind === "pdf" ? `<a class="file-link" href="/api/documents/${d.id}/file" target="_blank" title="PDF öffnen">PDF</a>` : ""}
      ${d.status !== "ready" ? `<span class="pill ${d.status}">${STATUS[d.status] || d.status}</span>` : ""}
    </span>
  </li>`;
}

// OneNote-Abschnitte nach Notizbuch/Abschnittsgruppe, dann Lektionen und eigene Notizen.
function groupDocs(list) {
  const groups = new Map();
  const add = (key, doc, label) => {
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({ doc, label });
  };
  for (const d of list) {
    if (d.external_id && d.title.includes(" / ")) {
      const parts = d.title.split(" / ");
      add(parts.slice(0, -1).join(" › "), d, parts[parts.length - 1]);
    } else if (d.kind === "lesson") add("Aufgenommene Lektionen", d, d.title);
    else add("Eigene Notizen und PDFs", d, d.title);
  }
  const order = (k) => (k === "Aufgenommene Lektionen" ? 0 : k === "Eigene Notizen und PDFs" ? 2 : 1);
  return [...groups.entries()].sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b, "de"));
}

function renderDocs() {
  const shown = docs.filter((d) => subject === null || d.subject === subject);
  $("#notes-count").textContent = plural(shown.length, "Notiz", "Notizen");
  const tree = $("#doc-tree");
  if (!shown.length) {
    tree.innerHTML = `<p class="hint">Hier ist noch nichts. Synchronisiere OneNote oder nimm eine Lektion auf.</p>`;
    return;
  }
  const groups = groupDocs(shown);
  tree.innerHTML = groups.map(([group, items]) => `
    <details class="group" ${groups.length <= 4 ? "open" : ""}>
      <summary><span class="title">${esc(group)}</span><span class="hint" style="margin:0">${items.length}</span></summary>
      <ul class="doc-list">
        ${items.sort((a, b) => a.label.localeCompare(b.label, "de", { numeric: true })).map(({ doc, label }) => docItem(doc, label, true)).join("")}
      </ul>
    </details>`).join("");
}

$("#expand-all").addEventListener("click", () => $$("#doc-tree details").forEach((d) => (d.open = true)));
$("#collapse-all").addEventListener("click", () => $$("#doc-tree details").forEach((d) => (d.open = false)));

function openFromList(e) {
  if (e.target.closest("a")) return;
  const li = e.target.closest("li[data-id]");
  if (li) openDoc(Number(li.dataset.id));
}
$("#doc-tree").addEventListener("click", openFromList);
for (const list of ["#doc-tree", "#home-recent"]) {
  $(list).addEventListener("keydown", (e) => { if (e.key === "Enter") openFromList(e); });
}

// --- Eine Notiz --------------------------------------------------------------

async function openDoc(id, keepOutput = false) {
  const doc = await api(`/api/documents/${id}`);
  const switched = currentDoc?.id !== doc.id;
  currentDoc = doc;
  if (currentView !== "doc") show("doc");
  $("#doc-title").textContent = doc.title.split(" / ").pop();
  const meta = [KIND_NAME[doc.kind], doc.title.includes(" / ") ? doc.title.split(" / ").slice(0, -1).join(" › ") : "",
    `hinzugefügt ${dateText(doc.created_at)}`, doc.status !== "ready" ? STATUS[doc.status] : "", doc.progress,
    doc.images_missing ? `${plural(doc.images_missing, "Bildseite", "Bildseiten")} mit Handschrift noch nicht gelesen (Tageslimit), wird automatisch nachgeholt` : ""];
  $("#doc-meta").innerHTML = esc(meta.filter(Boolean).join(", ")) + (doc.error ? `<br><span class="error-text">${esc(doc.error)}</span>` : "");
  fillSubjectSelect($("#doc-subject"), doc.subject);
  const ready = doc.status === "ready";
  ["#doc-summary", "#doc-quiz", "#doc-cards"].forEach((s) => ($(s).disabled = !ready));
  $("#doc-summary").textContent = doc.summary ? "Neu zusammenfassen" : "Zusammenfassen";
  $("#doc-retry").hidden = doc.status !== "error";
  if (!keepOutput) $("#doc-output").innerHTML = "";
  if (switched) docTab = doc.summary ? "summary" : doc.source_path ? "original" : "text";
  renderDocPane();
}

function renderDocPane() {
  const doc = currentDoc;
  $$(".doc-tabs button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.tab === docTab));
    b.hidden = b.dataset.tab === "original" && !doc.source_path;
  });
  const pane = $("#doc-pane");
  if (docTab === "summary") {
    pane.innerHTML = doc.summary ? `<div class="md">${md(doc.summary)}</div>`
      : `<p class="hint">Noch keine Zusammenfassung. Klick auf „Zusammenfassen“.</p>`;
  } else if (docTab === "text") {
    const text = doc.clean_text || doc.raw_text;
    pane.innerHTML = text ? `<pre class="text"></pre>` : `<p class="hint">Kein Text gefunden.</p>`;
    if (text) pane.firstChild.textContent = text;
    if (doc.kind === "lesson" && doc.raw_text && doc.clean_text) {
      pane.insertAdjacentHTML("beforeend", `<details style="margin-top:14px"><summary>Rohes Transkript mit Zeitangaben</summary><pre class="text"></pre></details>`);
      pane.querySelector("details pre").textContent = doc.raw_text;
    }
  } else {
    const url = `/api/documents/${doc.id}/file`;
    pane.innerHTML = doc.kind === "lesson"
      ? `<audio controls src="${url}" style="width:100%"></audio>`
      : `<iframe class="viewer" src="${url}" title="Original"></iframe><p class="hint"><a href="${url}" target="_blank">In neuem Fenster öffnen</a></p>`;
  }
}

$$(".doc-tabs button").forEach((b) => b.addEventListener("click", () => { docTab = b.dataset.tab; renderDocPane(); }));

$("#doc-subject").addEventListener("change", async (e) => {
  await send(`/api/documents/${currentDoc.id}`, { subject: e.target.value }, "PATCH");
  toast(`Verschoben nach „${e.target.value || NO_SUBJECT}“.`);
  currentDoc.subject = e.target.value;
  refreshAll();
});

$("#back").addEventListener("click", () => { currentDoc = null; show("notes"); });

$("#doc-delete").addEventListener("click", async () => {
  if (!confirm(`„${currentDoc.title}“ löschen? Das lässt sich nicht rückgängig machen.`)) return;
  await api(`/api/documents/${currentDoc.id}`, { method: "DELETE" });
  toast("Notiz gelöscht.");
  currentDoc = null;
  await refreshAll();
  show("notes");
});

$("#doc-retry").addEventListener("click", async () => {
  await send(`/api/documents/${currentDoc.id}/retry`);
  toast("Wird nochmals verarbeitet.");
  await loadDocs();
  openDoc(currentDoc.id);
});

$("#doc-summary").addEventListener("click", (e) => busy(e.target, "Fasse zusammen…", async () => {
  const { summary } = await send(`/api/documents/${currentDoc.id}/summary`);
  currentDoc.summary = summary;
  docTab = "summary";
  renderDocPane();
  $("#doc-summary").textContent = "Neu zusammenfassen";
}));

$("#doc-cards").addEventListener("click", (e) => busy(e.target, "Erstelle Karten…", async () => {
  const { created } = await send(`/api/documents/${currentDoc.id}/flashcards`);
  toast(`${plural(created, "Karteikarte", "Karteikarten")} erstellt.`);
  loadStatus();
}));

$("#doc-quiz").addEventListener("click", (e) => busy(e.target, "Erstelle Quiz…", async () => {
  const { fragen } = await send(`/api/documents/${currentDoc.id}/quiz`);
  renderQuiz($("#doc-output"), fragen, currentDoc.subject);
}));

// --- Quiz --------------------------------------------------------------------

function renderQuiz(box, questions, quizSubject) {
  let score = 0, answered = 0;
  box.innerHTML = `<div class="row between" style="margin-top:14px"><h3>Quiz</h3><span class="score" aria-live="polite"></span></div>` +
    questions.map((q, i) => `
    <div class="quiz-q" data-i="${i}">
      <div class="q">${i + 1}. ${esc(q.frage)}</div>
      <div class="opts">${q.optionen.map((o, j) => `<button data-j="${j}">${esc(o)}</button>`).join("")}</div>
      <p class="hint expl" hidden></p>
    </div>`).join("");
  box.querySelectorAll(".quiz-q").forEach((div) => {
    const q = questions[div.dataset.i];
    div.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn || div.dataset.done) return;
      div.dataset.done = "1";
      answered++;
      const right = Number(btn.dataset.j) === q.richtig;
      if (right) score++;
      else btn.classList.add("wrong");
      div.querySelectorAll(".opts button")[q.richtig].classList.add("right");
      const expl = div.querySelector(".expl");
      expl.textContent = (right ? "Richtig. " : "Leider falsch. ") + (q.erklaerung || "");
      expl.hidden = false;
      const done = answered === questions.length;
      box.querySelector(".score").textContent = `${score} von ${answered} richtig${done && score === questions.length ? " 🎉" : ""}`;
      if (done) send("/api/quiz/result", { subject: quizSubject, score, total: questions.length }).catch(() => {});
    });
  });
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#subject-quiz").addEventListener("click", (e) => {
  if (subject === null) { toast("Wähle zuerst ein Fach für das Quiz.", true); return; }
  busy(e.target, "Erstelle Quiz…", async () => {
    const { fragen } = await send(`/api/subjects/quiz?n=${$("#quiz-n").value}`, { subject });
    renderQuiz($("#quiz-box"), fragen, subject);
  });
});

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
  pending.className = "msg bot thinking";
  pending.textContent = "Ich schaue in deinen Notizen nach…";
  chat.append(pending);
  pending.scrollIntoView({ behavior: "smooth", block: "end" });
  try {
    const { answer, sources } = await send("/api/ask", { question, subject, history: chatHistory.slice(-3) });
    chatHistory.push({ question, answer });
    $("#chat-clear").hidden = false;
    pending.className = "msg bot";
    const seen = new Set();
    const unique = sources.filter((s) => !seen.has(s.doc_id) && seen.add(s.doc_id));
    pending.innerHTML = `<div class="md">${md(answer)}</div>` + (unique.length ? `<div class="sources">Aus: ${
      unique.map((s) => `<button data-id="${s.doc_id}">${esc(s.title.split(" / ").pop())}</button>`).join("")}</div>` : "");
  } catch (err) {
    pending.className = "msg bot error-text";
    pending.textContent = err.message;
  }
});

$("#chat").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-id]");
  if (b) openDoc(Number(b.dataset.id));
});

$("#chat-clear").addEventListener("click", () => {
  chatHistory = [];
  $("#chat").innerHTML = "";
  $("#chat-clear").hidden = true;
});

// --- Suche -------------------------------------------------------------------

let searchTimer = null;
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = $("#search").value.trim();
  if (q.length < 2) { $("#search-results").hidden = true; return; }
  searchTimer = setTimeout(async () => {
    try {
      const results = await send("/api/search", { query: q, subject });
      const box = $("#search-results");
      box.innerHTML = results.length ? results.map((r) => `<button data-id="${r.doc_id}">
          <strong>${KIND[r.kind] || ""} ${esc(r.title.split(" / ").pop())}</strong>${r.subject && subject === null ? ` <span class="tag">${esc(r.subject)}</span>` : ""}
          ${r.snippet ? `<span class="snippet">${esc(r.snippet)}…</span>` : ""}
        </button>`).join("") : `<p class="hint" style="padding:8px">Nichts gefunden.</p>`;
      box.hidden = false;
    } catch (e) { toast(e.message, true); }
  }, 300);
});

$("#search-results").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-id]");
  if (!b) return;
  $("#search-results").hidden = true;
  $("#search").value = "";
  openDoc(Number(b.dataset.id));
});

document.addEventListener("click", (e) => { if (!e.target.closest(".search")) $("#search-results").hidden = true; });
$("#search").addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#search-results").hidden = true; $("#search").blur(); } });

// --- Karteikarten ------------------------------------------------------------

let cardQueue = [];

async function loadCards() {
  cardQueue = await api("/api/flashcards/due" + subjectQuery());
  $("#cards-export").href = "/api/flashcards/export.csv" + subjectQuery();
  showCard();
  loadCardList();
}

function showCard() {
  const area = $("#flashcard-area");
  const card = cardQueue[0];
  if (!card) {
    area.innerHTML = `<h2>Karteikarten</h2><p class="hint">Für heute ist alles gelernt. Neue Karten kannst du unten aus deinen Notizen erstellen lassen.</p>`;
    loadStatus();
    return;
  }
  area.innerHTML = `
    <div class="row between"><h2>Karteikarten</h2><span class="hint">noch ${cardQueue.length}</span></div>
    <div class="index-card">
      <div class="meta">${esc((card.doc_title || "").split(" / ").pop())}</div>
      <div class="front">${esc(card.front)}</div>
      <div class="back" hidden>${esc(card.back)}</div>
    </div>
    <div class="grades" id="reveal-row"><button class="primary big" id="reveal">Antwort zeigen <span class="key">Leertaste</span></button></div>
    <div class="grades" id="grade-row" hidden>
      <button data-g="0">Nochmal <span class="key">1</span></button>
      <button data-g="1">Schwer <span class="key">2</span></button>
      <button data-g="2" class="primary">Gewusst <span class="key">3</span></button>
      <button data-g="3">Einfach <span class="key">4</span></button>
    </div>`;
  $("#reveal").addEventListener("click", reveal);
  $("#grade-row").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-g]");
    if (b) grade(Number(b.dataset.g));
  });
}

function reveal() {
  if (!$("#reveal-row") || $("#reveal-row").hidden) return;
  $(".index-card .back").hidden = false;
  $("#reveal-row").hidden = true;
  $("#grade-row").hidden = false;
  $("#grade-row .primary").focus();
}

async function grade(g) {
  const card = cardQueue[0];
  if (!card || $("#grade-row").hidden) return;
  $("#grade-row").hidden = true;
  await send(`/api/flashcards/${card.id}/review`, { grade: g });
  cardQueue.shift();
  if (g === 0) cardQueue.push(card); // gleich nochmal am Ende
  showCard();
}

document.addEventListener("keydown", (e) => {
  if (currentView !== "cards" || ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (e.key === " " && $("#reveal-row") && !$("#reveal-row").hidden) { e.preventDefault(); reveal(); }
  else if (["1", "2", "3", "4"].includes(e.key)) grade(Number(e.key) - 1);
});

async function loadCardList() {
  const cards = await api("/api/flashcards" + subjectQuery());
  $("#cards-count").textContent = plural(cards.length, "Karte", "Karten") + (subject === null ? "" : ` in ${subjectName(subject)}`);
  $("#card-list").innerHTML = cards.length ? cards.map((c) => `<tr data-id="${c.id}">
      <td><strong>${esc(c.front)}</strong></td><td>${esc(c.back)}</td>
      <td><button class="link" data-edit>Bearbeiten</button> <button class="link danger" data-del>Löschen</button></td>
    </tr>`).join("") : `<tr><td class="hint">Noch keine Karten.</td></tr>`;
  $("#card-list").onclick = async (e) => {
    const row = e.target.closest("tr[data-id]");
    if (!row) return;
    const card = cards.find((c) => c.id === Number(row.dataset.id));
    if (e.target.closest("[data-del]")) {
      if (!confirm("Diese Karte löschen?")) return;
      await api(`/api/flashcards/${card.id}`, { method: "DELETE" });
      toast("Karte gelöscht.");
    } else if (e.target.closest("[data-edit]")) {
      const front = prompt("Vorderseite (Frage):", card.front);
      if (front === null) return;
      const back = prompt("Rückseite (Antwort):", card.back);
      if (back === null || !front.trim() || !back.trim()) return;
      await send(`/api/flashcards/${card.id}`, { front, back }, "PATCH");
      toast("Karte gespeichert.");
    } else return;
    loadCards();
    loadStatus();
  };
}

$("#subject-cards").addEventListener("click", (e) => {
  if (subject === null) { toast("Wähle zuerst ein Fach.", true); return; }
  busy(e.target, "Erstelle Karten…", async () => {
    const { created } = await send("/api/subjects/flashcards", { subject });
    toast(`${plural(created, "neue Karteikarte", "neue Karteikarten")} erstellt.`);
    loadCards();
    loadStatus();
  });
});

// --- Hinzufügen --------------------------------------------------------------

let onenoteTimer = null;

function renderOnenote(s) {
  $("#onenote-box").hidden = !s.available;
  $("#onenote-sync").disabled = s.running;
  let text = "";
  if (s.running) text = s.message || "Läuft…";
  else if (s.error) text = "Fehler: " + s.error;
  else if (s.result) {
    const r = s.result;
    text = `Fertig: ${r.new} neu, ${r.updated} aktualisiert, ${r.unchanged} unverändert` + (r.empty ? `, ${r.empty} leer` : "");
    if (r.incomplete?.length) text += `, ${r.incomplete.length} noch nicht fertig von OneNote geladen (wird in 5 Minuten nochmals versucht)`;
    if (r.failed.length) text += `, ${r.failed.length} fehlgeschlagen`;
  }
  $("#onenote-status").textContent = text;
  clearTimeout(onenoteTimer);
  if (s.running) onenoteTimer = setTimeout(loadOnenote, 2000);
}

async function loadOnenote() {
  try {
    const before = $("#onenote-sync").disabled;
    const s = await api("/api/onenote");
    renderOnenote(s);
    if (before && !s.running) refreshAll();
  } catch { /* egal */ }
}

$("#onenote-sync").addEventListener("click", async () => {
  try { renderOnenote(await send("/api/onenote/sync")); } catch (e) { toast(e.message, true); }
});

$("#pdf-upload").addEventListener("click", (e) => busy(e.target, "Lade hoch…", async () => {
  const files = $("#pdf-files").files;
  if (!files.length) throw new Error("Wähle zuerst eine oder mehrere PDF-Dateien.");
  const form = new FormData();
  for (const f of files) form.append("files", f);
  form.append("subject", subject ?? "");
  await api("/api/upload/pdf", { method: "POST", body: form });
  $("#pdf-files").value = "";
  toast(`${plural(files.length, "PDF", "PDFs")} hochgeladen. Sie werden jetzt eingelesen.`);
  refreshAll();
}));

$("#note-save").addEventListener("click", (e) => busy(e.target, "Speichere…", async () => {
  if (!$("#note-text").value.trim()) throw new Error("Füge zuerst einen Text ein.");
  await send("/api/notes", { title: $("#note-title").value.trim() || "Notiz", text: $("#note-text").value, subject: subject ?? "" });
  $("#note-title").value = $("#note-text").value = "";
  toast("Notiz gespeichert.");
  refreshAll();
}));

// --- Aufnahme ----------------------------------------------------------------

let recorder = null, recChunks = [], recStart = 0, recTimer = null, wakeLock = null;

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

$("#audio-upload").addEventListener("click", (e) => busy(e.target, "Lade hoch…", async () => {
  const file = $("#audio-file").files[0];
  if (!file) throw new Error("Wähle zuerst eine Audiodatei.");
  await uploadAudio(file, file.name);
  $("#audio-file").value = "";
  toast("Hochgeladen. Die Lektion wird jetzt verarbeitet.");
}));

$("#rec-btn").addEventListener("click", () => (recorder ? stopRecording() : startRecording()));

async function startRecording() {
  if ($("#rec-subject").value === "__new__" && !recordSubject()) { toast("Gib dem neuen Fach einen Namen.", true); return; }
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
      $("#rec-time").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }, 1000);
    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* nicht unterstützt */ }
    $("#rec-btn").classList.add("on");
    $("#rec-btn").setAttribute("aria-label", "Aufnahme beenden und speichern");
    $("#rec-state").textContent = "Aufnahme läuft. Tippe nochmals, um zu beenden und zu speichern.";
  } catch (e) {
    toast("Das Mikrofon liess sich nicht starten: " + e.message, true);
  }
}

function stopRecording() {
  recorder.onstop = async () => {
    recorder.stream.getTracks().forEach((t) => t.stop());
    clearInterval(recTimer);
    wakeLock?.release().catch(() => {});
    $("#rec-btn").classList.remove("on");
    $("#rec-btn").setAttribute("aria-label", "Aufnahme starten");
    $("#rec-state").textContent = "Speichere…";
    const type = recorder.mimeType || "audio/webm";
    const ext = type.includes("ogg") ? "ogg" : "webm";
    const blob = new Blob(recChunks, { type });
    try {
      await uploadAudio(blob, `aufnahme-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${ext}`);
      $("#rec-state").textContent = "Gespeichert. Die App macht daraus jetzt Text, Zusammenfassung und Karteikarten.";
      toast("Lektion gespeichert.");
    } catch (e) {
      // Aufnahme nicht verlieren: als Datei anbieten
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `aufnahme.${ext}`;
      a.click();
      $("#rec-state").textContent = "Hochladen hat nicht geklappt. Die Aufnahme wurde als Datei heruntergeladen.";
      toast(e.message, true);
    }
    $("#rec-time").textContent = "0:00";
    recorder = null;
  };
  recorder.stop();
}

window.addEventListener("beforeunload", (e) => { if (recorder) { e.preventDefault(); e.returnValue = ""; } });

// --- Einstellungen -----------------------------------------------------------

let allModels = [];

async function loadSettings() {
  const s = await api("/api/settings");
  $("#key-hint").textContent = s.key_set ? `(gespeichert, endet auf ${s.key_hint.replace("…", "")})` : "(noch keiner)";
  $("#set-key").value = "";
  try {
    const { models, suggested, suggested_vision } = await api("/api/models");
    allModels = models;
    renderModels(s.chat_model || suggested || "", s.vision_model || suggested_vision || "");
  } catch {
    $("#set-model").innerHTML = `<option value="${esc(s.chat_model)}">${esc(s.chat_model || "Modell-Liste nicht erreichbar")}</option>`;
    $("#set-vision").innerHTML = `<option value="${esc(s.vision_model)}">${esc(s.vision_model || "keins")}</option>`;
  }
}

function renderModels(selected, selectedVision) {
  const onlyFree = $("#only-free").checked;
  const price = (m) => (m.free ? "gratis" : `${(Number(m.price_in) * 1e6).toFixed(2)} $ pro Mio. Tokens`);
  const options = (list, sel) => list.map((m) =>
    `<option value="${esc(m.id)}" ${m.id === sel ? "selected" : ""}>${esc(m.name)} (${price(m)})</option>`).join("");
  $("#set-model").innerHTML = options(allModels.filter((m) => !onlyFree || m.free || m.id === selected), selected);
  $("#set-vision").innerHTML = `<option value="">Keins, nur getippten Text lesen</option>` +
    options(allModels.filter((m) => m.vision && (!onlyFree || m.free || m.id === selectedVision)), selectedVision);
}

$("#only-free").addEventListener("change", () => renderModels($("#set-model").value, $("#set-vision").value));

$("#set-save").addEventListener("click", (e) => busy(e.target, "Speichere…", async () => {
  const body = { chat_model: $("#set-model").value, vision_model: $("#set-vision").value };
  if ($("#set-key").value.trim()) body.openrouter_key = $("#set-key").value.trim();
  await send("/api/settings", body);
  toast("Einstellungen gespeichert.");
  loadSettings();
  loadStatus();
}));

$("#set-test").addEventListener("click", (e) => busy(e.target, "Teste…", async () => {
  $("#set-result").textContent = "";
  try {
    const { reply } = await send("/api/settings/test");
    $("#set-result").textContent = "Verbunden. Die KI sagt: " + reply;
  } catch (err) { $("#set-result").textContent = err.message; }
}));

async function reprocess(button, onlyErrors) {
  await busy(button, "Starte…", async () => {
    const { queued } = await send("/api/documents/reprocess", { only_errors: onlyErrors });
    toast(queued ? `${plural(queued, "Notiz wird", "Notizen werden")} neu verarbeitet.` : "Nichts zu tun.");
    loadDocs();
  });
}

$("#reprocess-pdfs").addEventListener("click", (e) => {
  if (confirm("Alle PDFs neu einlesen? Das dauert eine Weile und braucht KI-Anfragen.")) reprocess(e.target, false);
});
$("#reprocess-errors").addEventListener("click", (e) => reprocess(e.target, true));

// --- Start -------------------------------------------------------------------

refreshAll().then(() => show("home"));
loadOnenote();
setInterval(loadStatus, 30000);
