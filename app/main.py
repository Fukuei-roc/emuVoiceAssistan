from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.chat import ChatService
from app.llm_troubleshooting import LLMTroubleshootingService
from app.config import ROOT_DIR, settings
from app.models import ChatRequest, ChatResponse, HealthResponse, KnowledgeSearchResult, RealtimeSessionResponse, SearchResponse
from app.realtime import RealtimeService
from app.troubleshooting import ConversationOrchestrator, FaultRegistry, TroubleshootingEngine


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="EMU 故障處理助手")

fault_registry = FaultRegistry(settings.knowledge_sources_path)
troubleshooting_engine = TroubleshootingEngine(fault_registry)
orchestrator = ConversationOrchestrator(troubleshooting_engine)
legacy_chat_service = ChatService(orchestrator)
chat_service = LLMTroubleshootingService(fault_registry)
realtime_service = RealtimeService(fault_registry)

static_dir = ROOT_DIR / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def loaded_fault_count() -> int:
    return sum(len(faults) for faults in fault_registry.registry.values())


@app.on_event("startup")
def startup() -> None:
    logger.info("Server startup")
    try:
        fault_registry.reload()
    except Exception:
        logger.exception("Fault registry reload failed")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    count = loaded_fault_count()
    return HealthResponse(
        status="ok" if count > 0 else "degraded",
        knowledge_loaded=count > 0,
        knowledge_sections=count,
        openai_configured=bool(settings.openai_api_key),
    )


@app.get("/api/search", response_model=SearchResponse)
def search(q: str = Query(min_length=1), limit: int = Query(default=5, ge=1, le=10)) -> SearchResponse:
    results = [KnowledgeSearchResult(**item) for item in fault_registry.search(q)[:limit]]
    return SearchResponse(query=q, results=results)


@app.get("/api/knowledge/status")
def knowledge_status():
    return fault_registry.status()


@app.post("/api/knowledge/reload")
def knowledge_reload():
    try:
        fault_registry.reload()
    except Exception as exc:
        logger.exception("Knowledge reload failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return fault_registry.status()


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        return chat_service.chat(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Chat failed")
        raise HTTPException(status_code=500, detail="文字對話處理失敗") from exc



@app.get("/api/realtime/context")
def realtime_context():
    return realtime_service.context_payload()
@app.post("/api/realtime/session", response_model=RealtimeSessionResponse)
async def realtime_session() -> RealtimeSessionResponse:
    try:
        session = await realtime_service.create_session()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Realtime session failed")
        raise HTTPException(status_code=500, detail="Realtime session 建立失敗") from exc
    return RealtimeSessionResponse(session=session)


@app.post("/api/realtime/call")
async def realtime_call(payload: dict):
    sdp = str(payload.get("sdp", ""))
    try:
        return await realtime_service.create_call(sdp)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Realtime call failed")
        raise HTTPException(status_code=500, detail="WebRTC SDP 交換失敗") from exc


def _realtime_chat_response(session_id: str, message: str) -> dict:
    response = legacy_chat_service.chat(ChatRequest(session_id=session_id, message=message))
    return response.model_dump()


@app.post("/api/realtime/getCurrentStep")
def realtime_get_current_step(payload: dict):
    session_id = str(payload.get("session_id", "realtime-default"))
    session = legacy_chat_service._get_session(session_id)
    result = orchestrator.get_turn(session)
    return {
        "session_id": session.session_id,
        "status": result.status,
        "node_id": result.node_id,
        "utterance": result.utterance,
        "waiting_for_answer": result.waiting_for_answer,
        "expected_input": result.expected_input.model_dump() if result.expected_input else None,
        "vehicle": session.vehicle,
        "fault_id": session.fault_id,
        "current_node": session.current_node,
        "last_turn_status": session.last_turn_status,
        "raw_user_text": session.raw_user_text,
        "interpretation_source": session.interpretation_source,
        "semantic_result": session.semantic_result,
    }


@app.post("/api/realtime/submitAnswer")
def realtime_submit_answer(payload: dict):
    message = str(payload.get("raw_answer", payload.get("message", ""))).strip()
    session_id = str(payload.get("session_id", "realtime-default"))
    if not message:
        raise HTTPException(status_code=400, detail="raw_answer 不可為空")
    return _realtime_chat_response(session_id, message)


@app.post("/api/realtime/processTroubleshooting")
def realtime_process_troubleshooting(payload: dict):
    message = str(payload.get("message", payload.get("query", ""))).strip()
    session_id = str(payload.get("session_id", "realtime-default"))
    if not message:
        raise HTTPException(status_code=400, detail="message 不可為空")
    return _realtime_chat_response(session_id, message)


@app.post("/api/realtime/searchKnowledge")
def realtime_search_knowledge(payload: dict):
    message = str(payload.get("query", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="query 不可為空")
    return _realtime_chat_response("realtime-default", message)


@app.get("/api/config")
def frontend_config():
    return {
        "realtime_model": settings.openai_realtime_model,
        "openai_configured": bool(settings.openai_api_key),
    }


