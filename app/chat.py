from __future__ import annotations

import logging
import time
import uuid

from app.models import ChatRequest, ChatResponse, ConversationSession
from app.troubleshooting import ConversationOrchestrator

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, orchestrator: ConversationOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.sessions: dict[str, ConversationSession] = {}

    def chat(self, request: ChatRequest) -> ChatResponse:
        start = time.perf_counter()
        session = self._get_session(request.session_id)
        result = self.orchestrator.submit_user_answer(session, request.message)
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "Troubleshooting chat latency_ms=%s session=%s vehicle=%s fault=%s node=%s status=%s waiting=%s",
            latency_ms,
            session.session_id,
            session.vehicle,
            session.fault_id,
            result.node_id,
            result.status,
            result.waiting_for_answer,
        )
        return ChatResponse(
            session_id=session.session_id,
            reply=result.utterance,
            sources=result.sources,
            latency_ms=latency_ms,
            vehicle=session.vehicle,
            fault_id=session.fault_id,
            current_node=session.current_node,
            waiting_for_answer=session.waiting_for_answer,
            expected_input=session.expected_input,
            last_parsed_answer=session.last_parsed_answer,
            last_turn_status=session.last_turn_status,
            raw_user_text=session.raw_user_text,
            interpretation_source=session.interpretation_source,
            semantic_result=session.semantic_result,
        )

    def _get_session(self, session_id: str | None) -> ConversationSession:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        new_session_id = session_id or str(uuid.uuid4())
        session = ConversationSession(session_id=new_session_id)
        self.sessions[new_session_id] = session
        return session


