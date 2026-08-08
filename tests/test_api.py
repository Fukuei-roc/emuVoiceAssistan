from pathlib import Path

from fastapi.testclient import TestClient

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
    assert data["results"][0]["vehicle"] == "EMU800"
    assert data["results"][0]["fault_id"] == "vcb_not_close"


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
    assert "一次只問一個問題" in data["instructions"]
    assert "double_voltage_decision" in data["instructions"]


def test_text_chat_uses_llm_history_and_yaml_prompt(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(chat_service, "client", fake)
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"message": "我發生VCP不閉合"}).json()
        second = client.post("/api/chat", json={"session_id": first["session_id"], "message": "八百新"}).json()
    assert first["reply"] == "請問目前是哪一型車？"
    assert second["session_id"] == first["session_id"]
    assert second["last_turn_status"] == "llm"
    assert len(fake.chat.completions.calls) == 2
    second_messages = fake.chat.completions.calls[1]["messages"]
    assert any("double_voltage_decision" in message["content"] for message in second_messages if message["role"] == "system")
    assert any(message["content"] == "我發生VCP不閉合" for message in second_messages)
    assert any(message["content"] == "八百新" for message in second_messages)


def test_frontend_debug_fields_exist():
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert "debugRealtimeAudio" in html
    assert "debugRawUserText" in html
    assert "llm-yaml1" in html
