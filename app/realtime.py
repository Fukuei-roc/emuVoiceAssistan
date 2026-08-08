from __future__ import annotations

import logging
import json
import re

import httpx

from app.config import settings
from app.knowledge import MarkdownKnowledgeBase

logger = logging.getLogger(__name__)

REALTIME_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"


REALTIME_INSTRUCTIONS = """你是 EMU800 型電聯車故障處理訓練助手。回答要適合語音播放，簡短直接。
在回答任何故障處置、設備操作、數值門檻或下一步確認前，必須先呼叫 searchKnowledge 工具。
只能根據 searchKnowledge 回傳的 EMU800 手冊內容回答；不得使用常識、推測、其他車型經驗或未提供的手冊內容。
如果 tool 結果不足以決定下一步，直接說目前資料不足，並只問一個必要的釐清問題。
若 tool 回傳多個章節，優先使用最符合目前故障與目前對話的章節，不要混用不相關章節。
不得自行發明或簡化強迫激磁、隔離、SIV轉供、降弓、KEY OFF、考克等操作。
一次只能提出一個問句；不要把多個確認項目合併在同一則回答。
若使用者只回答數值、狀態或短語，必須先判斷它是否是在回答上一輪問題；例如上一輪詢問電車線電壓時，使用者回答「25KV」就是電車線電壓為 25 kV。
若已能判斷使用者回答落在手冊正常範圍或異常範圍，直接依手冊進入下一個步驟，不要重複詢問同一問題。"""


SEARCH_KNOWLEDGE_TOOL = {
    "type": "function",
    "name": "searchKnowledge",
    "description": "Search EMU800 troubleshooting Markdown knowledge.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "故障現象或關鍵字，例如 VCB不閉合"}
        },
        "required": ["query"],
    },
}


class RealtimeService:
    def __init__(self, knowledge: MarkdownKnowledgeBase) -> None:
        self.knowledge = knowledge

    async def create_session(self) -> dict:
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key 尚未設定")

        payload = {
            "expires_after": {"anchor": "created_at", "seconds": 600},
            "session": {
                "type": "realtime",
                "model": settings.openai_realtime_model,
                "instructions": REALTIME_INSTRUCTIONS,
                "tools": [SEARCH_KNOWLEDGE_TOOL],
                "audio": {
                    "output": {
                        "voice": "alloy",
                    }
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(REALTIME_CLIENT_SECRETS_URL, headers=headers, json=payload)
        if response.status_code >= 400:
            logger.error("Realtime session creation failed status=%s", response.status_code)
            if response.status_code == 401:
                raise RuntimeError("OpenAI API key 無效或已被拒絕")
            raise RuntimeError(f"Realtime session 建立失敗：HTTP {response.status_code}")
        logger.info("Realtime session created")
        return response.json()

    async def create_call(self, sdp: str) -> dict:
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key 尚未設定")
        if not sdp.strip():
            raise RuntimeError("SDP offer 不可為空")
        sdp = self._normalize_sdp(sdp)

        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/sdp",
        }
        url = f"{REALTIME_CALLS_URL}?model={settings.openai_realtime_model}"

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, content=sdp.encode("utf-8"))
        if response.status_code >= 400:
            error_text = self._safe_error_text(response.text)
            logger.error("Realtime call creation failed status=%s body=%s", response.status_code, error_text[:500])
            if response.status_code == 401:
                raise RuntimeError("OpenAI API key 無效或已被拒絕")
            raise RuntimeError(
                f"WebRTC SDP 交換失敗：HTTP {response.status_code} "
                f"(server_received_sdp_length={len(sdp)}) {error_text[:500]}"
            )

        logger.info("Realtime call created sdp_length=%s", len(sdp))
        return {
            "sdp": response.text,
            "location": response.headers.get("Location"),
        }

    def search_knowledge(self, query: str) -> dict:
        results = self.knowledge.search(query, limit=5)
        return {
            "vehicle": settings.vehicle,
            "results": [result.model_dump() for result in results],
        }

    def _session_config(self) -> dict:
        return {
            "type": "realtime",
            "model": settings.openai_realtime_model,
            "instructions": REALTIME_INSTRUCTIONS,
            "tools": [SEARCH_KNOWLEDGE_TOOL],
            "audio": {
                "output": {
                    "voice": "alloy",
                }
            },
        }

    def _safe_error_text(self, text: str) -> str:
        return re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", text or "").strip()

    def _normalize_sdp(self, sdp: str) -> str:
        normalized = sdp.replace("\r\n", "\n").replace("\r", "\n")
        normalized = "\r\n".join(normalized.split("\n"))
        if not normalized.endswith("\r\n"):
            normalized += "\r\n"
        return normalized
