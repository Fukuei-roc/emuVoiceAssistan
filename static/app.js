const appShell = document.querySelector(".app-shell");
const chat = document.getElementById("chat");
const form = document.getElementById("chatForm");
const input = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const voiceButton = document.getElementById("voiceButton");
const stopVoiceButton = document.getElementById("stopVoiceButton");
const voiceStatus = document.getElementById("voiceStatus");
const voiceIndicator = document.getElementById("voiceIndicator");
const voiceActionTitle = document.getElementById("voiceActionTitle");
const voiceActionHint = document.getElementById("voiceActionHint");
const currentTask = document.getElementById("currentTask");
const taskTitle = document.getElementById("taskTitle");
const taskSupport = document.getElementById("taskSupport");
const flowLabel = document.getElementById("flowLabel");
const textToggle = document.getElementById("textToggle");
const textClose = document.getElementById("textClose");
const composerDrawer = document.getElementById("composerDrawer");
const historyToggle = document.getElementById("historyToggle");
const historyClose = document.getElementById("historyClose");
const historyPanel = document.getElementById("historyPanel");
const drawerBackdrop = document.getElementById("drawerBackdrop");
const thinkingIndicator = document.getElementById("thinkingIndicator");
const appNotice = document.getElementById("appNotice");
const noticeTitle = document.getElementById("noticeTitle");
const noticeMessage = document.getElementById("noticeMessage");
const noticeDismiss = document.getElementById("noticeDismiss");
const remoteAudio = document.getElementById("remoteAudio");

const debugSession = document.getElementById("debugSession");
const debugSources = document.getElementById("debugSources");
const debugLatency = document.getElementById("debugLatency");
const debugExpected = document.getElementById("debugExpected");
const debugParsed = document.getElementById("debugParsed");
const debugTurnStatus = document.getElementById("debugTurnStatus");
const debugRealtime = document.getElementById("debugRealtime");
const debugRawUserText = document.getElementById("debugRawUserText");
const debugInterpretationSource = document.getElementById("debugInterpretationSource");
const debugSemanticResult = document.getElementById("debugSemanticResult");
const debugRealtimeConnection = document.getElementById("debugRealtimeConnection");
const debugRealtimeAudio = document.getElementById("debugRealtimeAudio");

let sessionId = null;
let peerConnection = null;
let dataChannel = null;
let localStream = null;
let realtimeConnected = false;
let activeAiText = "";
let activeAiBubble = null;
let textRequestPending = false;
let connectionGeneration = 0;
let disconnectTimer = null;
let activeNoticeKind = null;
let realtimeRouting = {};

const HEALTHY_VOICE_STATES = new Set(["connected", "listening", "thinking", "speaking"]);
const FATAL_REALTIME_ERROR_CODES = new Set([
  "authentication_error",
  "invalid_api_key",
  "session_closed",
  "session_expired",
]);

const voiceLabels = {
  idle: ["待命", "點一下開始說話", "AI 已準備好協助你"],
  connecting: ["連線中", "正在連線…", "正在準備安全的即時語音"],
  connected: ["已連線", "語音已就緒", "直接開口，我正在等你"],
  listening: ["聆聽中", "正在聆聽", "請自然說出目前的狀況"],
  thinking: ["分析中", "正在分析…", "AI 正在判斷下一個處理步驟"],
  speaking: ["回答中", "AI 正在回答", "你可以隨時開口打斷"],
  error: ["已中斷", "連線中斷", "點一下語音核心重新連線"],
};

function updateDebugState(data) {
  debugSession.textContent = data.session_id || sessionId || "-";
  const source = data.sources?.[0] || {};
  debugSources.textContent = [data.vehicle || source.vehicle, data.fault_id || source.fault_id, source.source].filter(Boolean).join(" / ") || "-";
  debugExpected.textContent = data.semantic_result?.knowledge_chars ? `knowledge chars=${data.semantic_result.knowledge_chars}` : "LLM-driven";
  debugParsed.textContent = data.semantic_result ? JSON.stringify(data.semantic_result) : "-";
  debugTurnStatus.textContent = data.last_turn_status || "llm";
  debugRawUserText.textContent = data.raw_user_text || "-";
  debugInterpretationSource.textContent = data.interpretation_source || "llm";
  debugSemanticResult.textContent = data.semantic_result ? JSON.stringify(data.semantic_result) : "-";
}

function addMessage(role, text, options = {}) {
  const item = document.createElement("div");
  item.className = `history-message ${role === "user" ? "user" : "ai"}${options.streaming ? " streaming" : ""}`;
  const roleBadge = document.createElement("span");
  roleBadge.className = "history-role";
  roleBadge.textContent = role === "user" ? "你" : "AI";
  const bubble = document.createElement("p");
  bubble.textContent = text;
  item.append(roleBadge, bubble);
  chat.appendChild(item);
  chat.scrollTop = chat.scrollHeight;
  if (role === "ai" && !options.streaming) updateCurrentTask(text);
  return bubble;
}

function updateCurrentTask(text) {
  const cleanText = String(text || "").trim();
  if (!cleanText) return;
  taskTitle.textContent = cleanText;
  taskSupport.textContent = cleanText.includes("？") || cleanText.includes("?") ? "請直接說出答案。" : "完成目前指示後，再告訴我結果。";
  flowLabel.textContent = "引導進行中";
  currentTask.classList.remove("is-updating");
  void currentTask.offsetWidth;
  currentTask.classList.add("is-updating");
}

function openDrawer(drawer, toggle) {
  closeDrawers();
  drawer.hidden = false;
  drawerBackdrop.hidden = false;
  toggle?.setAttribute("aria-expanded", "true");
}

function closeDrawers() {
  composerDrawer.hidden = true;
  historyPanel.hidden = true;
  drawerBackdrop.hidden = true;
  textToggle.setAttribute("aria-expanded", "false");
  historyToggle.setAttribute("aria-expanded", "false");
}

textToggle.addEventListener("click", () => {
  openDrawer(composerDrawer, textToggle);
  window.setTimeout(() => input.focus({ preventScroll: true }), 180);
});
historyToggle.addEventListener("click", () => openDrawer(historyPanel, historyToggle));
textClose.addEventListener("click", closeDrawers);
historyClose.addEventListener("click", closeDrawers);
drawerBackdrop.addEventListener("click", closeDrawers);

function showNotice(title, message, technicalMessage = "", kind = "general") {
  noticeTitle.textContent = title;
  noticeMessage.textContent = message;
  appNotice.hidden = false;
  activeNoticeKind = kind;
  if (technicalMessage) debugRealtime.textContent = technicalMessage;
}
function hideNotice(kind = null) {
  if (kind && activeNoticeKind !== kind) return;
  appNotice.hidden = true;
  activeNoticeKind = null;
}
noticeDismiss.addEventListener("click", () => hideNotice());

function setVoiceState(state, technicalText = "") {
  const [status, title, hint] = voiceLabels[state] || voiceLabels.idle;
  appShell.dataset.voiceState = state;
  voiceIndicator.dataset.state = state;
  voiceStatus.textContent = status;
  voiceActionTitle.textContent = title;
  voiceActionHint.textContent = hint;
  if (HEALTHY_VOICE_STATES.has(state)) hideNotice("voice");
  if (technicalText) debugRealtime.textContent = technicalText;
}

function setThinking(visible) {
  thinkingIndicator.hidden = !visible;
  if (!realtimeConnected) setVoiceState(visible ? "thinking" : "idle", visible ? "text response pending" : "text response complete");
}

input.addEventListener("input", () => { sendButton.disabled = !input.value.trim() || textRequestPending; });
form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (message) submitTextMessage(message);
});

async function submitTextMessage(message) {
  if (!message || textRequestPending) return;
  hideNotice();
  input.value = "";
  textRequestPending = true;
  sendButton.disabled = true;
  addMessage("user", message);
  closeDrawers();
  setThinking(true);
  taskSupport.textContent = "AI 正在分析你的回答…";
  const startedAt = performance.now();
  try {
    const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId, message }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "文字對話失敗");
    sessionId = data.session_id;
    setThinking(false);
    addMessage("ai", data.reply);
    updateDebugState(data);
    debugLatency.textContent = `${data.latency_ms || Math.round(performance.now() - startedAt)} ms`;
  } catch (error) {
    setThinking(false);
    taskSupport.textContent = "回答未送出，請稍後再試一次。";
    showNotice("訊息沒有送出", "網路似乎不穩定，請確認連線後再試一次。", error.message);
    debugLatency.textContent = `${Math.round(performance.now() - startedAt)} ms`;
  } finally {
    textRequestPending = false;
    sendButton.disabled = !input.value.trim();
  }
}

voiceButton.addEventListener("click", async () => {
  if (realtimeConnected || peerConnection) { stopRealtime(); return; }
  await startRealtime();
});
stopVoiceButton.addEventListener("click", () => stopRealtime());

async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || `連線失敗：${response.status}`);
  return payload;
}

async function startRealtime() {
  const generation = ++connectionGeneration;
  voiceButton.disabled = true;
  hideNotice();
  setVoiceState("connecting", "preparing microphone");
  try {
    realtimeRouting = {};
    ensureMicrophoneAvailable();
    if (!sessionId) sessionId = `realtime-${crypto.randomUUID()}`;
    unlockAudioElement();
    debugRealtime.textContent = "requesting microphone permission";
    localStream = await getMicrophoneStream();
    debugRealtime.textContent = "microphone enabled; loading YAML context";
    const context = await fetchJson("/api/realtime/context");
    updateRealtimeContextDebug(context);

    const currentPeer = new RTCPeerConnection();
    peerConnection = currentPeer;
    currentPeer.onconnectionstatechange = () => {
      if (!isCurrentConnection(generation, currentPeer)) return;
      const state = currentPeer.connectionState || "closed";
      debugRealtimeConnection.textContent = state;
      if (state === "connected") {
        clearDisconnectTimer();
        if (realtimeConnected) setVoiceState("connected", "webrtc connected");
      } else if (state === "disconnected" && realtimeConnected) {
        scheduleDisconnectFailure(generation, currentPeer);
      } else if (state === "failed") {
        markVoiceFatal("語音連線中斷", "點一下語音核心重新連線。", "WebRTC failed");
      }
    };
    currentPeer.ontrack = async (event) => {
      if (!isCurrentConnection(generation, currentPeer)) return;
      debugRealtimeAudio.textContent = `remote track: ${event.track.kind}`;
      remoteAudio.srcObject = event.streams[0];
      remoteAudio.autoplay = true;
      remoteAudio.playsInline = true;
      try { await remoteAudio.play(); debugRealtimeAudio.textContent = "audio playback started"; }
      catch (error) { debugRealtimeAudio.textContent = `audio playback failed: ${error.message}`; }
    };
    localStream.getTracks().forEach((track) => currentPeer.addTrack(track, localStream));
    const currentChannel = currentPeer.createDataChannel("oai-events");
    dataChannel = currentChannel;
    currentChannel.addEventListener("open", () => {
      if (!isCurrentConnection(generation, currentPeer, currentChannel)) return;
      setVoiceState("connecting", "realtime connected; applying YAML context");
      sendRealtimeEvent({ type: "session.update", session: context.session });
    });
    currentChannel.addEventListener("message", (event) => handleRealtimeEvent(event, generation, currentPeer, currentChannel));
    currentChannel.addEventListener("error", () => {
      if (isCurrentConnection(generation, currentPeer, currentChannel)) markVoiceFatal("語音連線中斷", "點一下語音核心重新連線。", "Realtime data channel error");
    });
    currentChannel.addEventListener("close", () => {
      if (isCurrentConnection(generation, currentPeer, currentChannel) && realtimeConnected) markVoiceFatal("語音連線中斷", "點一下語音核心重新連線。", "Realtime data channel closed");
    });

    const offer = await currentPeer.createOffer();
    await currentPeer.setLocalDescription(offer);
    const localSdp = currentPeer.localDescription?.sdp || offer.sdp || "";
    if (!localSdp.trim()) throw new Error("瀏覽器產生的 SDP offer 是空的");
    const sdpResponse = await fetch("/api/realtime/call", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sdp: localSdp }) });
    const sdpPayload = await sdpResponse.json();
    if (!sdpResponse.ok) throw new Error(sdpPayload.detail || `WebRTC SDP 交換失敗：${sdpResponse.status}`);
    if (!isCurrentConnection(generation, currentPeer, currentChannel)) return;
    await currentPeer.setRemoteDescription({ type: "answer", sdp: sdpPayload.sdp });
    if (!isCurrentConnection(generation, currentPeer, currentChannel)) return;
    realtimeConnected = true;
    stopVoiceButton.disabled = false;
    voiceButton.setAttribute("aria-pressed", "true");
    voiceButton.setAttribute("aria-label", "停止語音");
  } catch (error) {
    if (generation !== connectionGeneration) return;
    showNotice("無法開始語音", friendlyVoiceError(error), error.message, "voice");
    setVoiceState("error", error.message);
    stopRealtime(false, true);
  } finally { voiceButton.disabled = false; }
}

function friendlyVoiceError(error) {
  const text = error?.message || "";
  if (text.includes("麥克風")) return text;
  if (text.includes("HTTPS")) return "請使用 HTTPS 開啟網站，才能啟用手機麥克風。";
  return "請確認網路與麥克風權限後，再試一次。";
}

function unlockAudioElement() {
  remoteAudio.autoplay = true;
  remoteAudio.playsInline = true;
  remoteAudio.muted = false;
  const promise = remoteAudio.play();
  if (promise?.catch) promise.catch(() => { debugRealtimeAudio.textContent = "audio unlock pending remote track"; });
}

async function getMicrophoneStream() {
  const request = navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const timeout = new Promise((_, reject) => window.setTimeout(() => reject(new Error("麥克風授權逾時。請確認瀏覽器已允許這個網站使用麥克風。")), 15000));
  try { return await Promise.race([request, timeout]); }
  catch (error) {
    if (error?.name === "NotAllowedError") throw new Error("麥克風權限被拒絕。請在網站設定中允許麥克風後再試一次。");
    if (error?.name === "NotFoundError") throw new Error("找不到可用麥克風。請確認裝置麥克風可以使用。");
    throw error;
  }
}

function updateRealtimeContextDebug(context) {
  debugExpected.textContent = `knowledge chars=${context.knowledge_chars || 0}`;
  debugSemanticResult.textContent = JSON.stringify({ mode: context.mode, knowledge_chars: context.knowledge_chars, sources: context.sources });
  debugInterpretationSource.textContent = "realtime-llm";
  const source = context.sources?.[0] || {};
  debugSources.textContent = [source.vehicle, source.fault_id, source.source].filter(Boolean).join(" / ") || "-";
}

function ensureMicrophoneAvailable() {
  if (!window.isSecureContext) throw new Error("手機瀏覽器使用麥克風需要 HTTPS。請改用安全連線後再試一次。");
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("此瀏覽器無法使用麥克風。請改用最新版 Chrome 或 Safari。");
}

function stopRealtime(updateStatus = true, preserveError = false) {
  connectionGeneration += 1;
  clearDisconnectTimer();
  realtimeConnected = false;
  if (dataChannel) dataChannel.close();
  if (peerConnection) peerConnection.close();
  if (localStream) localStream.getTracks().forEach((track) => track.stop());
  dataChannel = null; peerConnection = null; localStream = null; activeAiText = ""; activeAiBubble = null;
  stopVoiceButton.disabled = true;
  voiceButton.setAttribute("aria-pressed", "false");
  voiceButton.setAttribute("aria-label", "開始語音");
  voiceButton.disabled = false;
  if (updateStatus) setVoiceState("idle", "realtime stopped");
  else if (!preserveError) setVoiceState("idle");
}

function sendRealtimeEvent(event) { if (dataChannel?.readyState === "open") dataChannel.send(JSON.stringify(event)); }

function isCurrentConnection(generation, peer = peerConnection, channel = dataChannel) {
  return generation === connectionGeneration && peer === peerConnection && (!channel || channel === dataChannel);
}

function clearDisconnectTimer() {
  if (disconnectTimer) window.clearTimeout(disconnectTimer);
  disconnectTimer = null;
}

function scheduleDisconnectFailure(generation, peer) {
  clearDisconnectTimer();
  debugRealtime.textContent = "WebRTC temporarily disconnected; waiting for recovery";
  disconnectTimer = window.setTimeout(() => {
    if (isCurrentConnection(generation, peer) && peer.connectionState === "disconnected") {
      markVoiceFatal("語音連線中斷", "點一下語音核心重新連線。", "WebRTC disconnected for 5 seconds");
    }
  }, 5000);
}

function markVoiceFatal(title, message, detail) {
  console.error("Fatal Realtime voice error", detail);
  showNotice(title, message, detail, "voice");
  setVoiceState("error", detail);
  stopRealtime(false, true);
}

function isFatalRealtimeError(payload, peer = peerConnection, channel = dataChannel) {
  const error = payload?.error || {};
  const code = String(error.code || "").toLowerCase();
  const type = String(error.type || "").toLowerCase();
  if (FATAL_REALTIME_ERROR_CODES.has(code) || FATAL_REALTIME_ERROR_CODES.has(type)) return true;
  if (["failed", "closed"].includes(peer?.connectionState)) return true;
  if (channel && ["closing", "closed"].includes(channel.readyState)) return true;
  return false;
}

async function handleRealtimeEvent(event, generation = connectionGeneration, peer = peerConnection, channel = dataChannel) {
  if (!isCurrentConnection(generation, peer, channel)) return;
  let payload;
  try { payload = JSON.parse(event.data); } catch { return; }
  if (payload.type === "error") {
    const error = payload.error || {};
    const detail = [error.type, error.code, error.message, error.event_id].filter(Boolean).join(" | ") || "Realtime error";
    if (isFatalRealtimeError(payload, peer, channel)) {
      markVoiceFatal("語音發生問題", "點一下語音核心重新連線。", detail);
    } else {
      console.warn("Recoverable Realtime event error", payload);
      debugRealtime.textContent = `recoverable error: ${detail}`;
    }
    return;
  }
  if (payload.type === "session.updated") { setVoiceState("listening", "realtime context loaded"); return; }
  if (payload.type === "input_audio_buffer.speech_started") { setVoiceState("listening", "speech started"); return; }
  if (payload.type === "input_audio_buffer.speech_stopped" || payload.type === "response.created") setVoiceState("thinking", payload.type);

  const userTranscript = extractUserTranscript(payload);
  if (userTranscript) {
    debugRawUserText.textContent = userTranscript;
    addMessage("user", userTranscript);
    setVoiceState("thinking", "user transcript completed");
    updateRealtimeRouting(userTranscript, generation, peer, channel);
    return;
  }
  const aiDelta = extractAiTranscriptDelta(payload);
  if (aiDelta) {
    activeAiText += aiDelta;
    if (!activeAiBubble) activeAiBubble = addMessage("ai", "", { streaming: true });
    activeAiBubble.textContent = activeAiText;
    taskTitle.textContent = activeAiText;
    taskSupport.textContent = "AI 正在回答…";
    debugParsed.textContent = activeAiText;
    setVoiceState("speaking", "audio transcript streaming");
    return;
  }
  const aiDone = extractAiTranscriptDone(payload);
  if (aiDone) {
    if (activeAiBubble) { activeAiBubble.textContent = aiDone; activeAiBubble.closest(".history-message")?.classList.remove("streaming"); }
    else addMessage("ai", aiDone);
    activeAiText = ""; activeAiBubble = null; updateCurrentTask(aiDone);
    return;
  }
  if (payload.type === "response.done") setVoiceState("listening", "response completed; awaiting user");
}

async function updateRealtimeRouting(message, generation, peer, channel) {
  try {
    const response = await fetch("/api/realtime/route", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, routing: realtimeRouting }) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `routing failed: ${response.status}`);
    if (!isCurrentConnection(generation, peer, channel)) return;
    realtimeRouting = payload.routing || {};
    if (payload.session) sendRealtimeEvent({ type: "session.update", session: payload.session });
    updateRealtimeContextDebug({ ...payload.session, mode: "realtime-routing", knowledge_chars: payload.routing?.vehicle && payload.routing?.fault_id ? "selected" : 0, sources: [] });
    debugRealtime.textContent = `routing: ${JSON.stringify(payload.routing)}`;
  } catch (error) {
    console.warn("Realtime routing update failed", error);
    debugRealtime.textContent = `routing update failed: ${error.message}`;
  }
}

function extractUserTranscript(payload) {
  if ((payload.type === "conversation.item.input_audio_transcription.completed" || payload.type === "input_audio_transcription.completed") && payload.transcript) return payload.transcript.trim();
  return "";
}
function extractAiTranscriptDelta(payload) {
  if ((payload.type === "response.output_audio_transcript.delta" || payload.type === "response.audio_transcript.delta") && payload.delta) return payload.delta;
  return "";
}
function extractAiTranscriptDone(payload) {
  if ((payload.type === "response.output_audio_transcript.done" || payload.type === "response.audio_transcript.done") && payload.transcript) return payload.transcript.trim();
  return "";
}
