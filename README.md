# EMU AI 故障處理助手 MVP

這是一個教學示範 MVP，用來打通：

```text
手機 / 瀏覽器
→ OpenAI Realtime
→ 車型 / 故障辨識
→ 載入對應 YAML
→ 完整 YAML 提供給 LLM
→ LLM 依 YAML 逐步故障引導
→ Realtime 語音回答
```

核心原則：YAML 提供故障處理流程與技術依據；LLM 負責理解使用者、維持對話進度、判斷分支與決定下一個問題。系統透過 prompt 約束 LLM：一次只問一個問題，取得明確回覆後才繼續下一步。

這不是正式上線的安全關鍵決策系統，優先目標是自然、靈活、低延遲、適合語音展示。

目前已載入：

```text
EMU800 / VCB不閉合
```

## 快速開始

```powershell
cd D:\python\emuVoiceAssistan
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

編輯 `.env`：

```text
OPENAI_API_KEY=
```

`.env` 只保存敏感資訊。可納入版本控制的一般應用程式設定位於
`settings/application.toml`，其中包含文字模型、Realtime 模型與
`server_vad` threshold。

啟動：

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

開啟：

```text
http://localhost:8000
```

## Docker Compose（Ubuntu VPS）

先建立環境變數檔並填入 OpenAI API key：

```bash
cp .env.example .env
```

建置並啟動：

```bash
docker compose up -d --build
```

檢查狀態與 log：

```bash
docker compose ps
docker compose logs -f
```

停止服務：

```bash
docker compose down
```

對外使用 `8000` port。Compose 使用 named volume 保存 `/app/data`，且不會把 `.env` 或 OpenAI API key 寫入 image；`settings/` 內的非敏感設定會包含在 image 中。

## Knowledge

正式故障流程 YAML 放在：

```text
knowledge/EMU800/faults/vcb_not_close.yaml
```

設定檔：

```text
settings/application.toml
settings/knowledge_sources.json
```

目前格式：

```json
{
  "vehicles": [
    {
      "vehicle": "EMU800",
      "faults": [
        {
          "id": "vcb_not_close",
          "name": "VCB不閉合",
          "file": "knowledge/EMU800/faults/vcb_not_close.yaml"
        }
      ]
    }
  ]
}
```

Python 仍負責載入 registry 與選擇可用 YAML，避免把不存在的車型資料拿來回答。但選到 YAML 後，故障流程分支與下一步由 LLM 根據完整 YAML 與對話歷史自行判斷。

## 對話模式

Realtime 會把完整 YAML 注入 session instructions。LLM 會自然理解：

```text
八百新 / 八百線 → 八百型 / EMU800
VCP不閉合 / VCB不閉和 / 斷路器合不起來 → VCB不閉合
二十五K → 25kV
兩個燈都有亮 → 有 / 正常
```

但如果使用者提到未載入車型，例如 EMU700，而目前沒有 EMU700 knowledge，LLM 應回答目前沒有載入該資料，不應使用 EMU800 流程替代。

## Realtime

Realtime 主要路徑：

```text
Browser microphone
→ OpenAI Realtime API
→ LLM 讀完整 YAML + conversation history
→ LLM 自行判斷下一題
→ Realtime audio output
→ browser <audio>
```

前端會：

```text
1. 建立 WebRTC
2. 從 /api/realtime/context 取得完整 YAML instructions
3. session.update 注入 context
4. response.create 要求第一句開場
5. 之後由 Realtime VAD 自動 create_response
6. pc.ontrack 綁定 remoteAudio.srcObject 並呼叫 play()
```

Debug panel 顯示：

```text
realtime connection
vehicle / fault / knowledge file
knowledge chars
last user transcript
last AI transcript
audio state
```

## API

健康檢查：

```text
GET /health
```

搜尋流程：

```text
GET /api/search?q=VCB
```

文字聊天，使用同一份完整 YAML prompt 與 conversation history：

```text
POST /api/chat
```

Realtime context：

```text
GET /api/realtime/context
```

Realtime session：

```text
POST /api/realtime/session
```

Knowledge status：

```text
GET /api/knowledge/status
```

Knowledge reload：

```text
POST /api/knowledge/reload
```

## 如何修改知識

直接編輯：

```text
knowledge/EMU800/faults/vcb_not_close.yaml
```

重新載入：

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/knowledge/reload
```

不需要 embedding、vector DB、Markdown RAG 或 Python branch rebuild。

## 如何增加車型或故障

新增 YAML，例如：

```text
knowledge/EMU900/faults/vcb_not_close.yaml
knowledge/EMU800/faults/no_traction.yaml
```

再在 `settings/knowledge_sources.json` 加入對應 vehicle/fault/file。不同車型的同名故障是不同 YAML manual。

## 手機語音測試注意事項

瀏覽器的麥克風 API `getUserMedia` 只能在安全來源使用。

可用：

```text
http://localhost:8000
https://...
```

不可用：

```text
http://電腦IP:8000
```

同一個 Wi-Fi 下手機開 `http://電腦IP:8000` 時，文字功能可以正常，但語音按鈕會因為不是 HTTPS 而無法取得麥克風。手機語音測試需要 HTTPS、本機受信任憑證，或 tunnel 服務。

## 人工語音驗收

期望流程：

```text
AI：請說明目前遇到的故障。
User：VCB合不起來。
AI：請問是哪一型車？
User：八百新。
AI：VCB不閉合是在升弓後發生，還是行駛中發生？
User：升弓後。
AI：請看一下電車線電壓是多少？
User：25K。
AI：好，請確認VCBOTR下方兩個指示燈有沒有亮？
```

重點是每次一題、短句、自然、等待回答。

## 測試

```powershell
pytest
```

測試涵蓋 YAML 載入、knowledge routing、Realtime prompt 包含完整 YAML、一次只問一題規則、audio output 設定、前端 remote audio playback，以及 Text Chat conversation history。
