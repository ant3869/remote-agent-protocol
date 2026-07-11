const state = {
  latest: 0,
  status: null,
  messages: [],
  memories: { short: [], semantic: [] },
  attachments: [],
  voiceDraft: "",
  activeConfirm: null,
  selectedPersona: null,
  personaOriginal: null,
  connectionLost: false,
  avatar: { speaking: false, userSpeaking: false, completedAt: 0, failedAt: 0, latestAssistantText: "", lastActivityAt: Date.now() },
  wake: null,
  agentJobs: {},
  agentHistory: [],
  agentPrompts: null,
  confirmHistory: [],
  selectedAgentJobId: null,
  selectedMemory: null,
  sending: false,
  paletteIndex: 0,
};

const $ = (id) => document.getElementById(id);
const ACTIVE_AGENT_STATUSES = new Set(["running", "waiting", "blocked"]);

function fmtSeconds(value) {
  return typeof value === "number" ? `${value.toFixed(2)}s` : "--";
}

function fmtClock(value) {
  const timestamp = Date.parse(value || "");
  return Number.isNaN(timestamp) ? "" : new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function compactText(value, fallback = "") {
  const text = String(value || fallback).replace(/\s+/g, " ").trim();
  return text.length > 150 ? `${text.slice(0, 147)}...` : text;
}

function agentIsActive(job) {
  return ACTIVE_AGENT_STATUSES.has(job?.status);
}

function agentStatusTone(status) {
  if (status === "done") return "status-success";
  if (["failed", "cancelled", "timeout"].includes(status)) return "status-error";
  if (ACTIVE_AGENT_STATUSES.has(status)) return "status-warning";
  return "";
}

function describeAgentMove(event) {
  if (event.event === "started") return compactText(event.action || event.task, "Started task");
  if (event.event === "output") return compactText(event.line, "Received output");
  if (event.event === "finished") return compactText(event.result || event.summary, "Finished");
  return compactText(event.action || event.last_completed_step || event.state || event.event, "Updated status");
}

function moveFromEvent(event) {
  const text = describeAgentMove(event);
  if (!text) return null;
  return {
    at: event.finished_at || event.started_at || new Date().toISOString(),
    event: event.event || "update",
    status: event.status || "",
    text,
    tool: event.tool || "",
    step: event.step || "",
    step_total: event.step_total || "",
  };
}

function fallbackAgentMoves(job) {
  const moves = [];
  if (job.started_at) moves.push({ at: job.started_at, event: "started", status: job.status, text: "Started task" });
  if (job.action || job.tool || job.step) {
    const step = job.step ? `Step ${job.step}${job.step_total ? `/${job.step_total}` : ""}` : "";
    moves.push({
      at: job.finished_at || job.started_at || "",
      event: agentIsActive(job) ? "progress" : "status",
      status: job.status,
      text: compactText([step, job.tool, job.action].filter(Boolean).join(" · "), job.state || "Updated status"),
      tool: job.tool || "",
      step: job.step || "",
      step_total: job.step_total || "",
    });
  }
  if (job.last_completed_step) {
    moves.push({ at: job.finished_at || job.started_at || "", event: "completed", status: job.status, text: compactText(job.last_completed_step) });
  }
  if (job.finished_at || job.result || job.summary) {
    moves.push({ at: job.finished_at || "", event: "finished", status: job.status, text: compactText(job.result || job.summary, "Finished") });
  }
  return moves;
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

function vramTone(percent) {
  if (percent >= 95) return "status-error";
  if (percent >= 85) return "status-warning";
  return "status-success";
}

function wakeLabel(phase) {
  return {
    idle: "Wake word idle",
    loading_detector: "Loading detector",
    waiting_for_wake_word: "Listening for wake word",
    wake_word_detected: "Wake word detected",
    listening_for_command: "Listening for command",
    transcribing: "Transcribing",
    agent_responding: "Agent responding",
    follow_up_window: "Follow-up window",
    returning_to_passive: "Returning to wake-word mode",
    error: "Wake word error",
  }[phase] || "Wake word idle";
}

async function post(action, payload = {}) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-Token": window.__CSRF_TOKEN__ || "",
    },
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
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (state.connectionLost) {
      addMessage("sys", "System", "UI connection restored.");
      state.connectionLost = false;
    }
    state.latest = data.latest;
    state.status = data.status;
    data.events.forEach(handleEvent);
    renderStatus();
  } catch (error) {
    if (!state.connectionLost) {
      state.connectionLost = true;
      addMessage("sys", "System", `UI connection lost: ${error.message}`);
    }
  } finally {
    setTimeout(poll, state.connectionLost ? 2000 : 450);
  }
}

function handleEvent(event) {
  if (["transcript", "draft_voice", "turn", "speaking", "wake", "agent_job", "agent_confirm"].includes(event.type)) {
    state.avatar.lastActivityAt = Date.now();
  }
  if (event.type === "transcript" && event.role !== "user") state.avatar.latestAssistantText = event.text || "";
  if (event.type === "speaking") state.avatar.speaking = Boolean(event.value);
  if (event.type === "turn" && event.event === "user_started") state.avatar.userSpeaking = true;
  if (event.type === "turn" && event.event === "user_stopped") state.avatar.userSpeaking = false;
  if (event.type === "agent_job" && event.event === "finished" && event.status === "done") state.avatar.completedAt = Date.now();
  if (event.type === "agent_job" && ["failed", "timeout", "cancelled"].includes(event.status)) state.avatar.failedAt = Date.now();

  if (event.type === "transcript") {
    addMessage(event.role || "assistant", event.role === "user" ? "You" : currentPersona(), event.text || "");
  } else if (event.type === "draft_voice") {
    state.voiceDraft = event.text || "";
    $("voiceDraftLabel").textContent = state.voiceDraft ? `Voice draft: ${state.voiceDraft}` : "Voice transcript appears here when context is held.";
    if (event.intent === "send") sendMessage();
  } else if (event.type === "sys") {
    addMessage("sys", "System", event.text || "");
  } else if (event.type === "agent_job") {
    storeAgentEvent(event);
    renderAgentEvent(event);
  } else if (event.type === "memory") {
    state.memories[event.scope === "semantic" ? "semantic" : "short"] = (event.rows || []).map((row) => normalizeMemoryRow(row, event.scope));
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
    updateWakePhase(event.value ? "agent_responding" : "follow_up_window");
  } else if (event.type === "turn") {
    if (event.event === "user_started") updateWakePhase("listening_for_command");
    if (event.event === "user_stopped") updateWakePhase("transcribing");
  } else if (event.type === "wake") {
    state.wake = { ...(state.wake || {}), ...event, seen_at: Date.now() };
    if (event.remaining_secs || event.window_secs) {
      state.wake.expires_at = Date.now() + 1000 * (event.remaining_secs || event.window_secs);
    }
    if (event.phase === "wake_word_detected") playWakeChime();
    renderWakeStatus();
  }
  syncAvatarRuntime();
}

function updateWakePhase(phase) {
  if (state.status?.voiceMode !== "wake_word" || !state.wake) return;
  state.wake.phase = phase;
  if (phase === "follow_up_window") {
    state.wake.expires_at = Date.now() + 1000 * (state.wake.window_secs || 3);
  }
  renderWakeStatus();
}

function playWakeChime() {
  if (state.status?.muted || !window.AudioContext) return;
  const context = new AudioContext();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = "sine";
  oscillator.frequency.value = 660;
  gain.gain.setValueAtTime(0.0001, context.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.035, context.currentTime + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.18);
  oscillator.connect(gain).connect(context.destination);
  oscillator.start();
  oscillator.stop(context.currentTime + 0.2);
}

function currentPersona() {
  return state.status?.persona || "Assistant";
}

function avatarRuntimeSnapshot() {
  const s = state.status || {};
  const wake = currentWake();
  return {
    persona: s.persona,
    session: s.session,
    speaking: state.avatar.speaking,
    userSpeaking: state.avatar.userSpeaking,
    thinking: wake.phase === "transcribing" || wake.phase === "agent_responding",
    wakePhase: wake.phase,
    activeAgentCount: s.activeAgentCount || 0,
    pendingConfirmation: Boolean(state.activeConfirm || s.pendingConfirms?.length),
    completedAt: state.avatar.completedAt,
    sleeping: Date.now() - state.avatar.lastActivityAt > 120_000
      && !state.avatar.speaking
      && !state.avatar.userSpeaking
      && (s.activeAgentCount || 0) === 0,
    error: s.session === "failed" || Boolean(state.avatar.failedAt && Date.now() - state.avatar.failedAt < 5000),
    latestAssistantText: state.avatar.latestAssistantText,
  };
}

function syncAvatarRuntime() {
  const api = window.remoteAgentAvatar;
  if (!api || !state.status?.avatar) return;
  api.updateSettings(state.status.avatar);
  api.updateRuntime(avatarRuntimeSnapshot());
}

function populateAvatarSettings(avatar = {}) {
  if (!$('avatarSettingEnabled')) return;
  $('avatarSettingEnabled').value = String(avatar.enabled ?? true);
  $('avatarSettingAvatar').value = avatar.avatarId || 'butler';
  $('avatarSettingQuality').value = avatar.quality || 'high';
  $('avatarSettingLipSync').value = String(avatar.lipSync ?? true);
  $('avatarSettingGaze').value = String(avatar.gaze ?? true);
  $('avatarSettingIdle').value = String(avatar.idleMotion ?? true);
  $('avatarSettingIntensity').value = String(avatar.expressionIntensity ?? 0.62);
  $('avatarSettingShowState').value = String(avatar.showState ?? true);
  $('avatarSettingCollapsed').value = String(avatar.panelCollapsed ?? false);
  $('avatarSettingMotion').value = avatar.reducedMotion === null || avatar.reducedMotion === undefined
    ? 'system'
    : avatar.reducedMotion ? 'reduced' : 'normal';
}

function avatarSettingsPayload(overrides = {}) {
  const motion = $('avatarSettingMotion').value;
  return {
    enabled: $('avatarSettingEnabled').value === 'true',
    avatarId: $('avatarSettingAvatar').value,
    quality: $('avatarSettingQuality').value,
    lipSync: $('avatarSettingLipSync').value === 'true',
    gaze: $('avatarSettingGaze').value === 'true',
    idleMotion: $('avatarSettingIdle').value === 'true',
    expressionIntensity: Number($('avatarSettingIntensity').value),
    reducedMotion: motion === 'system' ? null : motion === 'reduced',
    showState: $('avatarSettingShowState').value === 'true',
    panelCollapsed: $('avatarSettingCollapsed').value === 'true',
    ...overrides,
  };
}

async function saveAvatarSettings(overrides = {}) {
  const result = await post('avatar_settings', { settings: avatarSettingsPayload(overrides) });
  const notice = $('avatarSettingsNotice');
  notice.textContent = result.ok ? 'Avatar settings saved.' : result.error;
  notice.classList.remove('hidden');
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
  $("vramPillRow").hidden = !s.vram?.available;
  if (s.vram?.available) {
    $("vramPill").textContent = s.vram.label;
    setPillTone("vramPill", vramTone(s.vram.percent));
  }
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
  $("settingsTtsProvider").textContent = s.tts?.provider || "--";
  $("settingsCoquiStatus").textContent = s.tts?.coqui?.label || s.tts?.coqui?.error || "--";
  $("settingsCoquiDevice").textContent = s.tts?.coqui?.device || "--";
  $("setupCoquiStatus").textContent = s.tts?.coqui?.available ? "Installed" : "Missing";
  $("setupCoquiStatus").className = s.tts?.coqui?.available ? "status-success" : "status-error";
  $("setupCoquiModel").textContent = s.tts?.coqui?.selectedModel || "--";
  $("settingsModel").textContent = s.model || "--";
  $("settingsAgent").textContent = s.toolUser;
  $("settingsMode").textContent = labelMode(s.voiceMode);
  state.wake = state.wake || s.wake;
  renderWakeStatus();
  syncSelects(s);
  renderPersonaPage(s);
  renderAgents(s);
  renderAgentsPage();
  renderStatusDashboard(s);
  populateAvatarSettings(s.avatar);
  syncAvatarRuntime();
  if (!state.activeConfirm && s.pendingConfirms?.length) {
    state.activeConfirm = s.pendingConfirms[0];
    renderConfirm();
  }
}

function currentWake() {
  return { ...(state.status?.wake || {}), ...(state.wake || {}) };
}

function wakeRemaining(wake) {
  if (!wake?.expires_at) return wake?.remaining_secs || 0;
  return Math.max(0, (wake.expires_at - Date.now()) / 1000);
}

function renderWakeStatus() {
  const wake = currentWake();
  if (!wake || !$("settingsWake")) return;
  const remaining = wakeRemaining(wake);
  let phase = state.status?.voiceMode === "wake_word" ? wake.phase : "idle";
  if (phase === "follow_up_window" && remaining <= 0) phase = "returning_to_passive";
  const model = wake.model || "none";
  const path = wake.model_path || "built-in/cache";
  $("settingsWake").textContent = state.status?.voiceMode === "wake_word" ? wakeLabel(phase) : "Disabled";
  $("settingsWakeWord").textContent = wake.persona || model;
  $("settingsWakeModel").textContent = model;
  $("settingsWakeFile").textContent = path;
  $("settingsWakeDetector").textContent = wake.detector_loaded ? "Loaded" : (wake.error || "Not loaded");
  $("settingsWakePassive").textContent = wake.passive ? "Passive listening" : "Responsive";
  $("settingsWakeCountdown").textContent = remaining > 0 ? `${remaining.toFixed(1)}s` : "--";
  $("chatState").classList.toggle("accent-agent-label", phase === "wake_word_detected" || phase === "follow_up_window");
}

function coquiModelRows() {
  const models = state.status?.tts?.coqui?.models || [];
  return models.map((m) => [m.id, `${m.id}${m.installed ? " · installed" : ""}`]);
}

function coquiSpeakerRows() {
  const speakers = state.status?.tts?.coqui?.speakers || [];
  return [["", speakers.length ? "Auto speaker" : "No speaker list loaded"], ...speakers.map((v) => [v, v])];
}

function coquiLanguageRows() {
  const languages = state.status?.tts?.coqui?.languages || [];
  return [["", languages.length ? "Auto language" : "No language list loaded"], ...languages.map((v) => [v, v])];
}

function coquiDeviceRows() {
  return ["cpu", "cuda", "mps"].map((v) => [v, v.toUpperCase()]);
}

function syncSelects(s) {
  fillSelect($("personaSelect"), s.personas.map((p) => [p.name, p.name]), s.persona);
  fillSelect($("toolSelect"), s.agentBackends.map((b) => [b, b]), s.toolUser);
  fillSelect($("modelSelect"), s.models.map((m) => [m, m]), s.model);
  fillSelect($("voiceSelect"), s.voices.map((v) => [v.value, v.label]), s.voice);
  fillSelect($("settingsPersonaSelect"), s.personas.map((p) => [p.name, p.name]), s.persona);
  fillSelect($("settingsToolSelect"), s.agentBackends.map((b) => [b, b]), s.toolUser);
  fillSelect($("settingsVoiceModeSelect"), voiceModeRows(), s.voiceMode);
  fillSelect($("settingsModelSelect"), s.models.map((m) => [m, m]), s.model);
  fillSelect($("settingsVoiceSelect"), s.voices.map((v) => [v.value, v.label]), s.voice);
  fillSelect($("settingsTtsProviderSelect"), (s.tts?.providers || []).map((p) => [p.id, p.label]), s.tts?.provider || "kokoro");
  fillSelect($("settingsCoquiModelSelect"), coquiModelRows(), s.tts?.coqui?.selectedModel || "");
  fillSelect($("settingsCoquiSpeakerSelect"), coquiSpeakerRows(), s.tts?.coqui?.speaker || "");
  fillSelect($("settingsCoquiLanguageSelect"), coquiLanguageRows(), s.tts?.coqui?.language || "");
  fillSelect($("settingsCoquiDeviceSelect"), coquiDeviceRows(), s.tts?.coqui?.device || "cpu");
}

function selectedPersonaRecord() {
  const personas = state.status?.personas || [];
  const selected = state.selectedPersona || state.status?.persona;
  return personas.find((persona) => persona.name === selected) || personas[0];
}

function renderPersonaPage(s) {
  if (!s.personas?.length) return;
  if (!state.selectedPersona || !s.personas.some((p) => p.name === state.selectedPersona)) {
    state.selectedPersona = s.persona;
  }
  renderPersonaList(s);
  loadPersonaEditor(selectedPersonaRecord(), { keepDirty: document.activeElement?.closest("#personasView") });
}

function renderPersonaList(s) {
  const query = ($("personaSearch")?.value || "").trim().toLowerCase();
  const list = $("personaList");
  if (!list) return;
  list.innerHTML = "";
  s.personas
    .filter((persona) => !query || `${persona.name} ${persona.description}`.toLowerCase().includes(query))
    .forEach((persona) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `persona-row${persona.name === state.selectedPersona ? " active" : ""}`;
      button.innerHTML = `<strong>${escapeHtml(persona.name)}</strong><span>${escapeHtml(persona.description || persona.effectiveModel || "")}</span>${persona.name === s.persona ? '<b>Active</b>' : ""}`;
      button.addEventListener("click", async () => {
        state.selectedPersona = persona.name;
        state.personaOriginal = null;
        loadPersonaEditor(persona);
        await post("persona", { name: persona.name });
      });
      list.appendChild(button);
    });
  if (!list.children.length) {
    list.innerHTML = '<div class="empty-state"><strong>No matching personas.</strong><span>Clear search to show the full list.</span></div>';
  }
}

function loadPersonaEditor(persona, options = {}) {
  if (!persona || !$("personaEditName")) return;
  if (options.keepDirty && state.personaOriginal === persona.name) return;
  state.personaOriginal = persona.name;
  $("personaEditName").value = persona.name;
  $("personaEditName").disabled = Boolean(persona.builtin);
  $("personaEditDescription").value = persona.description || "";
  $("personaEditPrompt").value = persona.systemPrompt || "";
  $("personaTone").value = persona.toneStyle || "";
  fillSelect($("personaEditModel"), [["", "App default"], ...(state.status?.models || []).map((m) => [m, m])], persona.model || "");
  fillSelect($("personaEditVoice"), (state.status?.voices || []).map((v) => [v.value, v.label]), persona.voice);
  fillSelect($("personaEditVoiceBackend"), (state.status?.tts?.providers || []).map((p) => [p.id, p.label]), persona.voiceBackend || "kokoro");
  fillSelect($("personaEditVoiceModel"), [["", "Provider default"], ...coquiModelRows()], persona.voiceModel || "");
  fillSelect($("personaEditCoquiSpeaker"), coquiSpeakerRows(), persona.ttsOptions?.speaker || "");
  fillSelect($("personaEditCoquiLanguage"), coquiLanguageRows(), persona.ttsOptions?.language || "");
  fillSelect($("personaEditCoquiDevice"), coquiDeviceRows(), persona.ttsOptions?.device || "cpu");
  fillSelect($("personaEditToolUser"), [["", "App default"], ...(state.status?.agentBackends || []).map((b) => [b, b])], persona.toolUser || "");
  $("personaMemoryMode").textContent = persona.memory?.mode || "--";
  $("personaCanWrite").textContent = persona.memory?.canWrite ? "Yes" : "No";
  $("personaCanRetrieve").textContent = persona.memory?.canRetrieve ? "Yes" : "No";
  $("personaContextHandling").textContent = persona.advanced?.contextHandling || "--";
  $("personaRouting").textContent = persona.routing?.defaultAgent || "App default";
  $("personaVoiceDefault").textContent = labelMode(persona.voiceDefaults?.mode);
  $("personaTools").textContent = (state.status?.agentBackends || []).join(", ") || "None";
  $("personaSafety").textContent = persona.advanced?.safety || "--";
  $("personaResponseFormat").textContent = persona.advanced?.responseFormat || "--";
  renderPersonaPreview();
}

function personaFormPayload() {
  return {
    originalName: state.personaOriginal,
    name: $("personaEditName").value.trim(),
    description: $("personaEditDescription").value.trim(),
    systemPrompt: $("personaEditPrompt").value.trim(),
    model: $("personaEditModel").value,
    voice: $("personaEditVoice").value,
    voiceBackend: $("personaEditVoiceBackend").value,
    voiceModel: $("personaEditVoiceModel").value,
    coquiSpeaker: $("personaEditCoquiSpeaker").value,
    coquiLanguage: $("personaEditCoquiLanguage").value,
    coquiDevice: $("personaEditCoquiDevice").value,
    toolUser: $("personaEditToolUser").value,
  };
}

function renderPersonaPreview() {
  const payload = personaFormPayload();
  $("personaPreviewName").textContent = payload.name || "--";
  $("personaPreviewDescription").textContent = payload.description || "No description set.";
  $("personaPreviewModel").textContent = payload.model || state.status?.model || "App default";
  $("personaPreviewVoice").textContent = payload.voiceBackend === "coqui"
    ? `Coqui · ${payload.coquiSpeaker || "auto"}`
    : (payload.voice || "--");
  $("personaPreviewTool").textContent = payload.toolUser || state.status?.toolUser || "App default";
  $("personaPreviewMemory").textContent = $("personaMemoryMode").textContent;
  const warnings = [];
  if (!payload.name) warnings.push("Name is required.");
  if (!payload.systemPrompt) warnings.push("System prompt is required.");
  $("personaWarnings").innerHTML = warnings.map((warning) => `<p>${escapeHtml(warning)}</p>`).join("");
}

function showPersonaNotice(text, ok = true) {
  const notice = $("personaNotice");
  notice.textContent = text;
  notice.className = `persona-notice ${ok ? "status-success" : "status-error"}`;
}

async function savePersona() {
  renderPersonaPreview();
  const payload = personaFormPayload();
  if (!payload.name || !payload.systemPrompt) {
    showPersonaNotice("Name and system prompt are required.", false);
    return;
  }
  const data = await post("persona_save", payload);
  showPersonaNotice(data.message || data.error || "Saved.", Boolean(data.ok));
  if (data.ok) state.selectedPersona = data.status?.persona || payload.name;
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

function voiceModeRows() {
  return [
    ["free_talk", "Free Talk"],
    ["wake_word", "Wake Word"],
    ["push_to_talk", "Push To Talk"],
  ];
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
    const active = agentIsActive(job);
    const detail = active ? (job.action || job.state || job.status) : "idle";
    const chip = document.createElement("article");
    chip.className = `agent-chip${active ? " active" : ""}`;
    chip.innerHTML = `<strong>${escapeHtml(backend)}</strong><span>${escapeHtml(s.agentMachines[backend] || "local")} / ${escapeHtml(detail)}</span>`;
    strip.appendChild(chip);
  });
}

function renderAgentEvent(event) {
  const label = event.action || event.state || event.event || "agent update";
  if (event.event === "progress") addMessage("agent", event.agent || "Agent", label);
  if (event.event === "finished") {
    const result = event.result || event.summary || "No answer returned.";
    addMessage("agent", event.agent || "Agent", result);
  }
}

function storeAgentEvent(event) {
  const jobId = event.job_id;
  if (!jobId) return;
  const old = state.agentJobs[jobId] || {};
  let lines = Array.isArray(old.lines) ? [...old.lines] : [];
  if (event.event === "output" && event.line) {
    lines.push(event.line);
    lines = lines.slice(-250);
  } else if (Array.isArray(event.lines)) {
    lines = event.lines.slice(-250);
  }
  let moves = Array.isArray(old.moves) ? [...old.moves] : [];
  const move = moveFromEvent(event);
  if (move) moves = [...moves, move].slice(-80);
  state.agentJobs[jobId] = { ...old, ...event, lines, moves };
  state.selectedAgentJobId = state.selectedAgentJobId || jobId;
  renderAgentsPage();
}

async function loadAgentsPage() {
  const response = await fetch("/api/agents");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  state.status = data.status || state.status;
  state.agentHistory = data.history || [];
  state.agentPrompts = data.prompts || state.agentPrompts;
  state.confirmHistory = data.confirmHistory || [];
  (data.jobs || []).forEach((job) => {
    if (!job.job_id) return;
    const old = state.agentJobs[job.job_id] || {};
    state.agentJobs[job.job_id] = {
      ...old,
      ...job,
      lines: Array.isArray(job.lines) ? job.lines : old.lines || [],
      moves: Array.isArray(old.moves) ? old.moves : fallbackAgentMoves(job),
    };
  });
  renderStatus();
  renderAgentsPage({ forcePrompts: true });
}

function allAgentJobs() {
  const rows = new Map();
  state.agentHistory.forEach((job, index) => {
    const id = job.job_id || `history-${index}`;
    rows.set(id, { ...job, job_id: id, source: "history" });
  });
  Object.values(state.agentJobs).forEach((job) => {
    if (!job.job_id) return;
    rows.set(job.job_id, { ...(rows.get(job.job_id) || {}), ...job, source: "live" });
  });
  return [...rows.values()].sort((a, b) => (
    Date.parse(b.started_at || b.finished_at || 0) - Date.parse(a.started_at || a.finished_at || 0)
  ));
}

function renderAgentsPage(options = {}) {
  if (!$("agentsView")) return;
  renderAgentRoster();
  renderAgentJobList();
  renderAgentDetail();
  renderAgentPrompts(Boolean(options.forcePrompts));
  renderConfirmHistory();
}

function renderConfirmHistory() {
  const list = $("confirmHistoryList");
  if (!list) return;
  const rows = state.confirmHistory || [];
  $("confirmHistoryCount").textContent = `${rows.length} resolved`;
  if (!rows.length) {
    list.innerHTML = '<div class="empty-state"><strong>No confirmations resolved yet.</strong><span>Held delegations you approve or deny will show up here with the reason they were held.</span></div>';
    return;
  }
  list.innerHTML = rows.map((row) => {
    const decisionTone = row.decision === "approve" ? "status-success" : "status-error";
    const decisionLabel = row.decision === "approve" ? "Approved" : "Denied";
    return `<article class="confirm-history-row"><div class="confirm-history-head"><strong>${escapeHtml(row.agent || "agent")}</strong><b class="${decisionTone}">${decisionLabel}</b><time>${escapeHtml(fmtClock(row.resolvedAt))}</time></div><p>${escapeHtml(compactText(row.task))}</p><p class="muted">${escapeHtml(row.reason || "")}</p></article>`;
  }).join("");
}

function renderAgentRoster() {
  const roster = $("agentRoster");
  if (!roster || !state.status) return;
  roster.innerHTML = "";
  state.status.agentBackends.forEach((backend) => {
    const live = Object.values(state.agentJobs).find((job) => job.agent === backend && agentIsActive(job));
    const status = live?.status || "idle";
    const detail = live ? compactText(live.action || live.state || live.task, "Working") : (state.status.agentMachines?.[backend] || "local");
    const row = document.createElement("article");
    row.className = `agent-roster-row ${status}`;
    row.innerHTML = `<strong>${escapeHtml(backend)}</strong><span>${escapeHtml(detail)}</span><b>${escapeHtml(status)}</b>`;
    roster.appendChild(row);
  });
}

function renderAgentJobList() {
  const list = $("agentJobList");
  if (!list) return;
  const jobs = allAgentJobs();
  $("agentJobCount").textContent = `${jobs.length} job${jobs.length === 1 ? "" : "s"}`;
  list.innerHTML = "";
  if (!jobs.length) {
    list.innerHTML = '<div class="empty-state"><strong>No agent jobs yet.</strong><span>Delegated work will appear here with raw output and results.</span></div>';
    return;
  }
  if (!state.selectedAgentJobId || !jobs.some((job) => job.job_id === state.selectedAgentJobId)) {
    state.selectedAgentJobId = jobs[0].job_id;
  }
  jobs.forEach((job) => {
    const button = document.createElement("button");
    button.type = "button";
    const active = agentIsActive(job);
    button.className = `agent-job-row ${job.job_id === state.selectedAgentJobId ? "active" : ""} ${active ? "running" : ""}`;
    const moves = Array.isArray(job.moves) ? job.moves : fallbackAgentMoves(job);
    const current = moves.at(-1)?.text || job.action || job.state || "No moves yet";
    const title = job.task || job.summary || "Agent task";
    button.innerHTML = `<strong>${escapeHtml(job.agent || "Agent")}</strong><b>${escapeHtml(job.status || job.state || "unknown")}</b><span>${escapeHtml(title)}</span><em>${escapeHtml(current)}</em><time>${escapeHtml(fmtClock(job.started_at || job.finished_at))}</time>`;
    button.addEventListener("click", () => {
      state.selectedAgentJobId = job.job_id;
      renderAgentJobList();
      renderAgentDetail();
    });
    list.appendChild(button);
  });
}

function selectedAgentJob() {
  return allAgentJobs().find((job) => job.job_id === state.selectedAgentJobId);
}

function renderAgentDetail() {
  if (!$("agentDetail")) return;
  const job = selectedAgentJob();
  if (!job) {
    $("agentDetailTitle").textContent = "No job selected";
    $("agentDetailStatus").textContent = "idle";
    $("agentDetailAgent").textContent = "--";
    $("agentDetailMachine").textContent = "--";
    $("agentDetailState").textContent = "--";
    $("agentDetailElapsed").textContent = "--";
    $("agentDetailTask").value = "";
    $("agentDetailResult").value = "";
    $("agentLineCount").textContent = "0 lines";
    $("agentMoveCount").textContent = "0 moves";
    $("agentDetailNow").innerHTML = '<span>Current move</span><strong>Nothing running.</strong><p class="muted">Select a live job to see what the agent is doing now.</p>';
    $("agentMoveTimeline").innerHTML = "";
    $("agentDetail").textContent = "Select a job to inspect its output.";
    return;
  }
  const lines = Array.isArray(job.lines) ? job.lines : [];
  const moves = Array.isArray(job.moves) && job.moves.length ? job.moves : fallbackAgentMoves(job);
  const currentMove = moves.at(-1);
  $("agentDetailTitle").textContent = job.task || job.action || "Agent task";
  $("agentDetailStatus").textContent = job.status || job.state || "unknown";
  $("agentDetailStatus").className = `accent-agent-label ${agentStatusTone(job.status)}`;
  $("agentDetailAgent").textContent = job.agent || "--";
  $("agentDetailMachine").textContent = job.machine || "--";
  $("agentDetailState").textContent = job.state || job.status || "--";
  $("agentDetailElapsed").textContent = typeof job.elapsed_secs === "number" ? fmtSeconds(job.elapsed_secs) : fmtSeconds(job.secs);
  $("agentDetailTask").value = job.task || "";
  $("agentDetailResult").value = job.result || job.summary || job.failure_detail || "";
  $("agentLineCount").textContent = `${lines.length} line${lines.length === 1 ? "" : "s"}`;
  $("agentMoveCount").textContent = `${moves.length} move${moves.length === 1 ? "" : "s"}`;
  const nowMeta = [
    job.tool ? `Tool: ${job.tool}` : "",
    job.step ? `Step: ${job.step}${job.step_total ? `/${job.step_total}` : ""}` : "",
    job.last_completed_step ? `Last done: ${job.last_completed_step}` : "",
  ].filter(Boolean).join(" · ");
  $("agentDetailNow").innerHTML = `<span>${agentIsActive(job) ? "Current move" : "Final move"}</span><strong>${escapeHtml(currentMove?.text || job.action || job.state || "No move reported.")}</strong><p class="muted">${escapeHtml(nowMeta || job.model_label || job.failure_kind || "No extra status reported.")}</p>`;
  $("agentMoveTimeline").innerHTML = moves.map((move) => (
    `<li class="${escapeHtml(move.status || "")}"><time>${escapeHtml(fmtClock(move.at))}</time><div><strong>${escapeHtml(move.event || "update")}</strong><p>${escapeHtml(move.text)}</p>${move.tool || move.step ? `<span>${escapeHtml([move.tool, move.step ? `step ${move.step}${move.step_total ? `/${move.step_total}` : ""}` : ""].filter(Boolean).join(" · "))}</span>` : ""}</div></li>`
  )).join("");
  $("agentDetail").textContent = lines.length ? lines.join("\n") : "No raw output captured for this job.";
}

function renderAgentPrompts(force = false) {
  const host = $("agentPromptEditors");
  if (!host || !state.agentPrompts) return;
  if (!force && document.activeElement?.closest("#agentPromptEditors")) return;
  host.innerHTML = "";
  Object.values(state.agentPrompts).forEach((prompt) => {
    const row = document.createElement("label");
    row.className = "prompt-editor";
    row.innerHTML = `<span><strong>${escapeHtml(prompt.label)}</strong><em>${escapeHtml(prompt.help || "")}</em></span><textarea id="promptEditor_${prompt.key}" rows="${prompt.key === "statusProtocol" ? 12 : 7}"></textarea><small>${prompt.custom ? "Custom" : "Default"}${prompt.required?.length ? ` · requires ${escapeHtml(prompt.required.join(", "))}` : ""}</small>`;
    row.querySelector("textarea").value = prompt.value || "";
    host.appendChild(row);
  });
}

function showAgentPromptNotice(text, ok = true) {
  const notice = $("agentPromptNotice");
  if (!notice) return;
  notice.textContent = text;
  notice.className = `persona-notice ${ok ? "status-success" : "status-error"}`;
}

async function saveAgentPrompts() {
  const prompts = {};
  Object.keys(state.agentPrompts || {}).forEach((key) => {
    prompts[key] = $(`promptEditor_${key}`)?.value || "";
  });
  const data = await post("agent_prompts_save", { prompts });
  if (data.prompts) state.agentPrompts = data.prompts;
  showAgentPromptNotice(data.message || data.error || "Saved.", Boolean(data.ok));
  renderAgentPrompts(true);
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
  if (s.vram?.available) {
    rows.push(["VRAM", s.vram.label, vramTone(s.vram.percent).replace("status-", "")]);
  }
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
  if (state.selectedMemory && !rows.some((row) => row.id === state.selectedMemory?.id && row.scope === state.selectedMemory?.scope)) {
    state.selectedMemory = null;
  }
  renderMemoryDetail();
  if (!rows.length) {
    list.innerHTML = '<div class="empty-state"><strong>No memories match.</strong><span>Change tab, tree filter, or search text.</span></div>';
    return;
  }
  rows.forEach((row) => {
    const card = document.createElement("article");
    const selected = state.selectedMemory?.id === row.id && state.selectedMemory?.scope === row.scope;
    card.className = `memory-card${selected ? " active" : ""}`;
    card.innerHTML = `<h4>${escapeHtml(memoryTitle(row))}</h4><p>${escapeHtml(row.text)}</p><div class="tag-row"><span class="tag">${escapeHtml(memoryScopeLabel(row))}</span><span class="tag">${escapeHtml(memoryScore(row))}</span></div>`;
    card.addEventListener("click", () => {
      state.selectedMemory = row;
      renderMemory();
    });
    list.appendChild(card);
  });
}

function currentMemoryRows() {
  const active = document.querySelector(".tab.active")?.dataset.memoryTab || "transcript";
  const filter = document.querySelector(".tree-item.active")?.dataset.memoryFilter || "all";
  const query = ($("memorySearch")?.value || "").trim().toLowerCase();
  let rows = active === "knowledge" || active === "pinned" ? state.memories.semantic : state.memories.short;
  if (active === "pinned") rows = rows.filter(isPinnedMemory);
  if (filter === "short") rows = rows.filter((row) => row.scope === "short");
  if (filter === "semantic") rows = rows.filter((row) => row.scope === "semantic");
  if (filter === "pinned") rows = rows.filter(isPinnedMemory);
  if (query) rows = rows.filter((row) => `${row.label || ""} ${row.text || ""} ${row.source || ""}`.toLowerCase().includes(query));
  return rows;
}

function normalizeMemoryRow(row, scope) {
  if (typeof row === "string") {
    return { id: row, scope: scope === "semantic" ? "semantic" : "short", source: scope === "semantic" ? "semantic" : "transcript", label: scope === "semantic" ? "Memory" : "Transcript", text: row, score: null };
  }
  const source = row.source || row.metadata?.source || (scope === "semantic" ? "semantic" : "transcript");
  return {
    id: row.id || row.memory_id || `${scope || "memory"}-${row.text || row.content || row.summary || ""}`,
    scope: row.scope || (scope === "semantic" ? "semantic" : "short"),
    source,
    role: row.role || "",
    label: row.label || row.role || row.id || "",
    text: String(row.text || row.memory || row.content || row.summary || ""),
    score: row.score ?? null,
    metadata: row.metadata || {},
  };
}

function isPinnedMemory(row) {
  return ["manual_gui", "multimodal_prompt"].includes(row.source);
}

function memoryTitle(row) {
  if (row.scope === "short") return row.label || "Transcript";
  return row.label || row.id || "Semantic memory";
}

function memoryScopeLabel(row) {
  if (row.scope === "short") return "Transcript";
  if (isPinnedMemory(row)) return "Pinned fact";
  return row.source === "session" ? "Unavailable" : "Knowledge";
}

function memoryScore(row) {
  return typeof row.score === "number" ? row.score.toFixed(2) : "local";
}

function renderMemoryDetail() {
  if (!state.selectedMemory) {
    $("memoryDetailTitle").textContent = "Memory";
    $("memoryDetailText").textContent = "Select a row to inspect its source, scope, and text.";
    $("memoryDeleteBtn").disabled = true;
    return;
  }
  const row = state.selectedMemory;
  $("memoryDetailTitle").textContent = memoryTitle(row);
  $("memoryDetailText").textContent = `${row.text}\n\nScope: ${memoryScopeLabel(row)}\nSource: ${row.source || "local"}${row.id ? `\nID: ${row.id}` : ""}`;
  $("memoryDeleteBtn").disabled = row.scope !== "semantic" || !row.id;
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

function ttsSettingsPayload() {
  return {
    provider: $("settingsTtsProviderSelect").value,
    voice: $("settingsVoiceSelect").value,
    model: $("settingsCoquiModelSelect").value,
    speaker: $("settingsCoquiSpeakerSelect").value,
    language: $("settingsCoquiLanguageSelect").value,
    device: $("settingsCoquiDeviceSelect").value,
  };
}

function applyTtsSettings() {
  post("tts", ttsSettingsPayload());
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

async function fetchCliDiagnostics() {
  const codex = $("setupCodexStatus");
  const claude = $("setupClaudeStatus");
  if (!codex || !claude) return;
  codex.textContent = "Checking...";
  claude.textContent = "Checking...";
  codex.className = "status-warning";
  claude.className = "status-warning";
  try {
    const response = await fetch('/api/cli-diagnostics');
    const data = await response.json();
    
    const codexData = data.cli_agents["codex"];
    if (codexData && codexData.status.available) {
        if (codexData.status.auth_ok) {
            codex.textContent = `Ready (${codexData.status.version})`;
            codex.className = "status-success";
        } else {
            codex.textContent = codexData.status.error || "Auth failed";
            codex.className = "status-error";
        }
    } else {
        codex.textContent = codexData?.status?.error || "Not found";
        codex.className = "status-error";
    }

    const claudeData = data.cli_agents["claude-code"];
    if (claudeData && claudeData.status.available) {
        if (claudeData.status.auth_ok) {
            claude.textContent = `Ready (${claudeData.status.version})`;
            claude.className = "status-success";
        } else {
            claude.textContent = claudeData.status.error || "Auth failed";
            claude.className = "status-error";
        }
    } else {
        claude.textContent = claudeData?.status?.error || "Not found";
        claude.className = "status-error";
    }
  } catch (e) {
    codex.textContent = "Error";
    codex.className = "status-error";
    claude.textContent = "Error";
    claude.className = "status-error";
  }
}

function navigateTo(view) {
  document.querySelector(`.nav-link[data-view="${view}"]`)?.click();
}

function commandPaletteItems() {
  const items = [];

  [
    ["control", "Control Center"],
    ["agents", "Agents"],
    ["personas", "Personas"],
    ["memory", "Memory"],
    ["setup", "Setup"],
    ["status", "Status"],
    ["settings", "Settings"],
  ].forEach(([view, label]) => {
    items.push({ id: `nav:${view}`, group: "Navigate", label, hint: "Open panel", action: () => navigateTo(view) });
  });

  items.push(
    { id: "action:focus-message", group: "Actions", label: "Focus message", hint: "Ctrl L", action: () => $("messageInput").focus() },
    { id: "action:toggle-mic", group: "Actions", label: "Toggle mic", hint: "Ctrl M", action: () => post("mute", { muted: !state.status?.muted }) },
    { id: "action:cycle-voice-mode", group: "Actions", label: "Cycle voice mode", hint: "", action: () => post("voice_mode", { mode: nextMode(state.status?.voiceMode) }) },
    { id: "action:new-chat", group: "Actions", label: "New chat", hint: "", action: () => { state.messages = []; renderChat(); post("restart_chat"); } },
    { id: "action:refresh-memory", group: "Actions", label: "Refresh memory", hint: "", action: () => { navigateTo("memory"); post("refresh_memory", { query: $("memorySearch").value }); } },
    { id: "action:export-diagnostics", group: "Actions", label: "Export diagnostics", hint: "", action: () => post("export_diagnostics") },
    { id: "action:start-ollama", group: "Actions", label: "Start Ollama", hint: "", action: () => post("start_ollama") },
    { id: "action:free-vram", group: "Actions", label: "Free VRAM", hint: "", action: () => post("free_vram") },
  );

  (state.status?.personas || []).forEach((persona) => {
    items.push({
      id: `persona:${persona.name}`,
      group: "Personas",
      label: persona.name,
      hint: compactText(persona.description || persona.effectiveModel || "", "Persona"),
      action: () => {
        navigateTo("personas");
        state.selectedPersona = persona.name;
        state.personaOriginal = null;
        loadPersonaEditor(persona);
        post("persona", { name: persona.name });
      },
    });
  });

  allAgentJobs().slice(0, 20).forEach((job) => {
    items.push({
      id: `agent-job:${job.job_id}`,
      group: "Agent Jobs",
      label: compactText(job.task || job.summary || job.agent || "Agent job", "Agent job"),
      hint: `${job.agent || "agent"} · ${job.status || job.state || "unknown"}`,
      action: () => {
        navigateTo("agents");
        state.selectedAgentJobId = job.job_id;
        renderAgentJobList();
        renderAgentDetail();
      },
    });
  });

  [...state.memories.short, ...state.memories.semantic].slice(0, 20).forEach((row, index) => {
    const label = compactText(row.text || row.label || "", "Memory");
    if (!label) return;
    items.push({
      id: `memory:${row.scope || "short"}:${row.id ?? index}`,
      group: "Memory",
      label,
      hint: row.scope === "semantic" ? "Semantic memory" : "Short-term memory",
      action: () => {
        navigateTo("memory");
        const query = (row.label || row.text || "").slice(0, 60);
        $("memorySearch").value = query;
        renderMemory();
        post("refresh_memory", { query });
      },
    });
  });

  return items;
}

function filteredPaletteItems() {
  const query = ($("paletteInput")?.value || "").trim().toLowerCase();
  const items = commandPaletteItems();
  if (!query) return items;
  return items.filter((item) => `${item.label} ${item.hint || ""} ${item.group}`.toLowerCase().includes(query));
}

function renderCommandPalette() {
  const results = $("paletteResults");
  if (!results) return;
  const items = filteredPaletteItems();
  if (state.paletteIndex >= items.length) state.paletteIndex = Math.max(0, items.length - 1);
  results.innerHTML = "";
  if (!items.length) {
    results.innerHTML = '<div class="empty-state"><strong>No matches.</strong><span>Try a different search.</span></div>';
    return;
  }
  let currentGroup = null;
  items.forEach((item, index) => {
    if (item.group !== currentGroup) {
      currentGroup = item.group;
      const heading = document.createElement("p");
      heading.className = "palette-group";
      heading.textContent = currentGroup;
      results.appendChild(heading);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = `palette-result${index === state.paletteIndex ? " active" : ""}`;
    button.innerHTML = `<span>${escapeHtml(item.label)}</span>${item.hint ? `<em>${escapeHtml(item.hint)}</em>` : ""}`;
    button.addEventListener("mouseenter", () => { state.paletteIndex = index; renderCommandPalette(); });
    button.addEventListener("click", () => runPaletteItem(item));
    results.appendChild(button);
  });
}

function runPaletteItem(item) {
  closeCommandPalette();
  item.action();
}

function openCommandPalette(initialQuery = "") {
  const palette = $("commandPalette");
  if (!palette) return;
  palette.classList.remove("hidden");
  state.paletteIndex = 0;
  $("paletteInput").value = initialQuery;
  renderCommandPalette();
  $("paletteInput").focus();
}

function closeCommandPalette() {
  const palette = $("commandPalette");
  if (!palette || palette.classList.contains("hidden")) return;
  palette.classList.add("hidden");
  $("paletteOpenBtn")?.focus();
}

function isCommandPaletteOpen() {
  return !$("commandPalette")?.classList.contains("hidden");
}

function bind() {
  window.addEventListener("rap:avatar-ready", syncAvatarRuntime);
  window.addEventListener("rap:avatar-collapse", (event) => {
    const collapsed = Boolean(event.detail?.collapsed);
    $('avatarSettingCollapsed').value = String(collapsed);
    void saveAvatarSettings({ panelCollapsed: collapsed });
  });
  $('avatarSettingsSaveBtn').addEventListener('click', () => void saveAvatarSettings());
  document.querySelectorAll(".nav-link").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      $(`${button.dataset.view}View`).classList.add("active");
      if (button.dataset.view === "memory") post("refresh_memory", { query: $("memorySearch").value });
      if (button.dataset.view === "agents") loadAgentsPage().catch((error) => showAgentPromptNotice(`Agents refresh failed: ${error.message}`, false));
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
  $("settingsPersonaSelect").addEventListener("change", (event) => post("persona", { name: event.target.value }));
  $("settingsToolSelect").addEventListener("change", (event) => post("tool_user", { backend: event.target.value }));
  $("settingsVoiceModeSelect").addEventListener("change", (event) => post("voice_mode", { mode: event.target.value }));
  $("settingsModelSelect").addEventListener("change", (event) => post("model", { model: event.target.value }));
  $("settingsVoiceSelect").addEventListener("change", applyTtsSettings);
  ["settingsTtsProviderSelect", "settingsCoquiModelSelect", "settingsCoquiSpeakerSelect", "settingsCoquiLanguageSelect", "settingsCoquiDeviceSelect"].forEach((id) => {
    $(id).addEventListener("change", applyTtsSettings);
  });
  $("settingsCoquiRefreshBtn").addEventListener("click", () => post("tts_refresh"));
  $("settingsTestVoiceBtn").addEventListener("click", () => post("tts_test"));
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
  $("settingsMuteBtn").addEventListener("click", () => post("mute", { muted: !state.status?.muted }));
  $("settingsRestartBtn").addEventListener("click", () => { state.messages = []; renderChat(); post("restart_chat"); });
  $("settingsMemoryBtn").addEventListener("click", () => post("refresh_memory", { query: $("memorySearch").value }));
  $("settingsDiagnosticsBtn").addEventListener("click", () => post("export_diagnostics"));
  $("settingsOllamaBtn").addEventListener("click", () => post("start_ollama"));
  $("settingsVramBtn").addEventListener("click", () => post("free_vram"));
  $("settingsRebootBtn").addEventListener("click", () => post("reboot_session"));
  $("agentRefreshBtn").addEventListener("click", () => loadAgentsPage().catch((error) => showAgentPromptNotice(`Agents refresh failed: ${error.message}`, false)));
  $("agentPromptSaveBtn").addEventListener("click", saveAgentPrompts);
  $("refreshCliBtn").addEventListener("click", fetchCliDiagnostics);

  $("memorySearch").addEventListener("input", (event) => { renderMemory(); post("refresh_memory", { query: event.target.value }); });
  $("memoryPinBtn").addEventListener("click", async () => {
    const text = $("memoryPinInput").value.trim();
    if (!text) return;
    $("memoryPinInput").value = "";
    await post("memory_add", { text });
  });
  $("memoryPinInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("memoryPinBtn").click();
    }
  });
  $("memoryDeleteBtn").addEventListener("click", () => {
    if (state.selectedMemory?.scope === "semantic" && state.selectedMemory.id && confirm("Delete selected semantic memory?")) {
      post("memory_delete", { id: state.selectedMemory.id });
      state.selectedMemory = null;
      renderMemory();
    }
  });
  $("memoryForgetShortBtn").addEventListener("click", () => {
    if (confirm("Clear short-term transcript memory?")) post("memory_forget_short");
  });
  $("memoryForgetSemanticBtn").addEventListener("click", () => {
    if (confirm("Delete all semantic memories?")) post("memory_forget_semantic");
  });
  $("personaSearch").addEventListener("input", () => renderPersonaList(state.status || { personas: [] }));
  ["personaEditName", "personaEditDescription", "personaEditPrompt", "personaEditModel", "personaEditVoice", "personaEditVoiceBackend", "personaEditVoiceModel", "personaEditCoquiSpeaker", "personaEditCoquiLanguage", "personaEditCoquiDevice", "personaEditToolUser"].forEach((id) => {
    $(id).addEventListener("input", renderPersonaPreview);
    $(id).addEventListener("change", renderPersonaPreview);
  });
  $("newPersonaBtn").addEventListener("click", async () => {
    const data = await post("persona_create", { source: state.selectedPersona || state.status?.persona });
    showPersonaNotice(data.message || data.error || "Created.", Boolean(data.ok));
    if (data.ok) state.selectedPersona = data.status?.persona;
  });
  $("duplicatePersonaBtn").addEventListener("click", async () => {
    const data = await post("persona_duplicate", { name: state.selectedPersona || state.status?.persona });
    showPersonaNotice(data.message || data.error || "Duplicated.", Boolean(data.ok));
    if (data.ok) state.selectedPersona = data.status?.persona;
  });
  $("deletePersonaBtn").addEventListener("click", async () => {
    const name = state.selectedPersona || state.status?.persona;
    if (!name || !confirm(`Delete or reset ${name}?`)) return;
    const data = await post("persona_delete", { name });
    showPersonaNotice(data.message || data.error || "Deleted.", Boolean(data.ok));
    if (data.ok) state.selectedPersona = data.status?.persona;
  });
  $("revertPersonaBtn").addEventListener("click", () => loadPersonaEditor(selectedPersonaRecord()));
  $("savePersonaBtn").addEventListener("click", savePersona);
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      if (tab.dataset.memoryTab === "transcript") {
        document.querySelector('.tree-item[data-memory-filter="short"]')?.click();
      } else if (tab.dataset.memoryTab === "pinned") {
        document.querySelector('.tree-item[data-memory-filter="pinned"]')?.click();
      } else {
        document.querySelector('.tree-item[data-memory-filter="semantic"]')?.click();
      }
      state.selectedMemory = null;
      renderMemory();
    });
  });
  document.querySelectorAll(".tree-item").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".tree-item").forEach((row) => row.classList.remove("active"));
      item.classList.add("active");
      state.selectedMemory = null;
      renderMemory();
    });
  });
  $("paletteOpenBtn").addEventListener("click", () => openCommandPalette());
  $("paletteInput").addEventListener("input", () => { state.paletteIndex = 0; renderCommandPalette(); });
  $("commandPalette").addEventListener("click", (event) => {
    if (event.target === $("commandPalette")) closeCommandPalette();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      isCommandPaletteOpen() ? closeCommandPalette() : openCommandPalette();
      return;
    }
    if (isCommandPaletteOpen()) {
      if (event.key === "Escape") { event.preventDefault(); closeCommandPalette(); return; }
      if (event.key === "ArrowDown") { event.preventDefault(); state.paletteIndex += 1; renderCommandPalette(); return; }
      if (event.key === "ArrowUp") { event.preventDefault(); state.paletteIndex = Math.max(0, state.paletteIndex - 1); renderCommandPalette(); return; }
      if (event.key === "Enter") {
        event.preventDefault();
        const item = filteredPaletteItems()[state.paletteIndex];
        if (item) runPaletteItem(item);
        return;
      }
      return;
    }
    if (event.ctrlKey && event.key.toLowerCase() === "l") $("messageInput").focus();
    if (event.ctrlKey && event.key.toLowerCase() === "m") post("mute", { muted: !state.status?.muted });
  });
}

bind();
poll();
setInterval(renderWakeStatus, 250);
fetchCliDiagnostics();
