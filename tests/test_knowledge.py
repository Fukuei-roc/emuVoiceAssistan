from app.config import ROOT_DIR
from app.llm_troubleshooting import LLMTroubleshootingService
from app.realtime import RealtimeService
from app.troubleshooting import FaultRegistry, load_fault_yaml

YAML_PATH = ROOT_DIR / "knowledge" / "EMU800" / "faults" / "vcb_not_close.yaml"
SETTINGS_PATH = ROOT_DIR / "settings" / "knowledge_sources.json"


def make_registry():
    registry = FaultRegistry(SETTINGS_PATH)
    registry.reload()
    return registry


def test_yaml_can_be_loaded_with_pydantic():
    procedure = load_fault_yaml(YAML_PATH)
    assert procedure.vehicle == "EMU800"
    assert procedure.id
    assert procedure.start in procedure.nodes
    assert len(procedure.nodes) > 5


def test_knowledge_routing_loads_emu800_vcb_only():
    registry = make_registry()
    assert registry.get("EMU800", "vcb_not_close") is not None
    assert registry.get("EMU700", "vcb_not_close") is None
    assert registry.available_vehicles() == ["EMU800"]
    faults = registry.available_faults("EMU800")
    assert faults[0]["fault_id"] == "vcb_not_close"


def test_llm_prompt_contains_complete_yaml_and_one_question_rule():
    service = LLMTroubleshootingService(make_registry())
    instructions = service.build_instructions()
    yaml_text = YAML_PATH.read_text(encoding="utf-8").strip().strip("`")
    assert "一次只問一個問題" in instructions
    assert "YAML START" in instructions
    assert "EMU800" in instructions
    assert "vcb_not_close" in instructions
    assert "double_voltage_decision" in instructions
    assert "19.0" in instructions
    assert "27.5" in instructions
    assert "不要自行發明" in instructions
    assert len(instructions) > len(yaml_text)


def test_realtime_session_config_is_llm_driven_with_audio():
    service = RealtimeService(make_registry())
    session = service.session_config()
    assert session["model"]
    assert session["tools"] == []
    assert session["tool_choice"] == "none"
    assert session["audio"]["input"]["turn_detection"]["create_response"] is True
    assert session["audio"]["output"]["voice"] == "alloy"
    assert "double_voltage_decision" in session["instructions"]
    assert "一次只問一個問題" in session["instructions"]


def test_realtime_context_reports_sources_and_knowledge_chars():
    service = RealtimeService(make_registry())
    context = service.context_payload()
    assert context["mode"] == "llm_driven"
    assert context["knowledge_loaded"] is True
    assert context["knowledge_chars"] > 1000
    assert context["sources"][0]["vehicle"] == "EMU800"
    assert context["sources"][0]["fault_id"] == "vcb_not_close"


def test_frontend_has_remote_audio_playback_and_no_submit_answer_loop():
    js = (ROOT_DIR / "static" / "app.js").read_text(encoding="utf-8")
    assert "remoteAudio.play()" in js
    assert "modalities: [\"audio\", \"text\"]" in js
    assert "session.update" in js
    assert "/api/realtime/context" in js
    assert "/api/realtime/submitAnswer" not in js
