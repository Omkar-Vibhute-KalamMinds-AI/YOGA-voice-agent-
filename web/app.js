const transcript = document.querySelector("#transcript");
const welcome = document.querySelector("#welcome");
const composer = document.querySelector("#composer");
const messageBox = document.querySelector("#message");
const sendButton = document.querySelector("#send");
const micButton = document.querySelector("#mic");
const cancelVoiceButton = document.querySelector("#voice-cancel");
const voiceOutInput = document.querySelector("#voice-out");
const languageSelect = document.querySelector("#voice-language");
const voiceSpeakerSelect = document.querySelector("#voice-speaker");
const chatList = document.querySelector("#chat-list");
const title = document.querySelector("#thread-title");
const userIdInput = document.querySelector("#user-id");
const savedNote = document.querySelector("#saved-note");
const appShell = document.querySelector("#app");
const sidebar = document.querySelector("#sidebar");
const scrim = document.querySelector("#scrim");
const menuToggle = document.querySelector("#menu-toggle");
const sidebarClose = document.querySelector("#sidebar-close");

let threads = [];
let activeId = null;
let busy = false;
let waitingApproval = false;
let mediaRecorder = null;
let recordingChunks = [];
let listening = false;
let sendOnStop = false;
let playback = document.querySelector("#yoga-speaker");
let speechCtx = null;
let keepAliveNode = null;
let heldUtterance = null;
let lastFromVoice = false;
let detectedLanguage = "";
let lastSpeakUrl = "";
let lastSpokenText = "";
let speakToken = 0;
let speakAbort = null;
let speechSource = null;
let ttsQueue = [];
let ttsBusy = false;
let speakSources = [];
let speakClock = 0;
let liveSocket = null;

userIdInput.value = localStorage.getItem("yoga-user-id") || "Fardeen";
voiceOutInput.checked = localStorage.getItem("yoga-voice-out") !== "0";
languageSelect.value = localStorage.getItem("yoga-voice-language") || "unknown";
const VOICE_ALIASES = {
  anushka: "arya",
  manisha: "vani",
  vidya: "dhwani",
  priya: "arya",
  ritu: "vani",
  neha: "dhwani",
  pooja: "nirali",
  simran: "rohini",
  ishita: "maya",
  shubh: "indra",
  aditya: "krishna",
  rahul: "vayu",
  ratan: "varun",
  amit: "kartik",
  abhilash: "indra",
  karun: "krishna",
  hitesh: "vayu",
};
const savedSpeaker = VOICE_ALIASES[localStorage.getItem("yoga-voice-speaker")] || localStorage.getItem("yoga-voice-speaker") || "arya";
voiceSpeakerSelect.value = savedSpeaker;
if (![...voiceSpeakerSelect.options].some((option) => option.value === voiceSpeakerSelect.value)) {
  voiceSpeakerSelect.value = "arya";
}

function userId() {
  return userIdInput.value.trim() || "Fardeen";
}

function languageCode() {
  const chosen = languageSelect.value || "unknown";
  if (chosen === "unknown") return detectedLanguage || "unknown";
  return chosen;
}

function speakerCode() {
  return voiceSpeakerSelect.value || "arya";
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderText(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

const ICONS = {
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>',
  restart: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
};

function addAction(bar, label, icon, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "action";
  button.title = label;
  button.innerHTML = `${icon}<span>${label}</span>`;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick(button);
  });
  bar.appendChild(button);
  return button;
}

function copyText(text, button) {
  navigator.clipboard.writeText(text).then(() => {
    const caption = button.querySelector("span");
    const previous = caption.textContent;
    caption.textContent = "Copied";
    button.classList.add("show");
    setTimeout(() => {
      caption.textContent = previous;
    }, 1200);
  });
}

function addBubble(role, content, userIndex) {
  if (role === "Tool") return null;
  welcome.hidden = true;
  const bubble = document.createElement("article");
  bubble.className = `bubble ${role === "User" ? "user" : role === "Tool" ? "tool" : "ai"}`;
  bubble.dataset.raw = content;
  if (role === "User") bubble.dataset.userIndex = String(userIndex);
  bubble.innerHTML = `<span class="who">${role}</span><div class="body"></div><div class="actions"></div>`;
  bubble.querySelector(".body").innerHTML = renderText(content);
  const bar = bubble.querySelector(".actions");
  addAction(bar, "Copy", ICONS.copy, (button) => copyText(bubble.dataset.raw || "", button));
  if (role === "User") {
    addAction(bar, "Edit", ICONS.edit, () => startEdit(bubble));
    addAction(bar, "Restart", ICONS.restart, () => sendMessage(bubble.dataset.raw, { replaceUserIndex: Number(bubble.dataset.userIndex) }));
  }
  transcript.appendChild(bubble);
  transcript.scrollTop = transcript.scrollHeight;
  return bubble;
}

function startEdit(bubble) {
  if (busy || bubble.querySelector("textarea")) return;
  const body = bubble.querySelector(".body");
  const bar = bubble.querySelector(".actions");
  body.hidden = true;
  bar.hidden = true;
  const box = document.createElement("div");
  box.className = "edit-box";
  box.innerHTML = `<textarea></textarea><div class="edit-actions"><button type="button" class="save">Save</button><button type="button" class="cancel">Cancel</button></div>`;
  const area = box.querySelector("textarea");
  area.value = bubble.dataset.raw || "";
  box.querySelector(".cancel").addEventListener("click", () => {
    box.remove();
    body.hidden = false;
    bar.hidden = false;
  });
  box.querySelector(".save").addEventListener("click", () => {
    const next = area.value.trim();
    if (!next) return;
    sendMessage(next, { replaceUserIndex: Number(bubble.dataset.userIndex) });
  });
  bubble.appendChild(box);
  area.focus();
}

function showWelcome() {
  transcript.querySelectorAll(".bubble").forEach((node) => node.remove());
  welcome.hidden = false;
  title.textContent = "YOGA Voice Agent";
}

function renderThreads() {
  chatList.innerHTML = "";
  if (!threads.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "No chats in this session yet.";
    chatList.appendChild(item);
    return;
  }
  for (const thread of threads) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = thread.title;
    button.classList.toggle("active", thread.id === activeId);
    button.addEventListener("click", () => openThread(thread.id));
    item.appendChild(button);
    chatList.appendChild(item);
  }
}

async function refreshThreads() {
  const response = await fetch("/api/threads");
  const data = await response.json();
  threads = data.threads;
  renderThreads();
}

async function createThread() {
  const response = await fetch("/api/threads", { method: "POST" });
  const data = await response.json();
  activeId = data.id;
  threads = [{ id: data.id, title: data.title }, ...threads.filter((thread) => thread.id !== data.id)];
  renderThreads();
  showWelcome();
  if (isNarrow()) closeSidebar();
  messageBox.focus();
}

async function openThread(threadId) {
  const response = await fetch(`/api/threads/${threadId}`);
  if (!response.ok) return;
  const data = await response.json();
  activeId = data.id;
  title.textContent = data.title;
  transcript.querySelectorAll(".bubble").forEach((node) => node.remove());
  if (!data.messages.length) {
    welcome.hidden = false;
  } else {
    welcome.hidden = true;
    for (const message of data.messages) {
      if (message.role === "Tool") continue;
      addBubble(message.role, message.content, message.index);
    }
  }
  renderThreads();
  if (isNarrow()) closeSidebar();
}

async function readStream(response, bubble, options = {}) {
  const body = bubble.querySelector(".body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  let requests = null;
  while (true) {
    const step = await reader.read();
    if (step.done) break;
    buffer += decoder.decode(step.value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((row) => row.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      if (event.error) {
        body.textContent = event.error;
        bubble.dataset.raw = event.error;
        answer = "";
      } else if (event.text) {
        answer += event.text;
        body.innerHTML = renderText(answer);
        bubble.dataset.raw = answer;
        transcript.scrollTop = transcript.scrollHeight;
        if (options.speakEarly && typeof options.onSpeechProgress === "function") {
          options.onSpeechProgress(answer);
        }
      } else if (event.reply_done && typeof options.onReplyDone === "function") {
        options.onReplyDone(answer || bubble.dataset.raw);
      } else if (event.interrupt) {
        requests = event.interrupt;
        stopSpeech();
      } else if (event.title) {
        const current = threads.find((thread) => thread.id === activeId);
        if (current) current.title = event.title;
        title.textContent = event.title;
        renderThreads();
      }
    }
  }
  return { answer, requests };
}

const FIELD_UI = {
  date: { label: "Date", type: "date", placeholder: "YYYY-MM-DD" },
  amount: { label: "Amount", type: "number", placeholder: "0.00", step: "0.01" },
  category: { label: "Category", type: "text", placeholder: "Food, travel, salon…" },
  subcategory: { label: "Subcategory", type: "text", placeholder: "Optional" },
  note: { label: "Note", type: "text", placeholder: "Optional detail" },
  query: { label: "Wikipedia search", type: "text", placeholder: "What to look up" },
  id: { label: "Expense ID", type: "number", placeholder: "ID" },
};

const EXPENSE_FIELD_ORDER = ["date", "amount", "category", "subcategory", "note"];

function prettyLabel(key) {
  if (FIELD_UI[key]) return FIELD_UI[key].label;
  return key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function toDateInput(value) {
  const text = String(value || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  const slash = text.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/);
  if (slash) {
    const day = slash[1].padStart(2, "0");
    const month = slash[2].padStart(2, "0");
    return `${slash[3]}-${month}-${day}`;
  }
  return text;
}

function orderedFields(kind, fields) {
  const source = fields && typeof fields === "object" ? { ...fields } : {};
  if (kind !== "expense_add") return source;
  const ordered = {};
  for (const key of EXPENSE_FIELD_ORDER) {
    ordered[key] = source[key] ?? "";
  }
  return ordered;
}

function parseRecord(record) {
  try {
    const parsed = JSON.parse(record);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
  } catch (_) {}
  return null;
}

function fieldValue(raw) {
  const text = String(raw).trim();
  if (!text) return "";
  try {
    return JSON.parse(text);
  } catch (_) {
    return raw;
  }
}

function collectEditedRecord(editor, fallback) {
  if (editor.classList.contains("fields")) {
    const data = {};
    editor.querySelectorAll("[data-field]").forEach((input) => {
      const key = input.dataset.field;
      let value = input.value;
      if (key === "amount" || key === "id") {
        value = value === "" ? "" : Number(value);
      }
      data[key] = value;
    });
    return JSON.stringify(data, null, 2);
  }
  return editor.value || fallback || "";
}

function showApproval(requests) {
  waitingApproval = true;
  sendButton.disabled = true;
  micButton.disabled = true;
  stopSpeech();
  const form = document.createElement("form");
  form.className = "approval";
  const chosen = new Map();
  const tryResume = () => {
    if (chosen.size === requests.length) resumeTurn([...chosen.values()], form);
  };
  for (const request of requests) {
    const block = document.createElement("section");
    const ask = document.createElement("p");
    ask.className = "ask";
    ask.textContent = request.question;
    const fields = orderedFields(request.kind, parseRecord(request.record) || request.args || {});
    const preview = document.createElement("div");
    preview.className = "record-summary";
    const editor = document.createElement(Object.keys(fields).length ? "div" : "textarea");
    editor.className = Object.keys(fields).length ? "fields" : "record-edit";
    editor.hidden = true;
    if (Object.keys(fields).length) {
      for (const [key, value] of Object.entries(fields)) {
        const spec = FIELD_UI[key] || { label: prettyLabel(key), type: "text", placeholder: "" };
        const row = document.createElement("div");
        row.className = "summary-row";
        const caption = document.createElement("span");
        caption.textContent = spec.label;
        const strong = document.createElement("strong");
        strong.textContent = value === "" || value == null ? "—" : String(value);
        row.append(caption, strong);
        preview.appendChild(row);

        const field = document.createElement("div");
        field.className = "field";
        const label = document.createElement("label");
        label.setAttribute("for", `${request.id}-${key}`);
        label.textContent = spec.label;
        const input = document.createElement("input");
        input.type = spec.type || "text";
        input.id = `${request.id}-${key}`;
        input.dataset.field = key;
        input.autocomplete = "off";
        input.placeholder = spec.placeholder || "";
        if (spec.step) input.step = spec.step;
        if (spec.type === "date") input.value = toDateInput(value);
        else input.value = value == null ? "" : String(value);
        field.append(label, input);
        editor.appendChild(field);
      }
    } else {
      editor.value = request.record || "";
      preview.className = "record-preview";
      preview.textContent = request.record || "";
    }
    const hint = document.createElement("p");
    hint.className = "edit-hint";
    hint.hidden = true;
    hint.textContent = "Update any field, then save to continue.";
    const choices = document.createElement("div");
    choices.className = "choices";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "save-edit";
    save.textContent = "Save and add";
    save.hidden = true;
    const pick = (action) => {
      choices.querySelectorAll("button").forEach((item) => item.classList.remove("picked"));
      const target = [...choices.querySelectorAll("button")].find((item) => item.classList.contains(action));
      if (target) target.classList.add("picked");
    };
    const yes = document.createElement("button");
    yes.type = "button";
    yes.className = "yes";
    yes.textContent = request.kind === "wikipedia" ? "Yes, search" : "Yes, add it";
    yes.addEventListener("click", () => {
      pick("yes");
      chosen.set(request.id, { id: request.id, action: "yes" });
      tryResume();
    });
    const no = document.createElement("button");
    no.type = "button";
    no.className = "no";
    no.textContent = "No";
    no.addEventListener("click", () => {
      pick("no");
      chosen.set(request.id, { id: request.id, action: "no" });
      tryResume();
    });
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "edit";
    edit.textContent = "Edit fields";
    edit.addEventListener("click", () => {
      pick("edit");
      chosen.delete(request.id);
      preview.hidden = true;
      editor.hidden = false;
      hint.hidden = false;
      save.hidden = false;
      yes.disabled = true;
      no.disabled = true;
      const first = editor.querySelector("input, textarea");
      if (first) first.focus();
    });
    save.addEventListener("click", () => {
      chosen.set(request.id, {
        id: request.id,
        action: "edit",
        record: collectEditedRecord(editor, request.record),
      });
      tryResume();
    });
    choices.append(yes, no, edit, save);
    block.append(ask, preview, editor, hint, choices);
    form.appendChild(block);
  }
  transcript.appendChild(form);
  transcript.scrollTop = transcript.scrollHeight;
  const spoken = requests.map((item) => item.question).filter(Boolean).join(" ");
  speakReply(spoken, true, languageCode());
}

async function resumeTurn(decisions, form) {
  form.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
  });
  waitingApproval = false;
  busy = true;
  const bubble = addBubble("AI", "");
  const body = bubble.querySelector(".body");
  body.textContent = "…";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: activeId,
        message: "",
        user_id: userId(),
        decisions,
        language: languageCode(),
      }),
    });
    if (!response.ok || !response.body) {
      body.textContent = "The assistant could not continue.";
      return;
    }
    const result = await readStream(response, bubble, followSpeech(lastFromVoice, bubble));
    if (!result.answer && body.textContent === "…") bubble.remove();
    if (result.requests) showApproval(result.requests);
  } catch (error) {
    body.textContent = error.message || "The connection dropped.";
    bubble.dataset.raw = body.textContent;
  } finally {
    busy = false;
    if (!waitingApproval) {
      sendButton.disabled = false;
      micButton.disabled = false;
      if (!lastFromVoice) messageBox.focus();
    }
  }
}

async function sendMessage(text, options = {}) {
  lastFromVoice = Boolean(options.fromVoice);
  const message = text.trim();
  if (!message || busy || waitingApproval) return;
  if (!activeId) await createThread();
  busy = true;
  sendButton.disabled = true;
  micButton.disabled = true;
  stopSpeech();
  const replaceUserIndex = options.replaceUserIndex;
  if (replaceUserIndex === undefined) {
    addBubble("User", message, transcript.querySelectorAll(".bubble.user").length);
  } else {
    const target = transcript.querySelector(`.bubble.user[data-user-index="${replaceUserIndex}"]`);
    if (target) {
      target.dataset.raw = message;
      target.querySelector(".body").innerHTML = renderText(message);
      target.querySelector(".body").hidden = false;
      const bar = target.querySelector(".actions");
      if (bar) bar.hidden = false;
      target.querySelector(".edit-box")?.remove();
      let node = target.nextElementSibling;
      while (node) {
        const next = node.nextElementSibling;
        if (node.classList.contains("bubble") || node.classList.contains("approval")) node.remove();
        node = next;
      }
    }
  }
  messageBox.value = "";
  const bubble = addBubble("AI", "");
  const body = bubble.querySelector(".body");
  body.textContent = "…";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: activeId,
        message,
        user_id: userId(),
        replace_user_index: replaceUserIndex,
        language: languageCode(),
      }),
    });
    if (!response.ok || !response.body) {
      body.textContent = "The assistant could not answer.";
      return;
    }
    const result = await readStream(response, bubble, followSpeech(options.fromVoice, bubble));
    if (!result.answer && body.textContent === "…") {
      if (result.requests) bubble.remove();
      else {
        body.textContent = "No reply text came back.";
        bubble.dataset.raw = body.textContent;
      }
    }
    if (result.requests) showApproval(result.requests);
  } catch (error) {
    body.textContent = error.message || "The connection dropped.";
    bubble.dataset.raw = body.textContent;
  } finally {
    busy = false;
    if (!waitingApproval) {
      sendButton.disabled = false;
      micButton.disabled = false;
      if (!lastFromVoice) messageBox.focus();
    }
  }
}

function unlockAudio() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (AudioContextClass && (!speechCtx || speechCtx.state === "closed")) {
    speechCtx = new AudioContextClass();
    keepAliveNode = null;
  }
  if (speechCtx && speechCtx.state === "suspended") speechCtx.resume().catch(() => {});
  if (speechCtx && !keepAliveNode) {
    const gain = speechCtx.createGain();
    gain.gain.value = 0.00001;
    const osc = speechCtx.createOscillator();
    osc.frequency.value = 20;
    osc.connect(gain);
    gain.connect(speechCtx.destination);
    osc.start();
    keepAliveNode = osc;
  }
  if (!playback) playback = document.querySelector("#yoga-speaker") || new Audio();
  playback.muted = false;
  playback.volume = 1;
  if (window.speechSynthesis) window.speechSynthesis.resume();
}

function lastPunct(text) {
  let last = -1;
  for (const mark of ["।", "!", "?", ".", "…", "\n"]) {
    last = Math.max(last, text.lastIndexOf(mark));
  }
  return last;
}

function readySpeech(text, allowPartial = false) {
  const cleaned = (text || "").replace(/\*\*/g, "").trim();
  if (!allowPartial) return "";
  if (cleaned.length < 24) return "";
  const window = cleaned.slice(0, 120);
  const cut = lastPunct(window);
  if (cut >= 16) return cleaned.slice(0, cut + 1).trim();
  if (cleaned.length >= 70) return cleaned.slice(0, 90).trim();
  return "";
}

function leftoverSpeech(full, prefix) {
  const all = (full || "").replace(/\*\*/g, "").trim();
  const start = (prefix || "").replace(/\*\*/g, "").trim();
  if (!start) return all;
  if (all.startsWith(start)) return all.slice(start.length).trim();
  return "";
}

function splitSpeakParts(text) {
  let rest = (text || "").replace(/\*\*/g, "").trim();
  const parts = [];
  const limit = 140;
  while (rest) {
    if (rest.length <= limit) {
      parts.push(rest);
      break;
    }
    const window = rest.slice(0, limit);
    const cut = Math.max(
      window.lastIndexOf("।"),
      window.lastIndexOf("!"),
      window.lastIndexOf("?"),
      window.lastIndexOf("."),
      window.lastIndexOf("\n"),
    );
    const at = cut >= 40 ? cut + 1 : limit;
    parts.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  return parts.filter(Boolean);
}

function stopSpeech() {
  speakToken += 1;
  speakClock = 0;
  ttsQueue = [];
  ttsBusy = false;
  for (const source of speakSources) {
    try {
      source.stop();
    } catch (_) {}
  }
  speakSources = [];
  if (liveSocket) {
    try {
      liveSocket.close();
    } catch (_) {}
    liveSocket = null;
  }
  if (speakAbort) {
    speakAbort.abort();
    speakAbort = null;
  }
  if (speechSource) {
    try {
      speechSource.stop();
    } catch (_) {}
    speechSource = null;
  }
  if (playback) {
    playback.pause();
    playback.removeAttribute("src");
    playback.load();
  }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
}

function markNeedsPlay(bubble, needed) {
  const button = bubble?.querySelector(".action[title='Speak']");
  if (!button) return;
  button.classList.toggle("needs-play", Boolean(needed));
}

async function playWavBlob(blob, token) {
  if (token !== speakToken) return;
  if (speechSource) {
    try {
      speechSource.stop();
    } catch (_) {}
    speechSource = null;
  }
  if (!playback) playback = document.querySelector("#yoga-speaker") || new Audio();
  if (lastSpeakUrl) URL.revokeObjectURL(lastSpeakUrl);
  lastSpeakUrl = URL.createObjectURL(blob);
  playback.muted = false;
  playback.volume = 1;
  await new Promise((resolve, reject) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      playback.removeEventListener("ended", finish);
      playback.removeEventListener("error", onError);
      resolve();
    };
    const onError = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      playback.removeEventListener("ended", finish);
      playback.removeEventListener("error", onError);
      reject(new Error("play"));
    };
    let timer = setTimeout(finish, 180000);
    playback.addEventListener("ended", finish);
    playback.addEventListener("error", onError);
    playback.addEventListener("loadedmetadata", () => {
      const duration = playback.duration;
      if (!Number.isFinite(duration) || duration <= 0) return;
      clearTimeout(timer);
      timer = setTimeout(finish, Math.ceil(duration * 1000) + 400);
    });
    playback.src = lastSpeakUrl;
    playback.play().then(() => {
      const duration = playback.duration;
      if (Number.isFinite(duration) && duration > 0) {
        clearTimeout(timer);
        timer = setTimeout(finish, Math.ceil(duration * 1000) + 400);
      }
    }).catch(onError);
  });
}

function speakWithBrowser(spoken, language) {
  if (!window.speechSynthesis) return false;
  const lang = String(language || "en-IN");
  const prefix = lang.slice(0, 2).toLowerCase();
  heldUtterance = new SpeechSynthesisUtterance(spoken.replace(/\*\*/g, ""));
  heldUtterance.rate = 1;
  heldUtterance.lang = lang;
  const voices = window.speechSynthesis.getVoices();
  const match = voices.find((voice) => (voice.lang || "").toLowerCase().startsWith(prefix));
  if (match) heldUtterance.voice = match;
  window.speechSynthesis.cancel();
  setTimeout(() => window.speechSynthesis.speak(heldUtterance), 60);
  fetch("/api/voice/spoken", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: spoken,
      thread_id: activeId || "",
      user_id: userId(),
      language: language || languageCode(),
    }),
  }).catch(() => {});
  return true;
}

function prefetchUpcoming() {
  let armed = 0;
  for (const item of ttsQueue) {
    if (armed >= 1) break;
    prefetchItem(item);
    armed += 1;
  }
}

function prefetchItem(item) {
  if (item.blobPromise) return item.blobPromise;
  if (!speakAbort) speakAbort = new AbortController();
  item.blobPromise = fetch("/api/voice/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: speakAbort.signal,
    body: JSON.stringify({
      text: item.text,
      thread_id: activeId || "",
      user_id: userId(),
      language: item.language,
      speaker: speakerCode(),
    }),
  }).then(async (response) => {
    if (!response.ok) throw new Error("tts");
    return response.blob();
  });
  return item.blobPromise;
}

async function pumpSpeak() {
  if (ttsBusy) return;
  ttsBusy = true;
  while (ttsQueue.length) {
    const token = speakToken;
    const item = ttsQueue.shift();
    prefetchUpcoming();
    try {
      const blob = await prefetchItem(item);
      if (token !== speakToken || !blob) break;
      lastSpokenText = item.text;
      markNeedsPlay(item.bubble, false);
      await playWavBlob(blob, token);
      if (token !== speakToken) break;
      markNeedsPlay(item.bubble, false);
    } catch (error) {
      if (token !== speakToken || (error && error.name === "AbortError")) break;
      markNeedsPlay(item.bubble, true);
    }
  }
  ttsBusy = false;
  if (ttsQueue.length) pumpSpeak();
}

function mergeBytes(left, right) {
  if (!left.byteLength) return right;
  const merged = new Uint8Array(left.byteLength + right.byteLength);
  merged.set(left, 0);
  merged.set(right, left.byteLength);
  return merged;
}

function queuePcm(bytes, token) {
  if (token !== speakToken || bytes.byteLength < 2) return;
  unlockAudio();
  const even = bytes.byteLength - (bytes.byteLength % 2);
  const view = new DataView(bytes.buffer, bytes.byteOffset, even);
  const buffer = speechCtx.createBuffer(1, even / 2, 22050);
  const channel = buffer.getChannelData(0);
  for (let index = 0; index < channel.length; index += 1) {
    channel[index] = view.getInt16(index * 2, true) / 32768;
  }
  const source = speechCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(speechCtx.destination);
  const startAt = Math.max(speechCtx.currentTime + 0.03, speakClock);
  source.start(startAt);
  speakClock = startAt + buffer.duration;
  speakSources.push(source);
}

async function streamPcm(text, language, token) {
  if (!speakAbort) speakAbort = new AbortController();
  const response = await fetch("/api/voice/speak-stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: speakAbort.signal,
    body: JSON.stringify({
      text,
      thread_id: activeId || "",
      user_id: userId(),
      language,
      speaker: speakerCode(),
    }),
  });
  if (!response.ok || !response.body) throw new Error("tts");
  const reader = response.body.getReader();
  let carry = new Uint8Array(0);
  while (true) {
    const step = await reader.read();
    if (token !== speakToken) return;
    if (step.value) {
      const merged = mergeBytes(carry, step.value);
      const even = merged.byteLength - (merged.byteLength % 2);
      if (even >= 2) queuePcm(merged.subarray(0, even), token);
      carry = merged.subarray(even);
    }
    if (step.done) {
      if (carry.byteLength >= 2) queuePcm(carry.subarray(0, carry.byteLength - (carry.byteLength % 2)), token);
      return;
    }
  }
}

function speakReply(text, fromVoice, language, bubble, append = false) {
  const spoken = (text || "").trim();
  if (!spoken) return;
  if (!fromVoice && !voiceOutInput.checked) return;
  unlockAudio();
  if (!append) {
    stopSpeech();
    speakAbort = new AbortController();
  } else if (!speakAbort) {
    speakAbort = new AbortController();
  }
  const token = speakToken;
  streamPcm(spoken, language || languageCode(), token).catch((error) => {
    if (token !== speakToken || (error && error.name === "AbortError")) return;
    markNeedsPlay(bubble, true);
  });
}

function openLiveSpeech(language) {
  const token = speakToken;
  const pending = { text: "", started: false, done: false };
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/voice/live`);
  liveSocket = socket;
  socket.binaryType = "arraybuffer";
  let carry = new Uint8Array(0);
  socket.onmessage = (event) => {
    if (token !== speakToken || !(event.data instanceof ArrayBuffer)) return;
    const merged = mergeBytes(carry, new Uint8Array(event.data));
    const even = merged.byteLength - (merged.byteLength % 2);
    if (even >= 2) queuePcm(merged.subarray(0, even), token);
    carry = merged.subarray(even);
  };
  socket.onopen = () => {
    if (token !== speakToken) return;
    socket.send(JSON.stringify({
      type: "start",
      language,
      speaker: speakerCode(),
      text: pending.text,
    }));
    pending.started = true;
    if (pending.done) socket.send(JSON.stringify({ type: "end" }));
  };
  return {
    push(text) {
      pending.text = text || "";
      if (pending.started && socket.readyState === WebSocket.OPEN && token === speakToken) {
        socket.send(JSON.stringify({ type: "text", text: pending.text }));
      }
    },
    finish() {
      pending.done = true;
      if (pending.started && socket.readyState === WebSocket.OPEN && token === speakToken) {
        socket.send(JSON.stringify({ type: "end" }));
      }
    },
  };
}

function followSpeech(fromVoice, bubble) {
  const enabled = fromVoice || voiceOutInput.checked;
  let session = null;
  let pushed = 0;
  return {
    speakEarly: enabled,
    onSpeechProgress: (answer) => {
      if (!enabled || (answer || "").trim().length < 24) return;
      if (!session) session = openLiveSpeech(languageCode());
      if (answer.length - pushed < 24) return;
      pushed = answer.length;
      session.push(answer);
    },
    onReplyDone: (full) => {
      const spoken = (full || "").trim();
      if (!enabled || !spoken) return;
      if (!session) session = openLiveSpeech(languageCode());
      session.push(spoken);
      session.finish();
    },
  };
}

function setMicState(state) {
  listening = state === "listening";
  composer.classList.toggle("listening", state === "listening");
  micButton.classList.toggle("listening", state === "listening");
  micButton.classList.toggle("transcribing", state === "transcribing");
  cancelVoiceButton.hidden = state !== "listening";
  if (state === "listening") {
    micButton.setAttribute("aria-label", "Listening");
    sendButton.setAttribute("aria-label", "Stop and send voice");
    messageBox.placeholder = "Listening… Send or cancel";
  } else if (state === "transcribing") {
    micButton.setAttribute("aria-label", "Transcribing");
    sendButton.setAttribute("aria-label", "Send");
    messageBox.placeholder = "Hearing you…";
  } else {
    micButton.setAttribute("aria-label", "Start listening");
    sendButton.setAttribute("aria-label", "Send");
    messageBox.placeholder = "Speak or type…";
  }
}

function apiError(data) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]) return detail[0].msg || JSON.stringify(detail[0]);
  return (data && data.error) || "Transcription failed.";
}

function stopAndSendVoice() {
  if (!listening) return false;
  sendOnStop = true;
  stopSpeech();
  unlockAudio();
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  else setMicState("idle");
  return true;
}

function cancelVoice() {
  if (!listening) return;
  sendOnStop = false;
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  else setMicState("idle");
}

async function toggleMic() {
  stopSpeech();
  if (waitingApproval) return;
  if (busy && !listening) return;
  unlockAudio();
  if (listening) return;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (_) {
    addBubble("AI", "Microphone access is needed to speak to the agent.");
    return;
  }
  recordingChunks = [];
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";
  mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size) recordingChunks.push(event.data);
  };
  mediaRecorder.onstop = async () => {
    stream.getTracks().forEach((track) => track.stop());
    const blob = new Blob(recordingChunks, { type: mediaRecorder.mimeType || "audio/webm" });
    recordingChunks = [];
    const shouldSend = sendOnStop;
    sendOnStop = false;
    if (!shouldSend) {
      setMicState("idle");
      return;
    }
    if (blob.size < 200) {
      setMicState("idle");
      addBubble("AI", "That recording was too short. Tap the mic and speak a little longer.");
      return;
    }
    setMicState("transcribing");
    try {
      const wav = await blobToWav(blob);
      await sendRecording(wav);
    } catch (error) {
      addBubble("AI", error.message || "Could not prepare the recording.");
      setMicState("idle");
    }
  };
  sendOnStop = true;
  mediaRecorder.start(250);
  setMicState("listening");
}

function encodeWav(buffer, sampleRate = 16000) {
  const source = buffer.getChannelData(0);
  const ratio = buffer.sampleRate / sampleRate;
  const length = Math.max(1, Math.floor(source.length / ratio));
  const samples = new Int16Array(length);
  for (let i = 0; i < length; i += 1) {
    const sample = Math.max(-1, Math.min(1, source[Math.floor(i * ratio)] || 0));
    samples[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  const bytes = samples.length * 2;
  const wav = new ArrayBuffer(44 + bytes);
  const view = new DataView(wav);
  const write = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + bytes, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, bytes, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    view.setInt16(offset, samples[i], true);
    offset += 2;
  }
  return wav;
}

async function blobToWav(blob) {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    return new Blob([encodeWav(decoded)], { type: "audio/wav" });
  } finally {
    await context.close();
  }
}

async function sendRecording(blob) {
  if (!activeId) await createThread();
  busy = true;
  sendButton.disabled = true;
  micButton.disabled = true;
  const body = new FormData();
  body.append("audio", blob, "speech.wav");
  body.append("thread_id", activeId);
  body.append("user_id", userId());
  body.append("language", languageSelect.value || "unknown");
  let handedOff = false;
  try {
    const response = await fetch("/api/voice/transcribe", { method: "POST", body });
    const data = await response.json();
    if (!response.ok) throw new Error(apiError(data));
    const transcriptText = (data.transcript || "").trim();
    if (data.language) detectedLanguage = data.language;
    if (!transcriptText) {
      addBubble("AI", "I did not catch that. Try the mic again.");
      return;
    }
    handedOff = true;
    busy = false;
    setMicState("idle");
    await sendMessage(transcriptText, { fromVoice: true });
  } catch (error) {
    addBubble("AI", error.message || "Voice transcription failed.");
  } finally {
    if (!handedOff) {
      busy = false;
      sendButton.disabled = waitingApproval;
      micButton.disabled = waitingApproval;
      setMicState("idle");
    }
  }
}

function isNarrow() {
  return window.matchMedia("(max-width: 860px)").matches;
}

function setSidebarOpen(open) {
  sidebar.classList.toggle("open", open);
  appShell.classList.toggle("sidebar-closed", !open);
  scrim.hidden = !(open && isNarrow());
  menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
  menuToggle.setAttribute("aria-label", open ? "Close sidebar" : "Open sidebar");
  localStorage.setItem("yoga-sidebar-open", open ? "1" : "0");
}

function closeSidebar() {
  setSidebarOpen(false);
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  unlockAudio();
  if (stopAndSendVoice()) return;
  sendMessage(messageBox.value);
});

messageBox.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

messageBox.addEventListener("input", () => {
  messageBox.style.height = "auto";
  messageBox.style.height = `${Math.min(messageBox.scrollHeight, 140)}px`;
});

document.querySelector("#new-chat").addEventListener("click", () => createThread());
document.querySelector("#save-settings").addEventListener("click", () => {
  localStorage.setItem("yoga-user-id", userId());
  localStorage.setItem("yoga-voice-out", voiceOutInput.checked ? "1" : "0");
  localStorage.setItem("yoga-voice-language", languageSelect.value || "unknown");
  localStorage.setItem("yoga-voice-speaker", speakerCode());
  userIdInput.value = userId();
  savedNote.hidden = false;
});

function previewLanguage() {
  const chosen = languageSelect.value || "unknown";
  return chosen === "unknown" ? "en-IN" : chosen;
}

function previewLine(name) {
  const lines = {
    "en-IN": `Hello, my name is ${name}, how can I help you today.`,
    "hi-IN": `नमस्ते, मेरा नाम ${name} है, आज मैं आपकी कैसे मदद करूँ।`,
    "mr-IN": `नमस्कार, माझं नाव ${name} आहे, आज मी तुम्हाला कशी मदत करू?`,
    "te-IN": `హలో, నా పేరు ${name}, ఈ రోజు నేను మీకు ఎలా సహాయం చేయగలను.`,
    "kn-IN": `ನಮಸ್ಕಾರ, ನನ್ನ ಹೆಸರು ${name}, ಇಂದು ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ.`,
    "ml-IN": `ഹലോ, എന്റെ പേര് ${name}, ഇന്ന് ഞാൻ നിങ്ങളെ എങ്ങനെ സഹായിക്കും.`,
    "ta-IN": `வணக்கம், என் பெயர் ${name}, இன்று நான் உங்களுக்கு எப்படி உதவ முடியும்.`,
    "pa-IN": `ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਮੇਰਾ ਨਾਂ ${name} ਹੈ, ਅੱਜ ਮੈਂ ਤੁਹਾਡੀ ਕਿਵੇਂ ਮਦਦ ਕਰਾਂ.`,
    "od-IN": `ନମସ୍କାର, ମୋ ନାମ ${name}, ଆଜି ମୁଁ ଆପଣଙ୍କୁ କିପରି ସାହାଯ୍ୟ କରିପାରିବି.`,
    "bn-IN": `নমস্কার, আমার নাম ${name}, আজ আমি আপনাকে কীভাবে সাহায্য করতে পারি.`,
    "gu-IN": `નમસ્તે, મારું નામ ${name} છે, આજે હું તમને કેવી રીતે મદદ કરી શકું.`,
  };
  const lang = previewLanguage();
  return { lang, text: lines[lang] || lines["en-IN"] };
}

function warmPreviews() {
  fetch("/api/voice/warm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language: previewLanguage(), speaker: speakerCode() }),
  }).catch(() => {});
}

function previewSelectedVoice() {
  unlockAudio();
  const option = voiceSpeakerSelect.selectedOptions[0];
  const name = (option && option.textContent.trim()) || "Arya";
  const sample = previewLine(name);
  localStorage.setItem("yoga-voice-speaker", speakerCode());
  speakReply(sample.text, true, sample.lang);
}

voiceSpeakerSelect.addEventListener("pointerdown", () => {
  unlockAudio();
  warmPreviews();
});
voiceSpeakerSelect.addEventListener("change", () => previewSelectedVoice());
languageSelect.addEventListener("change", () => warmPreviews());
warmPreviews();

micButton.addEventListener("click", () => toggleMic());
cancelVoiceButton.addEventListener("click", () => cancelVoice());

document.querySelectorAll(".chips button").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.prompt));
});

function isSidebarOpen() {
  return isNarrow() ? sidebar.classList.contains("open") : !appShell.classList.contains("sidebar-closed");
}

menuToggle.addEventListener("click", () => {
  setSidebarOpen(!isSidebarOpen());
});
sidebarClose.addEventListener("click", closeSidebar);
scrim.addEventListener("click", closeSidebar);
window.addEventListener("resize", () => {
  if (!isNarrow()) scrim.hidden = true;
  else scrim.hidden = !sidebar.classList.contains("open");
});

setSidebarOpen(localStorage.getItem("yoga-sidebar-open") !== "0");
refreshThreads();
