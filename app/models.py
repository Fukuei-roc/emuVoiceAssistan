from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class KnowledgeSection(BaseModel):
    id: int | None = None
    vehicle: str
    heading: str
    content: str
    source: str


class KnowledgeSearchResult(BaseModel):
    heading: str | None = None
    content: str | None = None
    source: str | None = None
    vehicle: str | None = None
    fault_id: str | None = None
    title: str | None = None


class ExpectedInput(BaseModel):
    field: str | None = None
    type: Literal["number", "boolean", "enum", "action_done", "text", "vehicle", "fault"]
    unit: str | None = None
    options: list[str] = Field(default_factory=list)


class ParsedAnswer(BaseModel):
    understood: bool
    ambiguous: bool = False
    value: Any = None
    raw: str
    reason: str | None = None
    source: str | None = None


class TurnResult(BaseModel):
    status: Literal["ask", "action", "clarify", "completed", "unsupported"]
    node_id: str | None = None
    utterance: str
    waiting_for_answer: bool
    expected_input: ExpectedInput | None = None
    parsed_answer: ParsedAnswer | None = None
    vehicle: str | None = None
    fault_id: str | None = None
    procedure_status: str = "active"
    sources: list[KnowledgeSearchResult] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    results: list[KnowledgeSearchResult]


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: list[KnowledgeSearchResult] = Field(default_factory=list)
    latency_ms: int
    vehicle: str | None = None
    fault_id: str | None = None
    current_node: str | None = None
    waiting_for_answer: bool = False
    expected_input: ExpectedInput | None = None
    last_parsed_answer: ParsedAnswer | None = None
    last_turn_status: str | None = None
    raw_user_text: str | None = None
    interpretation_source: str | None = None
    semantic_result: Any = None


class ConversationSession(BaseModel):
    session_id: str
    vehicle: str | None = None
    car_number: str | None = None
    train_number: str | None = None
    fault_id: str | None = None
    current_node: str | None = None
    waiting_for_answer: bool = False
    expected_input: ExpectedInput | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, str]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    procedure_status: str = "not_started"
    last_parsed_answer: ParsedAnswer | None = None
    last_turn_status: str | None = None
    raw_user_text: str | None = None
    interpretation_source: str | None = None
    semantic_result: Any = None
    last_knowledge_sections: list[KnowledgeSearchResult] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeStatus(BaseModel):
    vehicle: str | None = None
    file: str | None = None
    files: list[str] = Field(default_factory=list)
    sections: int = 0
    loaded_at: str | None = None
    file_modified_at: str | None = None


class HealthResponse(BaseModel):
    status: str
    knowledge_loaded: bool
    knowledge_sections: int
    openai_configured: bool


class RealtimeSessionResponse(BaseModel):
    session: dict[str, Any]
