from __future__ import annotations

import logging
import re

import httpx

from app.config import settings
from app.llm_troubleshooting import LLMTroubleshootingService
from app.troubleshooting import FaultRegistry

logger = logging.getLogger(__name__)

REALTIME_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"


class RealtimeService:
    def __init__(self, registry: FaultRegistry) -> None:
        self.registry = registry
        self.llm = LLMTroubleshootingService(registry)

    def session_config(self, vehicle: str | None = None, fault_id: str | None = None) -> dict:
        return {
            "type": "realtime",
            "model": settings.openai_realtime_model,
            "instructions": self.llm.build_instructions(vehicle=vehicle, fault_id=fault_id),
            "tools": [],
            "tool_choice": "none",
            "audio": {
                "input": {
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": settings.openai_realtime_vad_threshold,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 650,
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {"voice": "alloy"},
            },
        }

    async def create_session(self) -> dict:
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key 尚未設定")
        payload = {"expires_after": {"anchor": "created_at", "seconds": 600}, "session": self.session_config()}
        headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(REALTIME_CLIENT_SECRETS_URL, headers=headers, json=payload)
        if response.status_code >= 400:
            logger.error("Realtime session creation failed status=%s", response.status_code)
            if response.status_code == 401:
                raise RuntimeError("OpenAI API key 無效或已被拒絕")
            raise RuntimeError(f"Realtime session 建立失敗：HTTP {response.status_code}")
        logger.info("Realtime session created llm_driven knowledge_chars=%s", self.llm.build_context().chars)
        return response.json()

    async def create_call(self, sdp: str) -> dict:
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key 尚未設定")
        if not sdp.strip():
            raise RuntimeError("SDP offer 不可為空")
        sdp = self._normalize_sdp(sdp)
        headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/sdp"}
        url = f"{REALTIME_CALLS_URL}?model={settings.openai_realtime_model}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, content=sdp.encode("utf-8"))
        if response.status_code >= 400:
            error_text = self._safe_error_text(response.text)
            logger.error("Realtime call creation failed status=%s body=%s", response.status_code, error_text[:500])
            if response.status_code == 401:
                raise RuntimeError("OpenAI API key 無效或已被拒絕")
            raise RuntimeError(f"WebRTC SDP 交換失敗：HTTP {response.status_code} (server_received_sdp_length={len(sdp)}) {error_text[:500]}")
        logger.info("Realtime call created sdp_length=%s llm_driven=true", len(sdp))
        return {"sdp": response.text, "location": response.headers.get("Location")}

    def context_payload(self, vehicle: str | None = None, fault_id: str | None = None) -> dict:
        context_obj = self.llm.build_context(vehicle=vehicle, fault_id=fault_id)
        context = {
            "mode": "llm_driven",
            "knowledge_loaded": bool(context_obj.chars),
            "knowledge_chars": context_obj.chars,
            "sources": [source.model_dump() for source in context_obj.sources],
        }
        context["instructions"] = self.llm.build_instructions(vehicle=vehicle, fault_id=fault_id)
        context["session"] = self.session_config(vehicle=vehicle, fault_id=fault_id)
        return context

    def route_message(self, message: str, current: dict | None = None) -> dict:
        from app.semantic import extract_routing

        info = extract_routing(message, self.registry.available_vehicles(), self.registry.available_faults())
        prior = current or {}
        routing = {
            "vehicle": info.vehicle or prior.get("vehicle"),
            "car_number": info.car_number or prior.get("car_number"),
            "train_number": info.train_number or prior.get("train_number"),
            "fault_id": info.fault_id or prior.get("fault_id"),
        }
        return {
            "routing": routing,
            "session": self.session_config_with_state(routing),
        }

    def session_config_with_state(self, routing: dict[str, str | None]) -> dict:
        config = self.session_config(vehicle=routing.get("vehicle"), fault_id=routing.get("fault_id"))
        config["instructions"] = self.llm.build_instructions(
            vehicle=routing.get("vehicle"),
            fault_id=routing.get("fault_id"),
            train_number=routing.get("train_number"),
            car_number=routing.get("car_number"),
        )
        return config

    def _safe_error_text(self, text: str) -> str:
        return re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", text or "").strip()

    def _normalize_sdp(self, sdp: str) -> str:
        normalized = sdp.replace("\r\n", "\n").replace("\r", "\n")
        normalized = "\r\n".join(normalized.split("\n"))
        if not normalized.endswith("\r\n"):
            normalized += "\r\n"
        return normalized
