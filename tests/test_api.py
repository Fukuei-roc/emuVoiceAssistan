from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import ApplicationFileSettings, settings
from app.main import app, chat_service


class FakeChoice:
    def __init__(self, content):
        self.message = type("Message", (), {"content": content})()


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"choices": [FakeChoice("請問目前是哪一型車？")]} )()


class FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": FakeCompletions()})()


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["knowledge_loaded"] is True
    assert data["knowledge_sections"] >= 1


def test_search_api_returns_fault_procedure():
    with TestClient(app) as client:
        response = client.get("/api/search", params={"q": "VCB"})
    assert response.status_code == 200
    data = response.json()
    assert {item["vehicle"] for item in data["results"]} == {"EMU700", "EMU800"}
    assert all(item["fault_id"] == "vcb_not_close" for item in data["results"])


def test_knowledge_status_contains_fault_registry():
    with TestClient(app) as client:
        response = client.get("/api/knowledge/status")
    assert response.status_code == 200
    data = response.json()
    assert "EMU800" in data["vehicles"]
    assert "vcb_not_close" in data["vehicles"]["EMU800"]["faults"]


def test_realtime_context_contains_yaml_prompt():
    with TestClient(app) as client:
        response = client.get("/api/realtime/context")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "llm_driven"
    assert data["session"]["audio"]["input"]["turn_detection"]["create_response"] is True
    assert data["session"]["audio"]["input"]["turn_detection"]["threshold"] == 0.7
    assert "一次只問一個問題" in data["instructions"]
    assert data["knowledge_loaded"] is True
    assert "emu700_vcb_not_close" in data["instructions"]
    assert "emu800_vcb_not_close" in data["instructions"]
    with TestClient(app) as client:
        selected = client.get("/api/realtime/context", params={"vehicle": "EMU800", "fault_id": "vcb_not_close"}).json()
    assert selected["knowledge_loaded"] is True
    assert "emu700_vcb_not_close" in selected["instructions"]
    assert "emu800_vcb_not_close" in selected["instructions"]


def test_realtime_route_identifies_vehicle_and_rejects_train_number():
    with TestClient(app) as client:
        train = client.post("/api/realtime/route", json={"message": "我是4232次，我VCB不閉合"}).json()
        car = client.post("/api/realtime/route", json={"message": "我車號813，我VCB不閉合"}).json()
    assert train["routing"]["train_number"] == "4232"
    assert train["routing"]["vehicle"] is None
    assert train["routing"]["fault_id"] == "vcb_not_close"
    assert "4232" in train["session"]["instructions"]
    assert "不可改寫數字" in train["session"]["instructions"]
    assert car["routing"]["vehicle"] == "EMU800"
    assert car["routing"]["fault_id"] == "vcb_not_close"


def test_application_models_and_vad_are_loaded_from_toml():
    assert settings.openai_text_model == "gpt-4.1-mini"
    assert settings.openai_realtime_model == "gpt-realtime"
    assert settings.openai_realtime_vad_threshold == 0.7


@pytest.mark.parametrize("threshold", [-0.01, 1.01])
def test_application_settings_reject_invalid_vad_threshold(threshold):
    with pytest.raises(ValidationError):
        ApplicationFileSettings.model_validate(
            {
                "OPENAI_TEXT_MODEL": "gpt-4.1-mini",
                "OPENAI_REALTIME_MODEL": "gpt-realtime",
                "OPENAI_REALTIME_VAD_THRESHOLD": threshold,
            }
        )


def test_application_settings_reject_blank_model_names():
    with pytest.raises(ValidationError):
        ApplicationFileSettings.model_validate(
            {
                "OPENAI_TEXT_MODEL": "   ",
                "OPENAI_REALTIME_MODEL": "gpt-realtime",
                "OPENAI_REALTIME_VAD_THRESHOLD": 0.7,
            }
        )


def test_secret_example_and_application_settings_are_separated():
    env_example = Path(".env.example").read_text(encoding="utf-8").strip()
    application_toml = Path("settings/application.toml").read_text(encoding="utf-8")
    assert env_example == "OPENAI_API_KEY="
    assert "OPENAI_API_KEY" not in application_toml


def test_text_chat_uses_llm_history_and_yaml_prompt(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(chat_service, "client", fake)
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"message": "我發生VCP不閉合"}).json()
        second = client.post("/api/chat", json={"session_id": first["session_id"], "message": "八百新，VCB不閉合"}).json()
    assert first["reply"] == "請問目前是哪一型車？"
    assert second["session_id"] == first["session_id"]
    assert second["last_turn_status"] == "llm"
    assert len(fake.chat.completions.calls) == 2
    second_messages = fake.chat.completions.calls[1]["messages"]
    assert any("double_voltage_decision" in message["content"] for message in second_messages if message["role"] == "system")
    assert any(message["content"] == "我發生VCP不閉合" for message in second_messages)
    assert any(message["content"] == "八百新，VCB不閉合" for message in second_messages)


def test_frontend_debug_fields_exist():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    assert "debugRealtimeAudio" in html
    assert "debugRawUserText" in html
    assert "20260827-voice2" in html
    assert "voiceIndicator" in html
    assert "thinkingIndicator" in html
    assert "quickReplies" not in html
    assert "composerDrawer" in html
    assert "historyPanel" in html
    assert "viewport-fit=cover" in html
    assert "20260903-routing1" in html
    assert 'initial response requested' not in javascript
    assert 'modalities: ["audio", "text"]' not in javascript
    assert "Recoverable Realtime event error" in javascript
    assert "connectionGeneration" in javascript
    assert "getQuickReplyOptions" not in javascript
    assert "renderQuickReplies" not in javascript
