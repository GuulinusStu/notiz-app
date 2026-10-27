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
  if (view === "settings") { loadSettings(); loadBackup(); }
  if (view === "calendar") loadCalendar();
  if (view === "subjects") renderSubjectAdmin();
  if (view !== "notes") setSelecting(false);
  if (view === "record") { fillSubjectSelect($("#rec-subject"), subject ?? "", true); checkUnsavedRecording(); loadTimetable(); }
  if (view === "learn") loadMistakes();
  if (view === "learn") $("#learn-scope").textContent = subject === null
    ? "Ich suche in allen Fächern. Wähle ein Fach, um gezielter zu fragen."
    : `Ich suche nur in ${subjectName(subject)}.`;
}

$$(".views button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
$$("[data-goto]").forEach((b) => b.addEventListener("click", () => show(b.dataset.goto)));
$("#open-settings").addEventListener("click", () => show("settings"));
$("#open-subjects").addEventListener("click", () => show("subjects"));

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
  loadQuota();
}

async function loadQuota() {
  try {
    const q = await api("/api/quota");
    const el = $("#quota");
    el.hidden = false;
    el.classList.toggle("low", q.remaining < 10);
    el.textContent = q.remaining > 0
      ? `KI heute: noch ${q.remaining} von ${q.limit} Anfragen`
      : "KI-Limit für heute erreicht (ab 01:00 wieder frei)";
    el.title = "Gratis-Modelle erlauben eine bestimmte Zahl Anfragen pro Tag. Karteikarten lernen, Suche und Lern-Timer brauchen keine.";
  } catch { /* egal */ }
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
    show(["doc", "settings", "subjects"].includes(currentView) ? "home" : currentView);
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
  loadHomeExam();
  loadToday();
  $("#home-recent").innerHTML = s.recent.length ? s.recent.map((d) => docItem(d, d.title.split(" / ").pop(), "recent")).join("")
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
  const pick = selecting && showSubject !== "recent"
    ? `<input type="checkbox" class="pick" data-pick="${d.id}" ${selected.has(d.id) ? "checked" : ""} aria-label="${esc(label)} auswählen">` : "";
  return `<li data-id="${d.id}" tabindex="0">
    <span class="label">${pick}${KIND[d.kind] || ""} ${esc(label)}
      ${showSubject && d.subject && subject === null ? `<span class="tag">${esc(d.subject)}</span>` : ""}
      ${d.progress ? `<span class="progress">${esc(d.progress)}</span>`
        : d.images_missing ? `<span class="progress">${plural(d.images_missing, "Bildseite wird", "Bildseiten werden")} später noch gelesen</span>`
        : d.blank_pages ? `<span class="progress">${plural(d.blank_pages, "leere Seite", "leere Seiten")}</span>` : ""}</span>
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
    <details class="group" ${groups.length <= 4 || selecting ? "open" : ""}>
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
  if (selecting && e.currentTarget.id === "doc-tree") {
    const li = e.target.closest("li[data-id]");
    if (!li) return;
    const id = Number(li.dataset.id);
    selected.has(id) ? selected.delete(id) : selected.add(id);
    li.querySelector(".pick").checked = selected.has(id);
    updateMoveBar();
    return;
  }
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
  $("#doc-title").title = doc.title;
  const meta = [KIND_NAME[doc.kind], doc.title.includes(" / ") ? doc.title.split(" / ").slice(0, -1).join(" › ") : "",
    `hinzugefügt ${dateText(doc.created_at)}`, doc.status !== "ready" ? STATUS[doc.status] : "", doc.progress,
    doc.images_missing ? `${plural(doc.images_missing, "Bildseite", "Bildseiten")} mit Handschrift noch nicht gelesen (Tageslimit), wird automatisch nachgeholt` : "",
    doc.blank_pages ? `${plural(doc.blank_pages, "leere Seite", "leere Seiten")} (falls dort etwas stehen sollte: in OneNote öffnen, warten bis alles geladen ist, dann neu synchronisieren)` : ""];
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

function renderQuiz(box, questions, quizSubject, onAnswer = null) {
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
      if (onAnswer) onAnswer(q, right);
      if (right) score++;
      else btn.classList.add("wrong");
      if (!right && !onAnswer) {
        // ins Fehlerheft
        send("/api/mistakes", { subject: quizSubject || "", question: q.frage, options: q.optionen,
          right: q.richtig, explanation: q.erklaerung || "" }).catch(() => {});
      }
      div.querySelectorAll(".opts button")[q.richtig].classList.add("right");
      const expl = div.querySelector(".expl");
      expl.textContent = (right ? "Richtig. " : "Leider falsch. ") + (q.erklaerung || "");
      expl.hidden = false;
      const done = answered === questions.length;
      box.querySelector(".score").textContent = `${score} von ${answered} richtig${done && score === questions.length ? " 🎉" : ""}`;
      if (done) send("/api/quiz/result", { subject: quizSubject, score, total: questions.length }).catch(() => {});
      if (done && onAnswer) {
        box.insertAdjacentHTML("beforeend", `<button class="primary" style="margin-top:10px">Fertig</button>`);
        box.lastElementChild.addEventListener("click", () => { loadMistakes(); loadHome(); });
      }
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

let recorder = null, recChunks = [], recStart = 0, recTimer = null, wakeLock = null, recMarks = [];

// Laufende Aufnahmen werden alle 10 s im Browser zwischengespeichert, damit bei einem Absturz
// oder geschlossenen Fenster nichts verloren geht.
const recStore = {
  db: null,
  async open() {
    if (this.db) return this.db;
    this.db = await new Promise((resolve, reject) => {
      const req = indexedDB.open("notizapp-aufnahme", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("chunks", { autoIncrement: true });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return this.db;
  },
  async run(mode, fn) {
    try {
      const db = await this.open();
      return await new Promise((resolve, reject) => {
        const tx = db.transaction("chunks", mode);
        const result = fn(tx.objectStore("chunks"));
        tx.oncomplete = () => resolve(result?.result ?? result);
        tx.onerror = () => reject(tx.error);
      });
    } catch { return null; } // ohne Zwischenspeicher geht die Aufnahme trotzdem
  },
  add(item) { return this.run("readwrite", (st) => st.add(item)); },
  all() { return this.run("readonly", (st) => st.getAll()); },
  clear() { return this.run("readwrite", (st) => st.clear()); },
};

function recordSubject() {
  const sel = $("#rec-subject").value;
  return sel === "__new__" ? $("#rec-subject-new").value.trim() : sel;
}

async function uploadAudio(blob, filename, extra = {}) {
  const form = new FormData();
  form.append("file", blob, filename);
  form.append("title", extra.title ?? $("#rec-title").value);
  form.append("subject", extra.subject ?? recordSubject());
  form.append("marks", (extra.marks ?? []).join(","));
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

$("#rec-mark").addEventListener("click", () => {
  if (!recorder) return;
  recMarks.push(Math.round((Date.now() - recStart) / 1000));
  $("#rec-marks").textContent = `(${recMarks.length})`;
  recStore.add({ meta: true, marks: recMarks.slice() });
  toast("Stelle als wichtig markiert.");
});

async function startRecording() {
  if ($("#rec-subject").value === "__new__" && !recordSubject()) { toast("Gib dem neuen Fach einen Namen.", true); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true } });
    const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"].find((m) => MediaRecorder.isTypeSupported(m));
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 48000 } : undefined);
    recChunks = [];
    recMarks = [];
    await recStore.clear();
    await recStore.add({ meta: true, started: Date.now(), type: recorder.mimeType || "audio/webm",
      title: $("#rec-title").value, subject: recordSubject(), marks: [] });
    recorder.ondataavailable = (e) => {
      if (!e.data.size) return;
      recChunks.push(e.data);
      recStore.add({ blob: e.data });
    };
    recorder.start(10000); // alle 10 s ein Stück, damit bei einem Absturz weniger verloren geht
    recStart = Date.now();
    recTimer = setInterval(() => {
      const s = Math.floor((Date.now() - recStart) / 1000);
      $("#rec-time").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }, 1000);
    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* nicht unterstützt */ }
    $("#rec-btn").classList.add("on");
    $("#rec-btn").setAttribute("aria-label", "Aufnahme beenden und speichern");
    $("#rec-mark").hidden = false;
    $("#rec-marks").textContent = "";
    $("#rec-state").textContent = "Aufnahme läuft. Drück ⭐, wenn etwas Wichtiges kommt. Zum Beenden nochmals auf den roten Knopf.";
  } catch (e) {
    recorder = null;
    toast("Das Mikrofon liess sich nicht starten: " + e.message, true);
  }
}

function saveBlobAsFile(blob, ext) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `aufnahme.${ext}`;
  a.click();
}

function stopRecording() {
  recorder.onstop = async () => {
    recorder.stream.getTracks().forEach((t) => t.stop());
    clearInterval(recTimer);
    wakeLock?.release().catch(() => {});
    $("#rec-btn").classList.remove("on");
    $("#rec-btn").setAttribute("aria-label", "Aufnahme starten");
    $("#rec-mark").hidden = true;
    $("#rec-state").textContent = "Speichere…";
    const type = recorder.mimeType || "audio/webm";
    const ext = type.includes("ogg") ? "ogg" : "webm";
    const blob = new Blob(recChunks, { type });
    try {
      await uploadAudio(blob, `aufnahme-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${ext}`, { marks: recMarks });
      await recStore.clear();
      $("#rec-state").textContent = "Gespeichert. Die App macht daraus jetzt Text, Zusammenfassung und Karteikarten.";
      toast("Lektion gespeichert.");
    } catch (e) {
      // Aufnahme nicht verlieren: bleibt im Zwischenspeicher und wird zusätzlich als Datei angeboten
      saveBlobAsFile(blob, ext);
      $("#rec-state").textContent = "Hochladen hat nicht geklappt. Die Aufnahme wurde als Datei heruntergeladen.";
      toast(e.message, true);
    }
    $("#rec-time").textContent = "0:00";
    recorder = null;
  };
  recorder.stop();
}

async function checkUnsavedRecording() {
  if (recorder) return;
  const items = await recStore.all();
  const chunks = (items || []).filter((i) => i.blob);
  const box = $("#rec-recover");
  if (!chunks.length) { box.hidden = true; return; }
  const meta = Object.assign({}, ...(items || []).filter((i) => i.meta));
  const minutes = Math.max(1, Math.round(chunks.length / 6));
  box.hidden = false;
  box.innerHTML = `Eine Aufnahme${meta.started ? ` vom ${new Date(meta.started).toLocaleString("de-CH", { dateStyle: "short", timeStyle: "short" })}` : ""}
    (etwa ${minutes} Min.) wurde nicht fertig gespeichert.
    <div class="row" style="margin-top:8px"><button id="rec-restore" class="primary">Wiederherstellen und verarbeiten</button>
    <button id="rec-discard">Verwerfen</button></div>`;
  $("#rec-restore").onclick = (e) => busy(e.target, "Lade hoch…", async () => {
    const type = meta.type || "audio/webm";
    const blob = new Blob(chunks.map((c) => c.blob), { type });
    await uploadAudio(blob, `aufnahme-wiederhergestellt.${type.includes("ogg") ? "ogg" : "webm"}`,
      { title: meta.title || "Wiederhergestellte Aufnahme", subject: meta.subject ?? "", marks: meta.marks || [] });
    await recStore.clear();
    box.hidden = true;
    toast("Aufnahme wiederhergestellt. Sie wird jetzt verarbeitet.");
  });
  $("#rec-discard").onclick = async () => {
    if (!confirm("Die nicht gespeicherte Aufnahme endgültig verwerfen?")) return;
    await recStore.clear();
    box.hidden = true;
  };
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

// --- Heute lernen --------------------------------------------------------------

async function loadToday() {
  let t;
  try { t = await api("/api/today" + subjectQuery()); } catch { return; }
  const items = t.tasks.map((task) => `<li style="--tab:${tabColor(task.subject)}">
      <span class="what">${esc(task.task)}${task.goals.length ? `: ${task.goals.map(esc).join("; ")}` : ""}
        <small>${esc(task.subject ? task.subject + ", " : "")}Prüfung „${esc(task.title)}“ ${daysText(task.days_left)}</small></span>
      <button data-exam="${task.exam_id}">Öffnen</button></li>`);
  if (t.cards_due) items.push(`<li><span class="what">${plural(t.cards_due, "Karteikarte ist", "Karteikarten sind")} fällig</span>
      <button data-go="cards" class="primary">Lernen</button></li>`);
  if (t.mistakes_open) items.push(`<li><span class="what">${plural(t.mistakes_open, "Frage", "Fragen")} im Fehlerheft</span>
      <button data-go="mistakes">Üben</button></li>`);
  $("#home-today").hidden = !items.length;
  $("#today-list").innerHTML = items.join("");
}

$("#today-list").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.exam) { show("calendar"); openExam(Number(b.dataset.exam)); }
  else if (b.dataset.go === "cards") show("cards");
  else if (b.dataset.go === "mistakes") { show("learn"); $("#mistakes-box").scrollIntoView({ behavior: "smooth" }); }
});

// --- Fehlerheft ---------------------------------------------------------------

let mistakes = [];

async function loadMistakes() {
  try { mistakes = await api("/api/mistakes" + subjectQuery()); } catch { return; }
  $("#mistakes-practice").disabled = !mistakes.length;
  $("#mistakes-box").innerHTML = mistakes.length ? mistakes.map((m) => `<div class="mistake" data-id="${m.id}">
      <div class="q">${esc(m.question)}</div>
      <div class="a">Richtig: ${esc(m.options[m.right])}</div>
      ${m.explanation ? `<div class="hint">${esc(m.explanation)}</div>` : ""}
      <div class="row"><button class="link" data-card>Als Karteikarte</button><button class="link" data-done>Kann ich jetzt</button></div>
    </div>`).join("") : `<p class="hint">Noch keine Fehler gesammelt. 👍</p>`;
}

$("#mistakes-box").addEventListener("click", async (e) => {
  const box = e.target.closest(".mistake[data-id]");
  if (!box) return;
  try {
    if (e.target.closest("[data-card]")) {
      await send(`/api/mistakes/${box.dataset.id}/card`);
      toast("Als Karteikarte gespeichert.");
      loadStatus();
    } else if (e.target.closest("[data-done]")) {
      await send(`/api/mistakes/${box.dataset.id}/resolve`);
    } else return;
    loadMistakes();
  } catch (err) { toast(err.message, true); }
});

$("#mistakes-practice").addEventListener("click", () => {
  // Die gesammelten Fragen nochmals als Quiz, ohne KI. Richtig beantwortete verschwinden aus dem Heft.
  const list = [...mistakes].sort(() => Math.random() - 0.5).slice(0, 10);
  const questions = list.map((m) => ({ frage: m.question, optionen: m.options, richtig: m.right, erklaerung: m.explanation, id: m.id }));
  renderQuiz($("#mistakes-box"), questions, subject ?? "", (q, right) => {
    if (right) send(`/api/mistakes/${q.id}/resolve`).catch(() => {});
  });
});

// --- Erklär-Modus -------------------------------------------------------------

async function checkExplanation(goal, text, subj, out, button) {
  if (!goal.trim() || text.trim().length < 10) { toast("Schreib zuerst ein Thema und deine Erklärung.", true); return; }
  await busy(button, "Prüfe…", async () => {
    const { feedback, sources } = await send("/api/explain", { goal, explanation: text, subject: subj });
    const seen = new Set();
    const unique = sources.filter((x) => !seen.has(x.doc_id) && seen.add(x.doc_id));
    out.innerHTML = `<div class="msg bot explain-box"><div class="md">${md(feedback)}</div>
      <div class="sources">Verglichen mit: ${unique.map((x) => `<button data-id="${x.doc_id}">${esc(x.title.split(" / ").pop())}</button>`).join("")}</div></div>`;
    loadQuota();
  });
}

$("#explain-send").addEventListener("click", (e) =>
  checkExplanation($("#explain-goal").value, $("#explain-text").value, subject, $("#explain-result"), e.target));
$("#explain-result").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-id]");
  if (b) openDoc(Number(b.dataset.id));
});

// --- Stundenplan und Aufnahme auf dem Laptop ------------------------------------

const DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
let timetable = [], timetableSkipped = [], liveWasOn = false;

async function loadTimetable() {
  try {
    const t = await api("/api/timetable");
    timetable = t.entries;
    timetableSkipped = t.skipped || [];
    $("#tt-enabled").checked = t.enabled;
    $("#tt-pause").value = t.paused_until || "";
    renderTimetable();
    showRecordError(t.error);
  } catch (e) { toast(e.message, true); }
}

function showRecordError(error) {
  $("#tt-error").hidden = !error;
  $("#tt-error").textContent = error ? `${error} Prüfe, ob ein Mikrofon angeschlossen und in Windows erlaubt ist.` : "";
}

function renderTimetable() {
  const box = $("#tt-list");
  if (!timetable.length) {
    box.innerHTML = '<p class="tt-empty">Noch keine Lektionen. Füg mit „+ Lektion“ die erste hinzu.</p>';
  } else {
    box.innerHTML = timetable.map((e, i) => `
      <div class="tt-row${e.active ? "" : " off"}" data-i="${i}">
        <select data-k="day" aria-label="Tag">${DAYS.map((d, n) => `<option value="${n}"${n === e.day ? " selected" : ""}>${d}</option>`).join("")}</select>
        <input type="time" data-k="start" value="${esc(e.start)}" aria-label="Beginn">
        <input type="time" data-k="end" value="${esc(e.end)}" aria-label="Ende">
        <select data-k="subject" class="tt-subject" aria-label="Fach"></select>
        <input data-k="title" class="tt-title" value="${esc(e.title)}" placeholder="Name, z. B. Mathe bei Frau Keller" aria-label="Name">
        <label class="check" title="Diese Lektion aufnehmen"><input type="checkbox" data-k="active"${e.active ? " checked" : ""}> an</label>
        <button data-skip="${esc(e.id || "")}" ${!e.id || timetableSkipped.includes(e.id) ? "disabled" : ""} title="Nur heute nicht aufnehmen">Heute nicht</button>
        <button class="danger" data-del="${i}" aria-label="Lektion entfernen">✕</button>
      </div>`).join("");
    $$(".tt-row").forEach((row) => fillSubjectSelect(row.querySelector('[data-k="subject"]'), timetable[row.dataset.i].subject));
  }
  $("#tt-next").textContent = nextLessonText();
}

function nextLessonText() {
  if (!$("#tt-enabled").checked) return "Automatisches Aufnehmen ist ausgeschaltet.";
  const now = new Date();
  const pause = $("#tt-pause").value;
  for (let add = 0; add < 8; add++) {
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + add);
    const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    if (pause && iso <= pause) continue;
    const weekday = (day.getDay() + 6) % 7;
    const hm = add === 0 ? now.toTimeString().slice(0, 5) : "00:00";
    const next = timetable.filter((e) => e.active && e.day === weekday && e.end > hm && !(add === 0 && timetableSkipped.includes(e.id)))
      .sort((a, b) => a.start.localeCompare(b.start))[0];
    if (next) {
      const when = add === 0 ? "heute" : add === 1 ? "morgen" : `am ${DAYS[weekday]}`;
      return `Nächste Aufnahme: ${when} um ${next.start}, ${next.title || next.subject || "Lektion"}.`;
    }
  }
  return timetable.length ? "In den nächsten Tagen steht keine Aufnahme an." : "";
}

$("#tt-list").addEventListener("input", (e) => {
  const row = e.target.closest(".tt-row");
  const k = e.target.dataset.k;
  if (!row || !k) return;
  const entry = timetable[row.dataset.i];
  entry[k] = k === "active" ? e.target.checked : k === "day" ? Number(e.target.value) : e.target.value;
  if (k === "active") row.classList.toggle("off", !e.target.checked);
});

$("#tt-list").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) { timetable.splice(Number(del.dataset.del), 1); renderTimetable(); return; }
  const skip = e.target.closest("[data-skip]");
  if (skip) {
    await api(`/api/timetable/${encodeURIComponent(skip.dataset.skip)}/skip`, { method: "POST" });
    timetableSkipped.push(skip.dataset.skip);
    renderTimetable();
    toast("Diese Lektion wird heute nicht aufgenommen.");
  }
});

$("#tt-add").addEventListener("click", () => {
  const last = timetable[timetable.length - 1];
  const day = last ? last.day : Math.min((new Date().getDay() + 6) % 7, 4);
  timetable.push({ day, start: last ? last.end : "08:00", end: "", subject: subject ?? "", title: "", active: true });
  renderTimetable();
  $$(".tt-row").at(-1).querySelector('[data-k="end"]').focus();
});

$("#tt-enabled").addEventListener("change", () => ($("#tt-next").textContent = nextLessonText()));
$("#tt-pause").addEventListener("change", () => ($("#tt-next").textContent = nextLessonText()));

$("#tt-save").addEventListener("click", (e) => busy(e.currentTarget, "Speichere…", async () => {
  const missing = timetable.find((t) => !t.start || !t.end);
  if (missing) { toast("Bitte bei jeder Lektion Beginn und Ende eintragen.", true); return; }
  const t = await api("/api/timetable", { method: "PUT", body: JSON.stringify({
    entries: timetable, enabled: $("#tt-enabled").checked, paused_until: $("#tt-pause").value }) });
  timetable = t.entries;
  renderTimetable();
  toast("Stundenplan gespeichert.");
}));

// Ohne Ton: Die Aufnahme startet mitten im Unterricht.
function quietNotify(text) {
  toast(text);
  try { if (Notification.permission === "granted") new Notification("Notiz-App", { body: text, silent: true }); } catch { /* egal */ }
}

async function loadLiveRecording() {
  let s;
  try { s = await api("/api/recording"); } catch { return; }
  const rec = s.recording;
  $("#live-rec").hidden = !rec;
  if (!$("#view-record").hidden) showRecordError(s.error);
  if (rec) {
    const min = Math.floor(rec.seconds / 60);
    $("#live-text").textContent = `Aufnahme läuft: ${rec.title}${rec.subject ? ` (${subjectName(rec.subject)})` : ""}, seit ${min} Min.`
      + (rec.until ? `, endet um ${rec.until}` : "") + (rec.marks ? `, ${rec.marks} ⭐` : "")
      + (rec.silent ? ". Achtung: Das Mikrofon liefert keinen Ton. Ist es stummgeschaltet (Mikrofon-Taste) oder in Windows auf 0 gestellt?" : "");
    if (!liveWasOn) quietNotify(`Aufnahme läuft: ${rec.title}`);
  } else if (liveWasOn) {
    quietNotify("Aufnahme beendet. Die App macht jetzt Text und Zusammenfassung daraus.");
    refreshAll();
  }
  liveWasOn = !!rec;
}

$("#live-mark").addEventListener("click", async () => {
  try { await api("/api/recording/mark", { method: "POST" }); toast("Stelle als wichtig markiert."); loadLiveRecording(); }
  catch (e) { toast(e.message, true); }
});

$("#live-stop").addEventListener("click", (e) => busy(e.currentTarget, "Speichere…", async () => {
  if (!confirm("Aufnahme jetzt beenden? Diese Lektion wird heute nicht nochmals gestartet.")) return;
  await api("/api/recording/stop", { method: "POST" });
  toast("Aufnahme gespeichert. Sie wird jetzt verarbeitet.");
  await loadLiveRecording();
}));

// --- Lern-Timer -----------------------------------------------------------------

let timer = null; // { end, minutes, phase: "focus" | "pause", subject }

function openTimer() {
  if (timer) { if (confirm("Timer abbrechen?")) stopTimer(); return; }
  $("#timer-dialog").showModal();
}

function startTimer(minutes, phase = "focus") {
  timer = { end: Date.now() + minutes * 60000, minutes, phase, subject: subject ?? "" };
  if (phase === "focus" && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
  tickTimer();
}

function stopTimer() {
  timer = null;
  $("#timer-pill").hidden = true;
  document.title = subject === null ? "Notiz-App" : `${subjectName(subject)} – Notiz-App`;
}

function beep() {
  try {
    const ctx = new AudioContext();
    [0, 0.35, 0.7].forEach((t) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.2, ctx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + t + 0.3);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + t);
      o.stop(ctx.currentTime + t + 0.3);
    });
  } catch { /* kein Ton möglich */ }
}

function notify(text) {
  beep();
  toast(text);
  try { if (Notification.permission === "granted") new Notification("Notiz-App", { body: text }); } catch { /* egal */ }
}

function tickTimer() {
  if (!timer) return;
  const left = Math.max(0, Math.round((timer.end - Date.now()) / 1000));
  const text = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  const pill = $("#timer-pill");
  pill.hidden = false;
  pill.classList.toggle("pause", timer.phase === "pause");
  pill.textContent = (timer.phase === "focus" ? "⏱️ Lernen " : "☕ Pause ") + text;
  pill.title = "Klicken zum Abbrechen";
  document.title = `${text} ${timer.phase === "focus" ? "Lernen" : "Pause"} – Notiz-App`;
  if (left > 0) { setTimeout(tickTimer, 1000); return; }
  if (timer.phase === "focus") {
    send("/api/focus", { subject: timer.subject, minutes: timer.minutes }).then(() => { if (currentView === "home") loadHome(); }).catch(() => {});
    notify(`Super, ${timer.minutes} Minuten gelernt! Jetzt ${timer.minutes >= 45 ? 10 : 5} Minuten Pause.`);
    startTimer(timer.minutes >= 45 ? 10 : 5, "pause");
  } else {
    notify("Pause vorbei. Weiter geht's, wenn du bereit bist.");
    stopTimer();
  }
}

$("#open-timer").addEventListener("click", openTimer);
$("#timer-pill").addEventListener("click", openTimer);
$("#timer-close").addEventListener("click", () => $("#timer-dialog").close());
$("#timer-dialog").addEventListener("click", (e) => {
  const b = e.target.closest("[data-min]");
  if (!b) return;
  $("#timer-dialog").close();
  startTimer(Number(b.dataset.min));
});

// --- Sicherung ------------------------------------------------------------------

async function loadBackup() {
  try {
    const b = await api("/api/backup");
    $("#backup-info").textContent = b.last
      ? `Letzte Sicherung: ${new Date(b.last * 1000).toLocaleString("de-CH", { dateStyle: "medium", timeStyle: "short" })}, im Ordner ${b.folder}. Die letzten 7 Tage werden aufbewahrt.`
      : `Deine Notizen, Karten und Prüfungen werden jeden Tag in ${b.folder} gesichert.`;
  } catch { /* egal */ }
}

$("#backup-now").addEventListener("click", (e) => busy(e.target, "Sichere…", async () => {
  await send("/api/backup");
  toast("Gesichert.");
  loadBackup();
}));

// --- Notizen umbenennen und verschieben ---------------------------------------

let selecting = false;
const selected = new Set();

function setSelecting(on) {
  if (selecting === on) return;
  selecting = on;
  selected.clear();
  $("#doc-tree").classList.toggle("selecting", on);
  $("#select-mode").textContent = on ? "Fertig" : "Auswählen und verschieben";
  $("#move-bar").hidden = !on;
  if (on) {
    fillSubjectSelect($("#move-target"), subject ?? subjects[0]?.subject ?? "", false);
    $("#move-target").insertAdjacentHTML("beforeend", `<option value="__new__">Neues Fach…</option>`);
    $("#move-target").onchange = () => ($("#move-new").hidden = $("#move-target").value !== "__new__");
    $("#move-new").hidden = true;
  }
  updateMoveBar();
  if (currentView === "notes") renderDocs();
}

function updateMoveBar() {
  $("#move-count").textContent = selected.size ? plural(selected.size, "Notiz ausgewählt", "Notizen ausgewählt") : "Tippe Notizen an, um sie auszuwählen";
  $("#move-go").disabled = !selected.size;
}

$("#select-mode").addEventListener("click", () => setSelecting(!selecting));
$("#move-cancel").addEventListener("click", () => setSelecting(false));
$("#move-go").addEventListener("click", (e) => busy(e.target, "Verschiebe…", async () => {
  const target = $("#move-target").value === "__new__" ? $("#move-new").value.trim() : $("#move-target").value;
  if ($("#move-target").value === "__new__" && !target) throw new Error("Gib dem neuen Fach einen Namen.");
  const { moved } = await send("/api/documents/move", { ids: [...selected], subject: target });
  toast(`${plural(moved, "Notiz", "Notizen")} nach „${target || NO_SUBJECT}“ verschoben.`);
  setSelecting(false);
  await refreshAll();
}));

$("#doc-rename").addEventListener("click", async () => {
  const parts = currentDoc.title.split(" / ");
  const name = prompt("Neuer Name der Notiz:", parts.pop());
  if (!name || !name.trim()) return;
  const title = [...parts, name.trim()].join(" / "); // Notizbuch-Gruppe bleibt erhalten
  try {
    await send(`/api/documents/${currentDoc.id}`, { title }, "PATCH");
    toast("Umbenannt. Der Name bleibt auch nach dem nächsten OneNote-Abgleich.");
    await loadDocs();
    openDoc(currentDoc.id, true);
  } catch (err) { toast(err.message, true); }
});

// --- Fächer verwalten ---------------------------------------------------------

function renderSubjectAdmin() {
  const list = subjects.filter((s) => s.subject !== "");
  $("#subject-admin").innerHTML = list.length ? list.map((s, i) => `<li data-name="${esc(s.subject)}">
      <span class="swatch" style="--tab:${tabColor(s.subject)}"></span>
      <input value="${esc(s.subject)}" aria-label="Name von ${esc(s.subject)}">
      <span class="count">${plural(s.count, "Notiz", "Notizen")}</span>
      <button data-up ${i === 0 ? "disabled" : ""} aria-label="Nach oben">↑</button>
      <button data-down ${i === list.length - 1 ? "disabled" : ""} aria-label="Nach unten">↓</button>
      ${s.count ? "" : `<button data-remove class="danger" aria-label="Fach entfernen">Entfernen</button>`}
    </li>`).join("") : `<li class="hint">Noch keine Fächer.</li>`;
}

async function saveOrder(names) {
  subjects = await send("/api/subjects/order", { order: names });
  renderTabs();
  renderSubjectAdmin();
}

$("#subject-admin").addEventListener("click", async (e) => {
  const li = e.target.closest("li[data-name]");
  if (!li) return;
  const names = subjects.filter((s) => s.subject !== "").map((s) => s.subject);
  const i = names.indexOf(li.dataset.name);
  try {
    if (e.target.closest("[data-up]") && i > 0) [names[i - 1], names[i]] = [names[i], names[i - 1]];
    else if (e.target.closest("[data-down]") && i < names.length - 1) [names[i + 1], names[i]] = [names[i], names[i + 1]];
    else if (e.target.closest("[data-remove]")) {
      subjects = await api("/api/subjects?name=" + encodeURIComponent(li.dataset.name), { method: "DELETE" });
      toast(`„${li.dataset.name}“ entfernt.`);
      renderTabs();
      renderSubjectAdmin();
      return;
    } else return;
    await saveOrder(names);
  } catch (err) { toast(err.message, true); }
});

$("#subject-admin").addEventListener("change", async (e) => {
  const li = e.target.closest("li[data-name]");
  const old = li?.dataset.name, name = e.target.value.trim();
  if (!li || !name || name === old) { if (li) e.target.value = old; return; }
  const merge = subjects.some((s) => s.subject === name);
  if (merge && !confirm(`„${name}“ gibt es schon. Sollen „${old}“ und „${name}“ zusammengelegt werden?`)) { e.target.value = old; return; }
  try {
    await send("/api/subjects/rename", { old, new: name });
    if (subject === old) { subject = name; saveSubject(); }
    toast(merge ? `„${old}“ ist jetzt in „${name}“.` : `„${old}“ heisst jetzt „${name}“.`);
    await refreshAll();
    renderSubjectAdmin();
  } catch (err) { toast(err.message, true); }
});

$("#subject-add").addEventListener("click", async () => {
  const name = $("#subject-add-name").value.trim();
  if (!name) { toast("Gib zuerst einen Namen ein.", true); return; }
  try {
    subjects = await send("/api/subjects", { name });
    $("#subject-add-name").value = "";
    toast(`Fach „${name}“ angelegt.`);
    renderTabs();
    renderSubjectAdmin();
  } catch (err) { toast(err.message, true); }
});
$("#subject-add-name").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#subject-add").click(); });

// --- Prüfungen ----------------------------------------------------------------

const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
let calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let exams = [];
let planDays = new Map();   // Datum -> Prüfungen, für die an dem Tag gelernt wird
let currentExam = null;
let editingExam = null;

const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const longDate = (iso) => new Date(iso + "T12:00").toLocaleDateString("de-CH", { weekday: "long", day: "numeric", month: "long" });
const shortDate = (iso) => new Date(iso + "T12:00").toLocaleDateString("de-CH", { weekday: "short", day: "numeric", month: "short" });

function daysText(n) {
  if (n === 0) return "heute";
  if (n === 1) return "morgen";
  if (n < 0) return n === -1 ? "gestern" : `vor ${-n} Tagen`;
  return `in ${n} Tagen`;
}

async function loadExams() {
  exams = await api("/api/exams" + subjectQuery());
}

async function loadHomeExam() {
  try { await loadExams(); } catch { return; }
  const next = exams.find((e) => e.days_left >= 0);
  const strip = $("#home-exam");
  strip.hidden = !next;
  if (!next) return;
  strip.style.setProperty("--tab", tabColor(next.subject));
  const goals = next.goals_total ? `, ${next.goals_done} von ${next.goals_total} Lernzielen geschafft` : "";
  strip.innerHTML = `<span>📅 Nächste Prüfung: <strong>${esc(next.subject ? next.subject + ": " : "")}${esc(next.title)}</strong>
    <span class="hint">${esc(longDate(next.date))}${goals}</span></span><span class="days">${daysText(next.days_left)}</span>`;
  strip.onclick = () => { show("calendar"); openExam(next.id); };
}

async function loadCalendar() {
  try { await loadExams(); } catch (e) { toast(e.message, true); return; }
  // Lerntage aus den Plänen der kommenden Prüfungen markieren
  planDays = new Map();
  const upcoming = exams.filter((e) => e.days_left > 0);
  const details = await Promise.all(upcoming.slice(0, 6).map((e) => api(`/api/exams/${e.id}`).catch(() => null)));
  for (const d of details.filter(Boolean)) {
    for (const p of d.plan) {
      if (!planDays.has(p.date)) planDays.set(p.date, []);
      planDays.get(p.date).push(d);
    }
  }
  renderCalendar();
  renderExamList();
}

function renderCalendar() {
  $("#cal-title").textContent = `${MONTHS[calMonth.getMonth()]} ${calMonth.getFullYear()}`;
  const start = new Date(calMonth);
  start.setDate(1 - ((calMonth.getDay() + 6) % 7)); // Montag vor dem Monatsersten
  const today = isoDate(new Date());
  const byDate = {};
  for (const e of exams) (byDate[e.date] ||= []).push(e);
  let html = "";
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const iso = isoDate(d);
    const items = byDate[iso] || [];
    const learn = planDays.get(iso) || [];
    const cls = ["cal-day", d.getMonth() !== calMonth.getMonth() ? "other" : "", iso === today ? "today" : "",
      iso < today ? "past" : "", learn.length ? "plan" : ""].join(" ");
    const label = `${d.getDate()}. ${MONTHS[d.getMonth()]}` + (items.length ? `: ${items.map((e) => e.title).join(", ")}` : "")
      + (learn.length ? ", Lerntag" : "");
    html += `<button class="${cls}" data-date="${iso}" aria-label="${esc(label)}">
      <span class="num">${d.getDate()}</span>
      ${items.map((e) => `<span class="chip" data-exam="${e.id}" style="--tab:${tabColor(e.subject)}" title="${esc(e.subject)}: ${esc(e.title)}">${esc(e.subject || e.title)}</span>`).join("")}
    </button>`;
    if (i === 34 && d.getMonth() !== calMonth.getMonth()) break; // 5 Wochen reichen
  }
  $("#cal-grid").innerHTML = html;
}

function renderExamList() {
  const upcoming = exams.filter((e) => e.days_left >= 0);
  $("#exam-list").innerHTML = upcoming.length ? upcoming.map((e) => `<li data-exam="${e.id}" style="--tab:${tabColor(e.subject)}">
      <span class="when ${e.days_left <= 3 ? "soon" : ""}">${daysText(e.days_left)}</span>
      <strong>${esc(e.title)}</strong><br>
      <span class="hint">${esc(e.subject || NO_SUBJECT)}, ${esc(shortDate(e.date))}</span>
      ${e.goals_total ? `<div class="bar" title="${e.goals_done} von ${e.goals_total} Lernzielen"><span style="width:${100 * e.goals_done / e.goals_total}%"></span></div>` : ""}
    </li>`).join("") : `<li class="hint" style="border:0">Keine Prüfungen eingetragen.${subject === null ? "" : ` (nur ${esc(subjectName(subject))})`}</li>`;
}

$("#cal-prev").addEventListener("click", () => { calMonth.setMonth(calMonth.getMonth() - 1); renderCalendar(); });
$("#cal-next").addEventListener("click", () => { calMonth.setMonth(calMonth.getMonth() + 1); renderCalendar(); });
$("#cal-today").addEventListener("click", () => { calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1); renderCalendar(); });
$("#cal-grid").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-exam]");
  if (chip) { openExam(Number(chip.dataset.exam)); return; }
  const day = e.target.closest("[data-date]");
  if (day) editExam(null, day.dataset.date);
});
$("#exam-list").addEventListener("click", (e) => {
  const li = e.target.closest("[data-exam]");
  if (li) openExam(Number(li.dataset.exam));
});
$("#exam-new").addEventListener("click", () => editExam(null));

function editExam(exam, date) {
  editingExam = exam;
  $("#exam-detail").hidden = true;
  $("#exam-form").hidden = false;
  $("#exam-form-title").textContent = exam ? "Prüfung bearbeiten" : "Prüfung eintragen";
  fillSubjectSelect($("#exam-subject"), exam ? exam.subject : subject ?? subjects[0]?.subject ?? "", false);
  $("#exam-subject").insertAdjacentHTML("beforeend", `<option value="__new__">Neues Fach…</option>`);
  $("#exam-subject").onchange = () => ($("#exam-subject-new").hidden = $("#exam-subject").value !== "__new__");
  $("#exam-subject-new").hidden = true;
  $("#exam-title").value = exam?.title || "";
  $("#exam-date").value = exam?.date || date || isoDate(new Date(Date.now() + 7 * 86400000));
  $("#exam-goals").value = exam ? exam.goals.map((g) => g.text).join("\n") : "";
  $("#exam-delete").hidden = !exam;
  $("#exam-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#exam-title").focus({ preventScroll: true });
}

$("#exam-cancel").addEventListener("click", () => { $("#exam-form").hidden = true; if (editingExam) openExam(editingExam.id); });

$("#exam-goals-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const { goals_text } = await api("/api/exams/goals-from-file", { method: "POST", body: form });
    const current = $("#exam-goals").value.trim();
    $("#exam-goals").value = (current ? current + "\n" : "") + goals_text;
    toast("Lernziele übernommen. Lösche Zeilen, die keine Lernziele sind.");
  } catch (err) { toast(err.message, true); }
  e.target.value = "";
});

$("#exam-save").addEventListener("click", (e) => busy(e.target, "Speichere…", async () => {
  const subj = $("#exam-subject").value === "__new__" ? $("#exam-subject-new").value.trim() : $("#exam-subject").value;
  const body = { subject: subj, title: $("#exam-title").value.trim(), date: $("#exam-date").value, goals_text: $("#exam-goals").value };
  if (!body.title) throw new Error("Gib der Prüfung ein Thema, z. B. „Zahlensysteme“.");
  if (!body.date) throw new Error("Wähle ein Datum.");
  const saved = editingExam ? await send(`/api/exams/${editingExam.id}`, body, "PATCH") : await send("/api/exams", body);
  if (subj && !subjects.some((s) => s.subject === subj)) await send("/api/subjects", { name: subj });
  toast(editingExam ? "Prüfung gespeichert." : "Prüfung eingetragen.");
  $("#exam-form").hidden = true;
  calMonth = new Date(saved.date.slice(0, 4), Number(saved.date.slice(5, 7)) - 1, 1);
  await loadSubjects();
  await loadCalendar();
  openExam(saved.id);
}));

$("#exam-delete").addEventListener("click", async () => {
  if (!confirm(`Prüfung „${editingExam.title}“ löschen?`)) return;
  await api(`/api/exams/${editingExam.id}`, { method: "DELETE" });
  toast("Prüfung gelöscht.");
  $("#exam-form").hidden = true;
  editingExam = null;
  loadCalendar();
});

async function openExam(id) {
  if (currentView !== "calendar") show("calendar");
  const box = $("#exam-detail");
  box.hidden = false;
  $("#exam-form").hidden = true;
  box.innerHTML = `<p class="hint">Suche passende Notizen…</p>`;
  let ex;
  try { ex = currentExam = await api(`/api/exams/${id}`); } catch (e) { box.innerHTML = `<p class="error-text">${esc(e.message)}</p>`; return; }
  const today = isoDate(new Date());
  const open = ex.goals.filter((g) => !g.done).length;
  const goalsHtml = ex.goals.length ? ex.goals.map((g, i) => `<div class="goal ${g.done ? "done" : ""}">
      <label><input type="checkbox" data-goal="${i}" ${g.done ? "checked" : ""}> ${esc(g.text)}</label>
      <div class="found"><button class="link" data-explain="${i}">✍️ In eigenen Worten erklären</button></div>
      <div class="explain-area" data-area="${i}" hidden style="margin-left:28px">
        <textarea rows="4" placeholder="Erkläre das Lernziel so, als würdest du es jemandem beibringen…"></textarea>
        <button class="primary" data-check="${i}">Erklärung prüfen</button><div class="explain-out"></div>
      </div>
      ${g.docs.length ? `<div class="found">${g.docs.map((d) => `<button data-doc="${d.doc_id}" title="${esc(d.snippet)}">${esc(d.title.split(" / ").pop())}</button>`).join("")}</div>`
        : g.done ? "" : `<p class="missing">Dazu habe ich nichts in deinen Notizen gefunden. Schau im Heft nach oder nimm die Lektion dazu auf.</p>`}
    </div>`).join("")
    : `<p class="hint">Keine Lernziele eingetragen. Quiz und Karteikarten nehmen dann den ganzen Stoff von ${esc(ex.subject || "diesem Fach")}.
       <button class="link" data-edit>Lernziele hinzufügen</button></p>`;
  const planHtml = ex.plan.length ? `<h3 style="margin-top:18px">Lernplan bis zur Prüfung</h3><ul class="plan-list">${ex.plan.map((p) => `
      <li class="${p.date === today ? "today" : ""}"><span class="day">${p.date === today ? "Heute" : esc(shortDate(p.date))}</span>
        <span>${esc(p.task)}${p.goals.length ? `: ${p.goals.map(esc).join("; ")}` : ""}</span></li>`).join("")}</ul>` : "";
  box.innerHTML = `
    <div class="row between">
      <div><h2>${esc(ex.title)}</h2>
        <p class="hint">${esc(ex.subject || NO_SUBJECT)}, ${esc(longDate(ex.date))}, <strong>${daysText(ex.days_left)}</strong></p></div>
      <button data-edit>Bearbeiten</button>
    </div>
    <div class="row" style="margin:6px 0 10px">
      <button class="primary" data-quiz ${ex.days_left < 0 ? "" : ""}>Quiz ${ex.goals.length ? (open ? "zu den offenen Lernzielen" : "zu allen Lernzielen") : "zum Fach"}</button>
      <button data-cards>Karteikarten dazu erstellen</button>
    </div>
    <div id="exam-quiz"></div>
    ${ex.goals.length ? `<h3 style="margin-top:14px">Lernziele <span class="hint">(${ex.goals_done} von ${ex.goals_total} abgehakt)</span></h3>` : ""}
    ${goalsHtml}
    ${planHtml}`;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#exam-detail").addEventListener("click", async (e) => {
  const ex = currentExam;
  if (!ex) return;
  const explainBtn = e.target.closest("[data-explain]");
  const checkBtn = e.target.closest("[data-check]");
  if (explainBtn) {
    const area = $(`#exam-detail [data-area="${explainBtn.dataset.explain}"]`);
    area.hidden = !area.hidden;
    if (!area.hidden) area.querySelector("textarea").focus();
  } else if (checkBtn) {
    const area = checkBtn.closest(".explain-area");
    checkExplanation(ex.goals[Number(checkBtn.dataset.check)].text, area.querySelector("textarea").value,
      ex.subject || null, area.querySelector(".explain-out"), checkBtn);
  } else if (e.target.closest(".explain-out button[data-id]")) openDoc(Number(e.target.closest("button[data-id]").dataset.id));
  else if (e.target.closest("[data-edit]")) editExam(ex);
  else if (e.target.closest("[data-doc]")) openDoc(Number(e.target.closest("[data-doc]").dataset.doc));
  else if (e.target.closest("[data-quiz]")) busy(e.target.closest("button"), "Erstelle Quiz…", async () => {
    const { fragen } = await send(`/api/exams/${ex.id}/quiz?n=${Math.min(12, Math.max(5, ex.goals.length * 2))}`);
    renderQuiz($("#exam-quiz"), fragen, ex.subject);
  });
  else if (e.target.closest("[data-cards]")) busy(e.target.closest("button"), "Erstelle Karten…", async () => {
    const { created } = await send(`/api/exams/${ex.id}/flashcards`);
    toast(`${plural(created, "Karteikarte", "Karteikarten")} zu dieser Prüfung erstellt.`);
    loadStatus();
  });
});

$("#exam-detail").addEventListener("change", async (e) => {
  const box = e.target.closest("[data-goal]");
  if (!box || !currentExam) return;
  const done = currentExam.goals.map((g, i) => (i === Number(box.dataset.goal) ? box.checked : g.done));
  try {
    await send(`/api/exams/${currentExam.id}`, { done }, "PATCH");
    if (box.checked) toast("Lernziel abgehakt. 💪");
    await loadCalendar();
    openExam(currentExam.id);
  } catch (err) { toast(err.message, true); }
});

// --- Start -------------------------------------------------------------------

refreshAll().then(() => show("home"));
loadOnenote();
setInterval(loadStatus, 30000);
loadLiveRecording();
setInterval(loadLiveRecording, 15000);
