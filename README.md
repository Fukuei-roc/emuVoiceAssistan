# EMU800 AI 故障處理助手 MVP

這是一個最小可執行 MVP，用來打通：

手機 / 瀏覽器 → 語音 AI → Markdown 知識 → 故障引導 → 語音回答

目前預設車型載入 `EMU800`，知識來源由 `settings/knowledge_sources.json` 管理。第一階段文字聊天可獨立運作；Realtime 語音功能需要 OpenAI Realtime API 可用的模型與 API key。

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
OPENAI_TEXT_MODEL=gpt-4.1-mini
OPENAI_REALTIME_MODEL=gpt-realtime
```

啟動：

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

開啟：

```text
http://localhost:8000
```

## API

健康檢查：

```text
GET /health
```

搜尋知識：

```text
GET /api/search?q=VCB不閉合
```

文字聊天：

```text
POST /api/chat
```

```json
{
  "session_id": "optional",
  "message": "VCB不閉合"
}
```

Realtime session：

```text
POST /api/realtime/session
```

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

所以同一個 Wi-Fi 下用手機開 `http://電腦IP:8000` 時，文字功能可以正常，但語音按鈕會因為不是 HTTPS 而無法取得麥克風。手機語音測試需要改用 HTTPS、本機受信任憑證，或 tunnel 服務。

## 如何修改知識

直接編輯：

```text
knowledge/EMU800/EMU800_故障處理流程_AI整理版.md
```

重新載入：

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/knowledge/reload
```

不需要 embedding、vector DB rebuild 或 YAML conversion。MVP 會重新解析 Markdown heading，並重建 SQLite FTS5 index。

## 知識檔設定

知識文件路徑集中在：

```text
settings/knowledge_sources.json
```

目前內容：

```json
{
  "default_vehicle": "EMU800",
  "vehicles": [
    {
      "vehicle": "EMU800",
      "documents": [
        "knowledge/EMU800/EMU800_故障處理流程_AI整理版.md"
      ]
    }
  ]
}
```

若要移動 Markdown 或加入同車型第二份文件，只要改這個 settings 檔，然後重啟 server 或呼叫 `/api/knowledge/reload`。

## 如何未來增加車型

建立新資料夾，例如：

```text
knowledge/EMU900/
```

再把文件加入 `settings/knowledge_sources.json`：

```json
{
  "vehicle": "EMU900",
  "documents": [
    "knowledge/EMU900/EMU900_故障處理流程.md",
    "knowledge/EMU900/EMU900_補充說明.md"
  ]
}
```

目前 MVP 一次只載入 `default_vehicle` / `VEHICLE` 指定的車型；未來可以再加 UI 車型切換或自動掃描。

## 測試

```powershell
pytest
```

測試涵蓋 Markdown 解析、VCB/SIV/不鬆軔搜尋，以及 `/health`、`/api/search`、`/api/knowledge/status`。
