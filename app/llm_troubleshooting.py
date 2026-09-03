from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.config import settings
from app.models import ChatRequest, ChatResponse, ConversationSession, KnowledgeSearchResult
from app.semantic import extract_routing
from app.troubleshooting import FaultRegistry, strip_markdown_fence

logger = logging.getLogger(__name__)

LLM_TROUBLESHOOTING_PROMPT = """你是台鐵 EMU 電聯車故障處理訓練助手。

這是一個教學示範系統，不是正式上線的安全關鍵系統。

YAML 提供故障處理流程與技術依據。你負責閱讀 YAML、理解使用者回答、維持對話進度、判斷分支，並依照 YAML 一步一步引導故障排除。

重要規則：
1. 必須優先依照 YAML 流程。
2. 不要自行發明 YAML 沒有記載的故障處置。
3. 一次只問一個問題。
4. 問題要簡短、明確，適合語音對話。
5. 問完問題後立刻停止，等待使用者回答。
6. 在取得使用者回答前，不要自己繼續下一步。
7. 收到回答後，先理解使用者真正的意思，不要求使用者使用固定關鍵字。
8. 可以容忍口語、錯字、語音辨識錯誤、同義說法。例如八百新/八百線通常可能是八百型，VCP不閉合可能是VCB不閉合。
9. 只有在你認為已經取得足以判斷目前步驟的資訊後，才進到下一步。
10. 如果回答不足以判斷，只針對目前這一個問題追問。
11. 不要一次把後續多個檢查項目全部念出來。
12. 不要預告下一步。
13. 不要用條列式一次列出整個流程。
14. 如果使用者回答不知道、不確定、沒看到，就針對目前問題提供簡短確認提示，仍停留在目前步驟。
15. 如果使用者問目前檢查項目的位置或意思，可以簡短回答，再回到原本問題。
16. 如果使用者明確表示故障已恢復，可以依 YAML 流程判斷是否結束。
17. 每次回答盡量控制在 1 到 2 句。
18. 語氣像現場教官，不要像念手冊。
19. 不需要說明你正在走哪個 node，也不要朗讀 YAML node id。
20. 除非使用者詢問原因，否則不要長篇解釋。
21. 已經由使用者明確回答過的問題，不要重複詢問；請根據完整對話歷史判斷目前已確認哪些條件。
22. 若使用者提到未載入車型或未載入故障，請明確說目前沒有載入該資料，不要拿其他車型或故障流程替代。

目前可用車型與故障流程如下：
{catalog}

以下是目前已載入的完整 YAML 故障處理流程。這些 YAML 是本次故障排除的主要依據。

{knowledge_blocks}
"""


@dataclass
class KnowledgeContext:
    catalog: str
    knowledge_blocks: str
    sources: list[KnowledgeSearchResult]
    chars: int


class LLMTroubleshootingService:
    def __init__(self, registry: FaultRegistry) -> None:
        self.registry = registry
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        self.sessions: dict[str, ConversationSession] = {}

    def build_context(self, vehicle: str | None = None, fault_id: str | None = None) -> KnowledgeContext:
        catalog_lines: list[str] = []
        blocks: list[str] = []
        sources: list[KnowledgeSearchResult] = []
        total_chars = 0
        for registered_vehicle, faults in self.registry.registry.items():
            if not faults:
                continue
            catalog_lines.append(f"- {registered_vehicle}")
            for registered_fault_id, procedure in faults.items():
                fault_name = self.registry.fault_names.get(registered_vehicle, {}).get(registered_fault_id, procedure.symptom)
                catalog_lines.append(f"  - {fault_name} ({registered_fault_id})")
                sources.append(KnowledgeSearchResult(vehicle=registered_vehicle, fault_id=registered_fault_id, title=fault_name, source=procedure.source_file, heading=procedure.title, content=procedure.symptom))
                yaml_text = read_yaml_text(Path(procedure.source_file))
                total_chars += len(yaml_text)
                blocks.append(
                    f"--- YAML START: vehicle={registered_vehicle}, fault={fault_name}, fault_id={registered_fault_id}, file={procedure.source_file} ---\n"
                    f"{yaml_text}\n"
                    f"--- YAML END: vehicle={registered_vehicle}, fault_id={registered_fault_id} ---"
                )
        return KnowledgeContext(
            catalog="\n".join(catalog_lines) if catalog_lines else "目前沒有載入任何車型故障流程。",
            knowledge_blocks="\n\n".join(blocks) if blocks else "目前沒有可用 YAML。",
            sources=sources,
            chars=total_chars,
        )

    def build_instructions(self, vehicle: str | None = None, fault_id: str | None = None, train_number: str | None = None, car_number: str | None = None) -> str:
        context = self.build_context(vehicle=vehicle, fault_id=fault_id)
        if not vehicle:
            routing_note = "目前尚未確認車型；必須先詢問車型，不能選擇或執行任何特定車型故障流程。"
        elif not fault_id:
            routing_note = f"目前已確認車型：{vehicle}；請先確認故障，再開始對應流程。"
        else:
            routing_note = f"目前已確認車型：{vehicle}、故障：{fault_id}；只使用上方 {vehicle} 對應的 YAML。"
        routing_note += " Context 可能同時包含多個車型；不得跨車型混用節點、設備、車號對應或操作流程。"
        state = f"\nStructured conversation facts: train_number={train_number or 'unknown'}, car_number={car_number or 'unknown'}, vehicle={vehicle or 'unknown'}, fault_id={fault_id or 'unknown'}."
        if train_number and not vehicle:
            state += f" 復誦車次時必須忠實使用 {train_number}，不可改寫數字；先簡短確認車次，再只追問車型。車次不可推導車型。"
        return LLM_TROUBLESHOOTING_PROMPT.format(catalog=context.catalog, knowledge_blocks=context.knowledge_blocks) + "\n\nRouting state:\n" + routing_note + state

    def chat(self, request: ChatRequest) -> ChatResponse:
        if not self.client:
            raise RuntimeError("OpenAI API key 尚未設定")
        start = time.perf_counter()
        session = self._get_session(request.session_id)
        session.raw_user_text = request.message
        routing = extract_routing(request.message, self.registry.available_vehicles(), self.registry.available_faults())
        if routing.vehicle:
            session.vehicle = routing.vehicle
        if routing.car_number:
            session.car_number = routing.car_number
        if routing.train_number:
            session.train_number = routing.train_number
        if routing.fault_id:
            session.fault_id = routing.fault_id
        session.messages.append({"role": "user", "content": request.message})
        context = self.build_context(vehicle=session.vehicle, fault_id=session.fault_id)
        messages: list[dict[str, str]] = [{"role": "system", "content": self.build_instructions(vehicle=session.vehicle, fault_id=session.fault_id, train_number=session.train_number, car_number=session.car_number)}]
        messages.extend(session.messages[-24:])
        response = self.client.chat.completions.create(
            model=settings.openai_text_model,
            messages=messages,
            temperature=0.4,
            timeout=30,
        )
        reply = response.choices[0].message.content or "我沒有產生回覆，請再說一次。"
        session.messages.append({"role": "assistant", "content": reply})
        session.semantic_result = {"mode": "llm_driven", "knowledge_chars": context.chars, "sources": [source.model_dump() for source in context.sources]}
        session.interpretation_source = "llm"
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info("LLM chat latency_ms=%s session=%s turns=%s knowledge_chars=%s", latency_ms, session.session_id, len(session.messages), context.chars)
        primary = context.sources[0] if context.sources else None
        return ChatResponse(
            session_id=session.session_id,
            reply=reply,
            sources=context.sources,
            latency_ms=latency_ms,
            vehicle=session.vehicle,
            fault_id=session.fault_id,
            current_node=None,
            waiting_for_answer=False,
            expected_input=None,
            last_parsed_answer=None,
            last_turn_status="llm",
            raw_user_text=session.raw_user_text,
            interpretation_source=session.interpretation_source,
            semantic_result=session.semantic_result,
        )

    def debug_context(self) -> dict[str, Any]:
        context = self.build_context()
        return {
            "mode": "llm_driven",
            "knowledge_loaded": bool(context.sources),
            "knowledge_chars": context.chars,
            "sources": [source.model_dump() for source in context.sources],
            "instructions_chars": len(self.build_instructions()),
        }

    def _get_session(self, session_id: str | None) -> ConversationSession:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        new_session_id = session_id or str(uuid.uuid4())
        session = ConversationSession(session_id=new_session_id)
        self.sessions[new_session_id] = session
        return session


def read_yaml_text(path: Path) -> str:
    return strip_markdown_fence(path.read_text(encoding="utf-8")).strip()
