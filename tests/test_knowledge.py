from app.config import ROOT_DIR
from app.llm_troubleshooting import LLMTroubleshootingService
from app.realtime import RealtimeService
from app.troubleshooting import FaultRegistry, load_fault_yaml
from app.semantic import extract_routing

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


def test_knowledge_routing_loads_emu700_and_emu800_vcb():
    registry = make_registry()
    assert registry.get("EMU700", "vcb_not_close") is not None
    assert registry.get("EMU800", "vcb_not_close") is not None
    assert registry.available_vehicles() == ["EMU700", "EMU800"]
    assert registry.get("EMU700", "vcb_not_close") is not registry.get("EMU800", "vcb_not_close")
    faults = registry.available_faults("EMU800")
    assert faults[0]["fault_id"] == "vcb_not_close"


def test_llm_prompt_contains_complete_yaml_and_one_question_rule():
    service = LLMTroubleshootingService(make_registry())
    instructions = service.build_instructions(vehicle="EMU800", fault_id="vcb_not_close")
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
    session = service.session_config(vehicle="EMU800", fault_id="vcb_not_close")
    assert session["model"]
    assert session["tools"] == []
    assert session["tool_choice"] == "none"
    assert session["audio"]["input"]["turn_detection"]["create_response"] is True
    assert session["audio"]["input"]["turn_detection"]["threshold"] == 0.7
    assert session["audio"]["output"]["voice"] == "alloy"
    assert "double_voltage_decision" in session["instructions"]
    assert "一次只問一個問題" in session["instructions"]


def test_realtime_context_reports_sources_and_knowledge_chars():
    service = RealtimeService(make_registry())
    context = service.context_payload(vehicle="EMU800", fault_id="vcb_not_close")
    assert context["mode"] == "llm_driven"
    assert context["knowledge_loaded"] is True
    assert context["knowledge_chars"] > 1000
    assert {source["vehicle"] for source in context["sources"]} == {"EMU700", "EMU800"}
    assert all(source["fault_id"] == "vcb_not_close" for source in context["sources"])


def test_frontend_has_remote_audio_playback_and_no_submit_answer_loop():
    js = (ROOT_DIR / "static" / "app.js").read_text(encoding="utf-8")
    assert "remoteAudio.play()" in js
    assert "initial response requested" not in js
    assert "session.update" in js
    assert "/api/realtime/context" in js
    assert "/api/realtime/route" in js
    assert "/api/realtime/submitAnswer" not in js


def test_vehicle_and_train_number_routing_is_conservative():
    vehicles = ["EMU700", "EMU800"]
    faults = [{"fault_id": "vcb_not_close"}]
    assert extract_routing("4232次，我VCB不閉合", vehicles, faults).vehicle is None
    assert extract_routing("1167次", vehicles, faults).vehicle is None
    assert extract_routing("713次", vehicles, faults).vehicle is None
    assert extract_routing("813次", vehicles, faults).vehicle is None
    assert extract_routing("車號713", vehicles, faults).vehicle == "EMU700"
    assert extract_routing("我的車號是713", vehicles, faults).vehicle == "EMU700"
    assert extract_routing("713車", vehicles, faults).vehicle == "EMU700"
    assert extract_routing("我車號813，我VCB不閉合", vehicles, faults).vehicle == "EMU800"
    assert extract_routing("EMU700", vehicles, faults).vehicle == "EMU700"
    assert extract_routing("800型", vehicles, faults).vehicle == "EMU800"


def test_train_number_is_explicitly_available_for_repetition():
    service = RealtimeService(make_registry())
    route = service.route_message("我是4232次，我VCB不閉合")
    instructions = route["session"]["instructions"]
    assert "train_number=4232" in instructions
    assert "4232" in instructions
    assert "不可改寫數字" in instructions
    assert "車型" in instructions

    second = service.route_message("我是800型", current=route["routing"])
    assert second["routing"] == {
        "vehicle": "EMU800",
        "car_number": None,
        "train_number": "4232",
        "fault_id": "vcb_not_close",
    }
    assert "train_number=4232" in second["session"]["instructions"]
    assert "vehicle=EMU800" in second["session"]["instructions"]
