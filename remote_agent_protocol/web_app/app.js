const state = {
  latest: 0,
  status: null,
  messages: [],
  memories: { short: [], semantic: [] },
  attachments: [],
  voiceDraft: "",
  activeConfirm: null,
  sending: false,
};

const $ = (id) => document.getElementById(id);

function fmtSeconds(value) {
  return typeof value === "number" ? `${value.toFixed(2)}s` : "--";
}

function setPillTone(id, tone) {
  const pill = $(id)?.closest(".status-pill");
  if (!pill) return;
  pill.classList.remove("status-success", "status-warning", "status-error");
  if (tone) pill.classList.add(tone);
}

function healthTone(health) {
  if (health?.ok) return "status-success";
  return /check|start/i.test(health?.label || "") ? "status-warning" : "status-error";
}

async function post(action, payload = {}) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  });
  const data = await response.json();
  if (data.status) {
    state.status = data.status;
    renderStatus();
  }
  return data;
}

async function poll() {
  try {
    const response = await fetch(`/api/events?after=${state.latest}`);
    const data = await response.json();
    state.latest = data.latest;
    state.status = data.status;
    data.events.forEach(handleEvent);
    renderStatus();
  } catch (error) {
    addMessage("sys", "System", `UI connection lost: ${error.message}`);
  } finally {
    setTimeout(poll, 450);
  }
}

function handleEvent(event) {
  if (event.type === "transcript") {
    addMessage(event.role || "assistant", event.role === "user" ? "You" : currentPersona(), event.text || "");
  } else if (event.type === "draft_voice") {
    state.voiceDraft = event.text || "";
    $("voiceDraftLabel").textContent = state.voiceDraft ? `Voice draft: ${state.voiceDraft}` : "Voice transcript appears here when context is held.";
    if (event.intent === "send") sendMessage();
  } else if (event.type === "sys") {
    addMessage("sys", "System", event.text || "");
  } else if (event.type === "agent_job") {
    renderAgentEvent(event);
  } else if (event.type === "memory") {
    state.memories[event.scope === "semantic" ? "semantic" : "short"] = event.rows || [];
    renderMemory();
  } else if (event.type === "agent_confirm") {
    state.activeConfirm = event;
    renderConfirm();
  } else if (event.type === "agent_confirm_resolved") {
    if (state.activeConfirm?.token === event.token) state.activeConfirm = null;
    renderConfirm();
  } else if (event.type === "session") {
    $("chatState").textContent = event.state || "session";
  } else if (event.type === "speaking") {
    $("chatState").textContent = event.value ? "speaking" : "listening";
  }
}

function currentPersona() {
  return state.status?.persona || "Assistant";
}

function addMessage(role, name, text) {
  if (!text) return;
  state.messages.push({ role, name, text });
  renderChat();
}

function renderChat() {
  const log = $("chatLog");
  if (!state.messages.length) return;
  log.innerHTML = "";
  state.messages.slice(-80).forEach((message) => {
    const row = document.createElement("article");
    row.className = `message ${message.role}`;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = message.name;
    const body = document.createElement("div");
    body.textContent = message.text;
    row.append(name, body);
    log.appendChild(row);
  });
  log.scrollTop = log.scrollHeight;
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  $("appTitle").textContent = s.appName;
  $("personaName").textContent = s.persona;
  $("personaBlurb").textContent = s.personaBlurb || "";
  $("heroPersona").textContent = s.persona;
  $("heroBlurb").textContent = s.personaBlurb || s.subtitle;
  $("sessionPill").textContent = s.session;
  $("ollamaPill").textContent = s.health?.label || "checking";
  $("ttsPill").textContent = s.ttsHealth?.label || "checking";
  setPillTone("sessionPill", s.session === "ready" ? "status-success" : (["failed", "stopped"].includes(s.session) ? "status-error" : "status-warning"));
  setPillTone("ollamaPill", healthTone(s.health));
  setPillTone("ttsPill", healthTone(s.ttsHealth));
  const activeAgents = s.activeAgentCount || 0;
  $("agentsPill").textContent = activeAgents ? `${activeAgents} active` : "idle";
  $("agentsPill").closest(".status-pill").classList.toggle("busy", activeAgents > 0);
  $("muteBtn").textContent = s.muted ? "Mic muted" : "Mic live";
  $("muteBtn").classList.toggle("status-error", s.muted);
  $("muteBtn").classList.toggle("status-success", !s.muted);
  $("modeBtn").textContent = labelMode(s.voiceMode);
  $("modeCard").textContent = labelMode(s.voiceMode);
  $("modelCard").textContent = s.model || "--";
  $("serverCard").textContent = s.health?.label || "checking";
  $("memoryCard").textContent = s.semanticMemoryEnabled ? "Short + semantic" : "Short term";
  $("latStt").textContent = fmtSeconds(s.latency?.stt);
  $("latLlm").textContent = fmtSeconds(s.latency?.llm);
  $("latTts").textContent = fmtSeconds(s.latency?.tts);
  $("latTotal").textContent = fmtSeconds(s.latency?.total);
  $("settingsPersona").textContent = s.persona;
  $("settingsVoice").textContent = s.voice;
  $("settingsAgent").textContent = s.toolUser;
  syncSelects(s);
  renderAgents(s);
  renderStatusDashboard(s);
  if (!state.activeConfirm && s.pendingConfirms?.length) {
    state.activeConfirm = s.pendingConfirms[0];
    renderConfirm();
  }
}

function syncSelects(s) {
  fillSelect($("personaSelect"), s.personas.map((p) => [p.name, p.name]), s.persona);
  fillSelect($("toolSelect"), s.agentBackends.map((b) => [b, b]), s.toolUser);
  fillSelect($("modelSelect"), s.models.map((m) => [m, m]), s.model);
  fillSelect($("voiceSelect"), s.voices.map((v) => [v.value, v.label]), s.voice);
}

function fillSelect(select, rows, value) {
  if (select.dataset.loaded === JSON.stringify(rows) && select.value === value) return;
  const marker = JSON.stringify(rows);
  if (select.dataset.loaded !== marker) {
    select.innerHTML = "";
    rows.forEach(([val, label]) => {
      const option = document.createElement("option");
      option.value = val;
      option.textContent = label;
      select.appendChild(option);
    });
    select.dataset.loaded = marker;
  }
  select.value = value || rows[0]?.[0] || "";
}

function labelMode(mode) {
  return { wake_word: "Wake Word", free_talk: "Free Talk", push_to_talk: "Push To Talk" }[mode] || "Free Talk";
}

function nextMode(mode) {
  const modes = ["wake_word", "free_talk", "push_to_talk"];
  return modes[(modes.indexOf(mode) + 1) % modes.length] || "free_talk";
}

function renderAgents(s) {
  const strip = $("agentStrip");
  strip.innerHTML = "";
  s.agentBackends.forEach((backend) => {
    const job = s.agentStates?.[backend];
    const active = ["running", "waiting", "blocked"].includes(job?.status);
    const detail = active ? (job.action || job.state || job.status) : "idle";
    const chip = document.createElement("article");
    chip.className = `agent-chip${active ? " active" : ""}`;
    chip.innerHTML = `<strong>${backend}</strong><span>${s.agentMachines[backend] || "local"} / ${escapeHtml(detail)}</span>`;
    strip.appendChild(chip);
  });
}

function renderAgentEvent(event) {
  const label = event.action || event.state || event.event || "agent update";
  if (event.event === "progress") addMessage("agent", event.agent || "Agent", label);
  if (event.event === "finished") addMessage("agent", event.agent || "Agent", event.result || "Finished.");
}

function renderConfirm() {
  const bar = $("confirmBar");
  if (!state.activeConfirm) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  $("confirmTitle").textContent = `${state.activeConfirm.agent} on ${state.activeConfirm.machine || "local"}`;
  $("confirmText").textContent = `${state.activeConfirm.task || ""} ${state.activeConfirm.reason || ""}`.trim();
}

function renderStatusDashboard(s) {
  const rows = [
    ["Session", s.session, s.session === "ready" ? "success" : "warning"],
    ["Ollama", s.health?.label || "checking", s.health?.ok ? "success" : "error"],
    ["TTS", s.ttsHealth?.label || "checking", s.ttsHealth?.ok ? "success" : "error"],
    ["Voice mode", labelMode(s.voiceMode), "info"],
    ["Memory", s.semanticMemoryEnabled ? "Semantic enabled" : "Short term only", s.memoryEnabled ? "success" : "warning"],
    ["Default agent", s.toolUser, "info"],
  ];
  $("statusDashboard").innerHTML = rows.map(([label, value, tone]) => (
    `<article class="status-row-card"><span class="status-icon">${label.slice(0, 2).toUpperCase()}</span><div><strong>${label}</strong><p class="muted">${value}</p></div><b class="${tone}">${tone}</b></article>`
  )).join("");
}

function renderMemory() {
  const rows = currentMemoryRows();
  $("diaryCount").textContent = state.memories.short.length;
  $("nodeCount").textContent = state.memories.semantic.length;
  const list = $("memoryList");
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = '<div class="empty-state"><strong>No memories loaded.</strong><span>Refresh or search to load memory rows.</span></div>';
    return;
  }
  rows.forEach((row, index) => {
    const text = row.text || row.content || row.summary || JSON.stringify(row);
    const card = document.createElement("article");
    card.className = "memory-card";
    card.innerHTML = `<h4>${row.role || row.id || `Memory ${index + 1}`}</h4><p>${escapeHtml(text)}</p><div class="tag-row"><span class="tag">${row.score ?? "local"}</span></div>`;
    card.addEventListener("click", () => {
      $("memoryDetailTitle").textContent = row.role || row.id || `Memory ${index + 1}`;
      $("memoryDetailText").textContent = text;
    });
    list.appendChild(card);
  });
}

function currentMemoryRows() {
  const active = document.querySelector(".tab.active")?.dataset.memoryTab;
  return active === "knowledge" ? state.memories.semantic : state.memories.short;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function sendPayload() {
  return {
    prompt: $("messageInput").value,
    notes: $("notesInput").value,
    voiceDraft: state.voiceDraft,
    attachments: state.attachments,
  };
}

function clearComposer() {
  $("messageInput").value = "";
  $("notesInput").value = "";
  state.voiceDraft = "";
  $("voiceDraftLabel").textContent = "Voice transcript appears here when context is held.";
  state.attachments = [];
  renderAttachments();
  post("context_active", { active: false });
}

async function sendMessage() {
  if (state.sending) return;
  const payload = sendPayload();
  if (!payload.prompt.trim() && !payload.notes.trim() && !payload.voiceDraft.trim() && !payload.attachments.length) return;
  state.sending = true;
  $("sendBtn").disabled = true;
  try {
    await post("send", payload);
    clearComposer();
  } finally {
    state.sending = false;
    $("sendBtn").disabled = false;
  }
}

function delegateMessage() {
  const payload = sendPayload();
  if (!payload.prompt.trim() && !payload.notes.trim() && !payload.voiceDraft.trim() && !payload.attachments.length) return;
  addMessage("user", `You -> ${state.status?.toolUser || "agent"}`, payload.prompt || "Shared context");
  post("delegate", payload);
  clearComposer();
}

function renderAttachments() {
  const list = $("attachmentList");
  list.innerHTML = "";
  state.attachments.forEach((item, index) => {
    const pill = document.createElement("span");
    pill.className = "attachment-pill";
    pill.innerHTML = `<span>${escapeHtml(item.reference)}</span><button type="button" aria-label="Remove attachment">x</button>`;
    pill.querySelector("button").addEventListener("click", () => {
      state.attachments.splice(index, 1);
      renderAttachments();
      post("context_active", { active: hasContextDraft() });
    });
    list.appendChild(pill);
  });
}

function hasContextDraft() {
  return Boolean($("messageInput").value.trim() || $("notesInput").value.trim() || state.attachments.length);
}

function bind() {
  document.querySelectorAll(".nav-link").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      $(`${button.dataset.view}View`).classList.add("active");
      if (button.dataset.view === "memory") post("refresh_memory", { query: $("memorySearch").value });
    });
  });
  $("muteBtn").addEventListener("click", () => post("mute", { muted: !state.status?.muted }));
  $("modeBtn").addEventListener("click", () => post("voice_mode", { mode: nextMode(state.status?.voiceMode) }));
  $("pttBtn").addEventListener("pointerdown", () => { $("pttBtn").classList.add("active"); post("ptt", { active: true }); });
  $("pttBtn").addEventListener("pointerup", () => { $("pttBtn").classList.remove("active"); post("ptt", { active: false }); });
  $("personaSelect").addEventListener("change", (event) => post("persona", { name: event.target.value }));
  $("toolSelect").addEventListener("change", (event) => post("tool_user", { backend: event.target.value }));
  $("modelSelect").addEventListener("change", (event) => post("model", { model: event.target.value }));
  $("voiceSelect").addEventListener("change", (event) => post("voice", { voice: event.target.value }));
  $("sendBtn").addEventListener("click", sendMessage);
  $("delegateBtn").addEventListener("click", delegateMessage);
  $("messageInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  ["messageInput", "notesInput"].forEach((id) => $(id).addEventListener("input", () => post("context_active", { active: hasContextDraft() })));
  $("contextToggle").addEventListener("click", () => $("contextDrawer").classList.toggle("hidden"));
  $("addAttachmentBtn").addEventListener("click", () => {
    const reference = $("attachmentRef").value.trim();
    if (!reference) return;
    state.attachments.push({ reference, note: $("attachmentNote").value.trim() });
    $("attachmentRef").value = "";
    $("attachmentNote").value = "";
    renderAttachments();
    post("context_active", { active: true });
  });
  $("approveBtn").addEventListener("click", () => {
    if (state.activeConfirm) post("approve", { token: state.activeConfirm.token });
    state.activeConfirm = null;
    renderConfirm();
  });
  $("denyBtn").addEventListener("click", () => {
    if (state.activeConfirm) post("deny", { token: state.activeConfirm.token });
    state.activeConfirm = null;
    renderConfirm();
  });
  $("restartChatBtn").addEventListener("click", () => { state.messages = []; renderChat(); post("restart_chat"); });
  $("freeVramBtn").addEventListener("click", () => post("free_vram"));
  $("startOllamaBtn").addEventListener("click", () => post("start_ollama"));
  $("diagnosticsBtn").addEventListener("click", () => post("export_diagnostics"));
  $("rebootBtn").addEventListener("click", () => post("reboot_session"));
  $("memorySearch").addEventListener("input", (event) => post("refresh_memory", { query: event.target.value }));
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      renderMemory();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key.toLowerCase() === "l") $("messageInput").focus();
    if (event.ctrlKey && event.key.toLowerCase() === "m") post("mute", { muted: !state.status?.muted });
  });
}

bind();
poll();
