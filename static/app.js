const chat = document.getElementById("chat");
const form = document.getElementById("chatForm");
const input = document.getElementById("messageInput");
const voiceButton = document.getElementById("voiceButton");
const voiceStatus = document.getElementById("voiceStatus");
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

function updateDebugState(data) {
  debugSession.textContent = data.session_id || sessionId || "-";
  const source = data.sources?.[0] || {};
  const parts = [data.vehicle || source.vehicle, data.fault_id || source.fault_id, source.source].filter(Boolean);
  debugSources.textContent = parts.join(" / ") || "-";
  if (debugExpected) debugExpected.textContent = data.semantic_result?.knowledge_chars ? `knowledge chars=${data.semantic_result.knowledge_chars}` : "LLM-driven";
  if (debugParsed) debugParsed.textContent = data.semantic_result ? JSON.stringify(data.semantic_result) : "-";
  if (debugTurnStatus) debugTurnStatus.textContent = data.last_turn_status || "llm";
  if (debugRawUserText) debugRawUserText.textContent = data.raw_user_text || "-";
  if (debugInterpretationSource) debugInterpretationSource.textContent = data.interpretation_source || "llm";
  if (debugSemanticResult) debugSemanticResult.textContent = data.semantic_result ? JSON.stringify(data.semantic_result) : "-";
}

function addMessage(role, text) {
  const item = document.createElement("div");
  item.className = `message ${role === "user" ? "user" : "ai"}`;
  const speaker = document.createElement("div");
  speaker.className = "speaker";
  speaker.textContent = role === "user" ? "你" : "AI";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  item.append(speaker, bubble);
  chat.appendChild(item);
  chat.scrollTop = chat.scrollHeight;
  return bubble;
}

function setRealtimeStatus(text) {
  voiceStatus.textContent = text;
  debugRealtime.textContent = text;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  input.focus();
  addMessage("user", message);
  const startedAt = performance.now();
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "文字對話失敗");
    sessionId = data.session_id;
    addMessage("ai", data.reply);
    updateDebugState(data);
    debugLatency.textContent = `${data.latency_ms || Math.round(performance.now() - startedAt)} ms`;
  } catch (error) {
    addMessage("ai", error.message);
    debugLatency.textContent = `${Math.round(performance.now() - startedAt)} ms`;
  }
});

voiceButton.addEventListener("click", async () => {
  if (realtimeConnected) {
    stopRealtime();
    return;
  }
  await startRealtime();
});

async function startRealtime() {
  voiceButton.disabled = true;
  setRealtimeStatus("準備麥克風...");
  try {
    ensureMicrophoneAvailable();
    if (!sessionId) sessionId = `realtime-${crypto.randomUUID()}`;
    unlockAudioElement();

    setRealtimeStatus("請允許瀏覽器使用麥克風...");
    localStream = await getMicrophoneStream();
    setRealtimeStatus("麥克風已啟用，載入 YAML context...");
    const context = await fetch("/api/realtime/context").then((response) => response.json());
    updateRealtimeContextDebug(context);

    peerConnection = new RTCPeerConnection();
    peerConnection.onconnectionstatechange = () => {
      if (debugRealtimeConnection) debugRealtimeConnection.textContent = peerConnection.connectionState;
      console.info("REALTIME AUDIO connection state", peerConnection.connectionState);
    };
    peerConnection.ontrack = async (event) => {
      console.info("REALTIME AUDIO remote track received", event.track.kind);
      if (debugRealtimeAudio) debugRealtimeAudio.textContent = `remote track: ${event.track.kind}`;
      remoteAudio.srcObject = event.streams[0];
      remoteAudio.autoplay = true;
      remoteAudio.playsInline = true;
      try {
        await remoteAudio.play();
        if (debugRealtimeAudio) debugRealtimeAudio.textContent = "audio playback started";
      } catch (error) {
        if (debugRealtimeAudio) debugRealtimeAudio.textContent = `audio playback failed: ${error.message}`;
      }
    };

    localStream.getTracks().forEach((track) => peerConnection.addTrack(track, localStream));

    dataChannel = peerConnection.createDataChannel("oai-events");
    dataChannel.addEventListener("open", () => {
      setRealtimeStatus("Realtime 已連線，LLM 讀取完整 YAML 中");
      sendRealtimeEvent({ type: "session.update", session: context.session });
      requestInitialRealtimeResponse();
    });
    dataChannel.addEventListener("message", handleRealtimeEvent);

    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    const localSdp = peerConnection.localDescription?.sdp || offer.sdp || "";
    if (!localSdp.trim()) throw new Error("瀏覽器產生的 SDP offer 是空的");
    setRealtimeStatus(`交換 WebRTC SDP... offer length=${localSdp.length}`);
    const sdpResponse = await fetch("/api/realtime/call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sdp: localSdp }),
    });
    const sdpPayload = await sdpResponse.json();
    if (!sdpResponse.ok) throw new Error(sdpPayload.detail || `WebRTC SDP 交換失敗：${sdpResponse.status}`);
    await peerConnection.setRemoteDescription({ type: "answer", sdp: sdpPayload.sdp });

    realtimeConnected = true;
    voiceButton.textContent = "停止語音";
    voiceButton.classList.add("connected");
  } catch (error) {
    setRealtimeStatus(error.message);
    stopRealtime(false);
  } finally {
    voiceButton.disabled = false;
  }
}

function unlockAudioElement() {
  remoteAudio.autoplay = true;
  remoteAudio.playsInline = true;
  remoteAudio.muted = false;
  // Do not await play() before a MediaStream is attached. On some mobile
  // browsers that promise stays pending forever and blocks getUserMedia().
  const playPromise = remoteAudio.play();
  if (playPromise && typeof playPromise.catch === "function") {
    playPromise.catch(() => {
      if (debugRealtimeAudio) debugRealtimeAudio.textContent = "audio unlocked after remote track";
    });
  }
}

async function getMicrophoneStream() {
  const request = navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const timeout = new Promise((_, reject) => {
    window.setTimeout(() => reject(new Error("麥克風授權逾時。請確認瀏覽器是否跳出允許麥克風的提示，並允許此網站使用麥克風。")), 15000);
  });
  try {
    return await Promise.race([request, timeout]);
  } catch (error) {
    if (error && error.name === "NotAllowedError") {
      throw new Error("麥克風權限被拒絕。請在瀏覽器網站設定允許麥克風後再試一次。");
    }
    if (error && error.name === "NotFoundError") {
      throw new Error("找不到可用麥克風。請確認裝置有麥克風並未被其他程式占用。");
    }
    throw error;
  }
}

function updateRealtimeContextDebug(context) {
  if (debugExpected) debugExpected.textContent = `knowledge chars=${context.knowledge_chars || 0}`;
  if (debugSemanticResult) debugSemanticResult.textContent = JSON.stringify({ mode: context.mode, knowledge_chars: context.knowledge_chars, sources: context.sources });
  if (debugInterpretationSource) debugInterpretationSource.textContent = "realtime-llm";
  const source = context.sources?.[0] || {};
  debugSources.textContent = [source.vehicle, source.fault_id, source.source].filter(Boolean).join(" / ") || "-";
}

function requestInitialRealtimeResponse() {
  if (debugRealtimeAudio) debugRealtimeAudio.textContent = "response audio requested";
  sendRealtimeEvent({
    type: "response.create",
    response: {
      modalities: ["audio", "text"],
      instructions: "請用一句話開始教學對話：請司機員說明目前遇到的故障。只問這一題，說完就停。",
    },
  });
}

function ensureMicrophoneAvailable() {
  if (!window.isSecureContext) {
    throw new Error("手機瀏覽器使用麥克風需要 HTTPS；http://電腦IP:8000 不是安全來源。請改用 HTTPS/tunnel，或只在本機 localhost 測試語音。");
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("此瀏覽器未提供 getUserMedia。請確認使用 Chrome/Safari，並以 HTTPS 或 localhost 開啟。");
  }
}

function stopRealtime(updateStatus = true) {
  if (dataChannel) dataChannel.close();
  if (peerConnection) peerConnection.close();
  if (localStream) localStream.getTracks().forEach((track) => track.stop());
  dataChannel = null;
  peerConnection = null;
  localStream = null;
  realtimeConnected = false;
  activeAiText = "";
  voiceButton.textContent = "開始語音";
  voiceButton.classList.remove("connected");
  voiceButton.disabled = false;
  if (updateStatus) setRealtimeStatus("Realtime 已停止");
}

function sendRealtimeEvent(event) {
  if (dataChannel && dataChannel.readyState === "open") {
    dataChannel.send(JSON.stringify(event));
  }
}

async function handleRealtimeEvent(event) {
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return;
  }
  if (payload.type === "error") {
    setRealtimeStatus(payload.error?.message || "Realtime error");
    return;
  }
  if (payload.type === "session.updated") {
    setRealtimeStatus("Realtime YAML context 已載入");
    return;
  }
  const userTranscript = extractUserTranscript(payload);
  if (userTranscript) {
    if (debugRawUserText) debugRawUserText.textContent = userTranscript;
    addMessage("user", userTranscript);
    return;
  }
  const aiDelta = extractAiTranscriptDelta(payload);
  if (aiDelta) {
    activeAiText += aiDelta;
    if (debugParsed) debugParsed.textContent = activeAiText;
    return;
  }
  const aiDone = extractAiTranscriptDone(payload);
  if (aiDone) {
    addMessage("ai", aiDone);
    activeAiText = "";
    return;
  }
  if (payload.type === "response.done") {
    setRealtimeStatus("等待使用者回答");
  }
}

function extractUserTranscript(payload) {
  if ((payload.type === "conversation.item.input_audio_transcription.completed" || payload.type === "input_audio_transcription.completed") && payload.transcript) {
    return payload.transcript.trim();
  }
  return "";
}

function extractAiTranscriptDelta(payload) {
  if ((payload.type === "response.output_audio_transcript.delta" || payload.type === "response.audio_transcript.delta") && payload.delta) {
    return payload.delta;
  }
  return "";
}

function extractAiTranscriptDone(payload) {
  if ((payload.type === "response.output_audio_transcript.done" || payload.type === "response.audio_transcript.done") && payload.transcript) {
    return payload.transcript.trim();
  }
  return "";
}



