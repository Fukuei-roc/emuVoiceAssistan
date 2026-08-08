from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.chat import ChatService
from app.config import ROOT_DIR, settings
from app.knowledge import MarkdownKnowledgeBase
from app.models import ChatRequest, ChatResponse, HealthResponse, RealtimeSessionResponse, SearchResponse
from app.realtime import RealtimeService


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="EMU800 AI 故障處理助手")

knowledge = MarkdownKnowledgeBase(settings.db_path, settings.knowledge_paths, settings.active_vehicle)
chat_service = ChatService(knowledge)
realtime_service = RealtimeService(knowledge)

static_dir = ROOT_DIR / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def startup() -> None:
    logger.info("Server startup")
    knowledge.reload()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        knowledge_loaded=knowledge.section_count > 0,
        knowledge_sections=knowledge.section_count,
        openai_configured=bool(settings.openai_api_key),
    )


@app.get("/api/search", response_model=SearchResponse)
def search(q: str = Query(min_length=1), limit: int = Query(default=5, ge=1, le=10)) -> SearchResponse:
    return SearchResponse(query=q, results=knowledge.search(q, limit=limit))


@app.get("/api/knowledge/status")
def knowledge_status():
    return knowledge.status()


@app.post("/api/knowledge/reload")
def knowledge_reload():
    try:
        knowledge.reload()
    except Exception as exc:
        logger.exception("Knowledge reload failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return knowledge.status()


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        return chat_service.chat(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Chat failed")
        raise HTTPException(status_code=500, detail="文字對話處理失敗") from exc


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


@app.post("/api/realtime/searchKnowledge")
def realtime_search_knowledge(payload: dict):
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不可為空")
    return realtime_service.search_knowledge(query)


@app.get("/api/config")
def frontend_config():
    return {
        "realtime_model": settings.openai_realtime_model,
        "openai_configured": bool(settings.openai_api_key),
    }
