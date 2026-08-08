const chat = document.getElementById("chat");
const form = document.getElementById("chatForm");
const input = document.getElementById("messageInput");
const voiceButton = document.getElementById("voiceButton");
const voiceStatus = document.getElementById("voiceStatus");
const remoteAudio = document.getElementById("remoteAudio");

const debugSession = document.getElementById("debugSession");
const debugSources = document.getElementById("debugSources");
const debugLatency = document.getElementById("debugLatency");
const debugRealtime = document.getElementById("debugRealtime");

let sessionId = null;
let peerConnection = null;
let dataChannel = null;
let localStream = null;
let realtimeConnected = false;
let realtimeResponseActive = false;
let queuedRealtimeResponse = null;

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
    if (!response.ok) {
      throw new Error(data.detail || "文字對話失敗");
    }
    sessionId = data.session_id;
    addMessage("ai", data.reply);
    debugSession.textContent = sessionId;
    debugSources.textContent = (data.sources || []).map((item) => item.heading).join(" / ") || "-";
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
    peerConnection = new RTCPeerConnection();
    peerConnection.ontrack = (event) => {
      remoteAudio.srcObject = event.streams[0];
    };

    localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    localStream.getTracks().forEach((track) => peerConnection.addTrack(track, localStream));

    dataChannel = peerConnection.createDataChannel("oai-events");
    dataChannel.addEventListener("open", () => {
      setRealtimeStatus("Realtime 已連線");
      sendRealtimeEvent({
        type: "session.update",
        session: {
          type: "realtime",
          instructions:
            "你是 EMU800 型電聯車故障處理訓練助手。回答要適合語音播放，簡短直接。在回答任何故障處置、設備操作、數值門檻或下一步確認前，必須先呼叫 searchKnowledge 工具。只能根據 searchKnowledge 回傳的 EMU800 手冊內容回答；不得使用常識、推測、其他車型經驗或未提供的手冊內容。如果 tool 結果不足以決定下一步，直接說目前資料不足，並只問一個必要的釐清問題。若 tool 回傳多個章節，優先使用最符合目前故障與目前對話的章節，不要混用不相關章節。不得自行發明或簡化強迫激磁、隔離、SIV轉供、降弓、KEY OFF、考克等操作。一次只能提出一個問句；不要把多個確認項目合併在同一則回答。若使用者只回答數值、狀態或短語，必須先判斷它是否是在回答上一輪問題；例如上一輪詢問電車線電壓時，使用者回答「25KV」就是電車線電壓為 25 kV。若已能判斷使用者回答落在手冊正常範圍或異常範圍，直接依手冊進入下一個步驟，不要重複詢問同一問題。",
          tools: [
            {
              type: "function",
              name: "searchKnowledge",
              description: "Search EMU800 troubleshooting Markdown knowledge.",
              parameters: {
                type: "object",
                properties: {
                  query: { type: "string", description: "故障現象或關鍵字，例如 VCB不閉合" },
                },
                required: ["query"],
              },
            },
          ],
        },
      });
      requestRealtimeResponse({
        modalities: ["audio", "text"],
        instructions: "請用繁體中文先詢問使用者目前遇到的故障。",
      });
    });
    dataChannel.addEventListener("message", handleRealtimeEvent);

    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    const localSdp = peerConnection.localDescription?.sdp || offer.sdp || "";
    if (!localSdp.trim()) {
      throw new Error("瀏覽器產生的 SDP offer 是空的");
    }
    setRealtimeStatus(`交換 WebRTC SDP... offer length=${localSdp.length}`);

    const sdpResponse = await fetch("/api/realtime/call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sdp: localSdp }),
    });
    const sdpPayload = await sdpResponse.json();
    if (!sdpResponse.ok) {
      throw new Error(sdpPayload.detail || `WebRTC SDP 交換失敗：${sdpResponse.status}`);
    }
    const answer = { type: "answer", sdp: sdpPayload.sdp };
    await peerConnection.setRemoteDescription(answer);

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
  realtimeResponseActive = false;
  queuedRealtimeResponse = null;
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

function requestRealtimeResponse(response = null) {
  if (realtimeResponseActive) {
    queuedRealtimeResponse = response || {};
    return;
  }
  realtimeResponseActive = true;
  const event = { type: "response.create" };
  if (response) event.response = response;
  sendRealtimeEvent(event);
}

async function handleRealtimeEvent(event) {
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return;
  }

  if (payload.type === "error") {
    if ((payload.error?.message || "").includes("active response in progress")) {
      queuedRealtimeResponse = queuedRealtimeResponse || {};
      setRealtimeStatus("Realtime 正在回答，已等待目前回答完成");
      return;
    }
    setRealtimeStatus(payload.error?.message || "Realtime error");
    return;
  }

  if (payload.type === "response.created") {
    realtimeResponseActive = true;
  }

  if (payload.type === "response.done") {
    realtimeResponseActive = false;
    if (queuedRealtimeResponse) {
      const nextResponse = queuedRealtimeResponse;
      queuedRealtimeResponse = null;
      requestRealtimeResponse(nextResponse);
    }
  }

  const textDelta = payload.delta || payload.text;
  if (payload.type === "response.text.delta" && textDelta) {
    debugRealtime.textContent = `text: ${textDelta}`;
  }

  const item = payload.item;
  if (item && item.type === "function_call" && item.name === "searchKnowledge" && item.call_id) {
    let args = {};
    try {
      args = JSON.parse(item.arguments || "{}");
    } catch {
      args = {};
    }
    const result = await fetch("/api/realtime/searchKnowledge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: args.query || "" }),
    }).then((response) => response.json());

    debugSources.textContent = (result.results || []).map((section) => section.heading).join(" / ") || "-";
    sendRealtimeEvent({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: item.call_id,
        output: JSON.stringify(result),
      },
    });
    requestRealtimeResponse();
  }
}
