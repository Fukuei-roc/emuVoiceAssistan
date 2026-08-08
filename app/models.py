from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KnowledgeSection(BaseModel):
    id: int | None = None
    vehicle: str
    heading: str
    content: str
    source: str


class KnowledgeSearchResult(BaseModel):
    heading: str
    content: str
    source: str


class SearchResponse(BaseModel):
    query: str
    results: list[KnowledgeSearchResult]


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: list[KnowledgeSearchResult]
    latency_ms: int


class ConversationSession(BaseModel):
    session_id: str
    vehicle: str = "EMU800"
    messages: list[dict[str, str]] = Field(default_factory=list)
    last_knowledge_sections: list[KnowledgeSearchResult] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeStatus(BaseModel):
    vehicle: str
    file: str
    files: list[str] = Field(default_factory=list)
    sections: int
    loaded_at: str | None
    file_modified_at: str | None


class HealthResponse(BaseModel):
    status: str
    knowledge_loaded: bool
    knowledge_sections: int
    openai_configured: bool


class RealtimeSessionResponse(BaseModel):
    session: dict[str, Any]
