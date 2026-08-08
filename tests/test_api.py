from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["knowledge_loaded"] is True
    assert data["knowledge_sections"] > 10


def test_search_api():
    with TestClient(app) as client:
        response = client.get("/api/search", params={"q": "VCB不閉合"})
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "VCB不閉合"
    assert data["results"]
    assert any("VCB" in item["heading"] for item in data["results"])


def test_knowledge_status():
    with TestClient(app) as client:
        response = client.get("/api/knowledge/status")
    assert response.status_code == 200
    data = response.json()
    assert data["vehicle"] == "EMU800"
    assert data["sections"] > 10
    assert data["file"].endswith("EMU800_故障處理流程_AI整理版.md")
    assert data["files"]
