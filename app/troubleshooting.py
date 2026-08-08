from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import ROOT_DIR
from app.models import ConversationSession, ExpectedInput, KnowledgeSearchResult, ParsedAnswer, TurnResult
from app.semantic import NaturalLanguageInterpreter

logger = logging.getLogger(__name__)

VALID_OPERATORS = {"is_true", "is_false", "eq", "neq", "gt", "gte", "lt", "lte", "between", "outside", ">", ">=", "<", "<=", "==", "!="}
YES_PATTERNS = ["是", "有", "已", "已恢復", "亮", "點亮", "都有亮", "全部亮", "on", "正常"]
NO_PATTERNS = ["不是", "沒有", "沒", "無", "未", "未恢復", "不亮", "沒亮", "off", "無效", "異常"]
AMBIGUOUS_PATTERNS = ["好像", "應該", "可能", "大概", "也許", "不確定", "不知道", "沒注意", "看不清", "不清楚", "差不多", "正常吧"]
ACTION_DONE_PATTERNS = ["完成", "好了", "已完成", "做完", "處理完", "確認完", "ok", "OK"]
SIDE_LOCATION_PATTERNS = ["在哪", "哪裡", "位置", "在哪裡"]


class FaultCondition(BaseModel):
    parameter: str | None = None
    operator: str
    value: float | int | str | bool | None = None
    min: float | int | None = None
    max: float | int | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def validate_operator(self) -> "FaultCondition":
        if self.operator not in VALID_OPERATORS:
            raise ValueError(f"Unsupported condition operator: {self.operator}")
        return self


class TroubleshootingNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    type: Literal["decision", "action", "information", "end"]
    question: str | None = None
    action: str | None = None
    result: str | None = None
    next: str | None = None
    options: dict[str, str] = Field(default_factory=dict)
    condition: FaultCondition | None = None
    location: str | None = None
    device: str | None = None
    description: str | None = None
    step: int | None = None


class FaultProcedure(BaseModel):
    id: str
    title: str
    vehicle: str
    symptom: str
    start: str
    nodes: dict[str, TroubleshootingNode]
    source_file: str = ""

    @model_validator(mode="after")
    def validate_graph(self) -> "FaultProcedure":
        if self.start not in self.nodes:
            raise ValueError(f"Start node not found: {self.start}")
        seen = set()
        for node_id, node in self.nodes.items():
            if node_id in seen:
                raise ValueError(f"Duplicate node id: {node_id}")
            seen.add(node_id)
            node.id = node_id
            if node.next and node.next not in self.nodes:
                raise ValueError(f"Node {node_id} next target not found: {node.next}")
            for label, target in node.options.items():
                if target not in self.nodes:
                    raise ValueError(f"Node {node_id} option {label} target not found: {target}")
        if not any(node.type == "end" for node in self.nodes.values()):
            raise ValueError("Procedure must contain at least one end node")
        return self


class FaultConfig(BaseModel):
    id: str
    name: str
    file: str


class VehicleFaultConfig(BaseModel):
    vehicle: str
    faults: list[FaultConfig] = Field(default_factory=list)


class FaultSourcesConfig(BaseModel):
    vehicles: list[VehicleFaultConfig] = Field(default_factory=list)


class FaultLoadError(BaseModel):
    vehicle: str
    fault_id: str
    file: str
    error: str


class FaultRegistry:
    def __init__(self, settings_path: Path) -> None:
        self.settings_path = settings_path
        self.registry: dict[str, dict[str, FaultProcedure]] = {}
        self.fault_names: dict[str, dict[str, str]] = {}
        self.errors: list[FaultLoadError] = []
        self.loaded_at: datetime | None = None

    def reload(self) -> None:
        self.registry = {}
        self.fault_names = {}
        self.errors = []
        config = FaultSourcesConfig.model_validate(json.loads(self.settings_path.read_text(encoding="utf-8")))
        for vehicle_config in config.vehicles:
            vehicle = normalize_vehicle(vehicle_config.vehicle) or vehicle_config.vehicle
            self.registry.setdefault(vehicle, {})
            self.fault_names.setdefault(vehicle, {})
            for fault in vehicle_config.faults:
                path = resolve_path(fault.file)
                try:
                    procedure = load_fault_yaml(path)
                    if normalize_vehicle(procedure.vehicle) != vehicle:
                        raise ValueError(f"YAML vehicle {procedure.vehicle} does not match settings vehicle {vehicle}")
                    procedure.id = fault.id
                    procedure.source_file = str(path)
                    self.registry[vehicle][fault.id] = procedure
                    self.fault_names[vehicle][fault.id] = fault.name
                except Exception as exc:
                    logger.exception("Failed to load fault YAML vehicle=%s fault=%s file=%s", vehicle, fault.id, path)
                    self.errors.append(FaultLoadError(vehicle=vehicle, fault_id=fault.id, file=str(path), error=str(exc)))
        self.loaded_at = datetime.now(timezone.utc)

    def available_vehicles(self) -> list[str]:
        return sorted(vehicle for vehicle, faults in self.registry.items() if faults)

    def available_faults(self, vehicle: str | None = None) -> list[dict[str, str]]:
        vehicles = [vehicle] if vehicle else list(self.registry.keys())
        results: list[dict[str, str]] = []
        for vehicle_name in vehicles:
            for fault_id, procedure in self.registry.get(vehicle_name, {}).items():
                results.append({"vehicle": vehicle_name, "fault_id": fault_id, "name": self.fault_names.get(vehicle_name, {}).get(fault_id, procedure.symptom), "title": procedure.title, "symptom": procedure.symptom})
        return results

    def get(self, vehicle: str, fault_id: str) -> FaultProcedure | None:
        return self.registry.get(vehicle, {}).get(fault_id)

    def has_vehicle(self, vehicle: str) -> bool:
        return vehicle in self.registry and bool(self.registry[vehicle])

    def search(self, query: str) -> list[dict[str, str]]:
        found_vehicle = normalize_vehicle(query)
        found_fault = normalize_fault(query)
        results: list[dict[str, str]] = []
        for vehicle, faults in self.registry.items():
            if found_vehicle and found_vehicle != vehicle:
                continue
            for fault_id, procedure in faults.items():
                title = self.fault_names.get(vehicle, {}).get(fault_id, procedure.symptom)
                haystack = normalize_text(" ".join([fault_id, title, procedure.title, procedure.symptom]))
                if found_fault == fault_id or normalize_text(query) in haystack or any(token in haystack for token in query_tokens(query)):
                    results.append({"vehicle": vehicle, "fault_id": fault_id, "title": title, "source": procedure.source_file})
        return results

    def status(self) -> dict[str, Any]:
        vehicles: dict[str, Any] = {}
        for vehicle, faults in self.registry.items():
            vehicles[vehicle] = {
                "fault_count": len(faults),
                "faults": sorted(faults.keys()),
                "sources": {fault_id: proc.source_file for fault_id, proc in faults.items()},
            }
        return {
            "loaded_at": self.loaded_at.isoformat() if self.loaded_at else None,
            "valid": any(self.registry.values()),
            "vehicles": vehicles,
            "errors": [error.model_dump() for error in self.errors],
        }


class TroubleshootingEngine:
    def __init__(self, registry: FaultRegistry, interpreter: NaturalLanguageInterpreter | None = None) -> None:
        self.registry = registry
        self.interpreter = interpreter or NaturalLanguageInterpreter()

    def handle_user_message(self, session: ConversationSession, message: str) -> TurnResult:
        logger.info("TURN START session_id=%s current_node=%s expected_input=%s", session.session_id, session.current_node, session.expected_input.model_dump() if session.expected_input else None)
        logger.info("RAW USER INPUT session_id=%s raw=%r", session.session_id, message)
        session.raw_user_text = message
        session.messages.append({"role": "user", "content": message})

        if not session.vehicle:
            explicit_vehicle = normalize_vehicle(message)
            if explicit_vehicle and not self.registry.has_vehicle(explicit_vehicle):
                session.vehicle = explicit_vehicle
            else:
                if explicit_vehicle or (session.expected_input and session.expected_input.type == "vehicle") or likely_vehicle_hint(message):
                    vehicle_parse = self.interpreter.interpret_vehicle(message, self.registry.available_vehicles(), "請問目前是哪一型車？")
                    self._record_interpretation(session, vehicle_parse)
                else:
                    vehicle_parse = ParsedAnswer(understood=False, ambiguous=True, raw=message, reason="尚未提供車型", source="deterministic")
                if not session.fault_id:
                    fault_parse = self.interpreter.interpret_fault(message, None, self.registry.available_faults(), "請說明目前遇到的故障。")
                    if fault_parse.understood and not fault_parse.ambiguous:
                        session.fault_id = str(fault_parse.value)
                        self._record_interpretation(session, fault_parse)
                if vehicle_parse.understood and not vehicle_parse.ambiguous and vehicle_parse.value:
                    session.vehicle = str(vehicle_parse.value)
                else:
                    return self._finish(session, TurnResult(status="ask", node_id=None, utterance="請問目前是哪一型車？", waiting_for_answer=True, expected_input=ExpectedInput(field="vehicle", type="vehicle")))

        if session.vehicle and not self.registry.has_vehicle(session.vehicle):
            logger.info("VALIDATION vehicle=%s exists=false", session.vehicle)
            return self._finish(session, TurnResult(status="unsupported", node_id=None, utterance=f"目前尚未載入 {session.vehicle} 的故障處理資料。", waiting_for_answer=False, vehicle=session.vehicle, fault_id=session.fault_id, procedure_status="unsupported"))
        logger.info("VALIDATION vehicle=%s exists=true", session.vehicle)

        if not session.fault_id:
            fault_parse = self.interpreter.interpret_fault(message, session.vehicle, self.registry.available_faults(session.vehicle), "請說明目前遇到的故障。")
            self._record_interpretation(session, fault_parse)
            if fault_parse.understood and not fault_parse.ambiguous and fault_parse.value:
                session.fault_id = str(fault_parse.value)
            else:
                return self._finish(session, TurnResult(status="ask", node_id=None, utterance="請說明目前遇到的故障。", waiting_for_answer=True, expected_input=ExpectedInput(field="fault", type="fault")))

        procedure = self._procedure_or_unsupported(session)
        if isinstance(procedure, TurnResult):
            return self._finish(session, procedure)

        if not session.current_node:
            session.current_node = procedure.start
            session.procedure_status = "active"
            return self._enter_current_node(session, procedure, prefix=f"開始 {session.vehicle} {fault_display_name(session.fault_id)} 流程。")

        node = procedure.nodes[session.current_node]
        if is_side_question(message):
            return self._clarify_same_node(session, procedure, node, side_question=True)

        parsed = self.interpreter.interpret_answer(message, expected_input_for_node(node), node_context_for_interpreter(node))
        self._record_interpretation(session, parsed)
        logger.info("PARSED ANSWER session_id=%s value=%r understood=%s ambiguous=%s source=%s", session.session_id, parsed.value, parsed.understood, parsed.ambiguous, parsed.source)
        if not parsed.understood or parsed.ambiguous:
            return self._clarify_same_node(session, procedure, node, parsed=parsed)

        if node.type == "decision":
            next_node = self._next_for_decision(node, parsed)
            if not next_node:
                parsed = ParsedAnswer(understood=False, ambiguous=True, raw=message, reason="無法對應 YAML options", source=parsed.source)
                self._record_interpretation(session, parsed)
                return self._clarify_same_node(session, procedure, node, parsed=parsed)
            session.variables[node.id] = parsed.value
            old_node = session.current_node
            session.current_node = next_node
            session.waiting_for_answer = False
            logger.info("ENGINE session_id=%s branch_from=%s next_node=%s", session.session_id, old_node, next_node)
            return self._enter_current_node(session, procedure)

        if node.type in {"action", "information"}:
            old_node = session.current_node
            if node.next:
                session.variables[node.id] = parsed.value
                session.current_node = node.next
                session.waiting_for_answer = False
                logger.info("ENGINE session_id=%s action_ack_from=%s next_node=%s", session.session_id, old_node, node.next)
                return self._enter_current_node(session, procedure)
            return self._finish(session, TurnResult(status="completed", node_id=node.id, utterance="流程結束。", waiting_for_answer=False, vehicle=session.vehicle, fault_id=session.fault_id, procedure_status="completed", sources=self._sources(procedure)))

        if node.type == "end":
            return self._enter_current_node(session, procedure)

        return self._clarify_same_node(session, procedure, node)
    def get_current_turn(self, session: ConversationSession) -> TurnResult:
        if not session.vehicle:
            return self._finish(session, TurnResult(status="ask", node_id=None, utterance="請問目前是哪一型車？", waiting_for_answer=True, expected_input=ExpectedInput(field="vehicle", type="vehicle")))
        if not session.fault_id:
            return self._finish(session, TurnResult(status="ask", node_id=None, utterance="請說明目前遇到的故障。", waiting_for_answer=True, expected_input=ExpectedInput(field="fault", type="fault")))
        procedure = self._procedure_or_unsupported(session)
        if isinstance(procedure, TurnResult):
            return self._finish(session, procedure)
        if not session.current_node:
            session.current_node = procedure.start
        return self._enter_current_node(session, procedure)

    def _procedure_or_unsupported(self, session: ConversationSession) -> FaultProcedure | TurnResult:
        if not session.vehicle or not session.fault_id:
            return TurnResult(status="unsupported", node_id=None, utterance="目前缺少車型或故障資訊。", waiting_for_answer=False, vehicle=session.vehicle, fault_id=session.fault_id, procedure_status="unsupported")
        procedure = self.registry.get(session.vehicle, session.fault_id)
        if not procedure:
            return TurnResult(status="unsupported", node_id=None, utterance=f"目前沒有 {session.vehicle} 的 {fault_display_name(session.fault_id)} 處理流程。", waiting_for_answer=False, vehicle=session.vehicle, fault_id=session.fault_id, procedure_status="unsupported")
        return procedure

    def _enter_current_node(self, session: ConversationSession, procedure: FaultProcedure, prefix: str | None = None) -> TurnResult:
        node = procedure.nodes[session.current_node]
        expected = expected_input_for_node(node)
        if node.type == "decision":
            utterance = render_question(node)
            status = "ask"
            waiting = True
        elif node.type == "action":
            utterance = render_action(node)
            status = "action"
            waiting = True
        elif node.type == "information":
            utterance = render_information(node)
            status = "action"
            waiting = True
        else:
            utterance = node.result or "流程結束。"
            status = "completed"
            waiting = False
            session.procedure_status = "completed"
            expected = None
        if prefix:
            utterance = f"{prefix}\n{utterance}"
        result = TurnResult(status=status, node_id=node.id, utterance=utterance, waiting_for_answer=waiting, expected_input=expected, vehicle=session.vehicle, fault_id=session.fault_id, procedure_status=session.procedure_status, sources=self._sources(procedure))
        return self._finish(session, result)

    def _clarify_same_node(self, session: ConversationSession, procedure: FaultProcedure, node: TroubleshootingNode, parsed: ParsedAnswer | None = None, side_question: bool = False) -> TurnResult:
        if side_question and (node.location or node.device):
            utterance = f"{node.device or '該設備'}位置：{node.location or 'YAML 未記載位置'}。{repeat_current_prompt(node)}"
        else:
            utterance = clarification_for_node(node)
        result = TurnResult(status="clarify", node_id=node.id, utterance=utterance, waiting_for_answer=True, expected_input=expected_input_for_node(node), parsed_answer=parsed, vehicle=session.vehicle, fault_id=session.fault_id, procedure_status=session.procedure_status, sources=self._sources(procedure))
        return self._finish(session, result)

    def _next_for_decision(self, node: TroubleshootingNode, parsed: ParsedAnswer) -> str | None:
        if node.condition and isinstance(parsed.value, (int, float)):
            return option_for_boolean(node.options, evaluate_condition(node.condition, parsed.value))
        if isinstance(parsed.value, bool):
            return option_for_boolean(node.options, parsed.value)
        if isinstance(parsed.value, str) and parsed.value in node.options:
            return node.options[parsed.value]
        return None

    def _record_interpretation(self, session: ConversationSession, parsed: ParsedAnswer) -> None:
        session.last_parsed_answer = parsed
        session.interpretation_source = parsed.source
        session.semantic_result = parsed.model_dump()
        logger.info(
            "AI SEMANTIC INTERPRETATION session_id=%s source=%s value=%r understood=%s ambiguous=%s",
            session.session_id,
            parsed.source,
            parsed.value,
            parsed.understood,
            parsed.ambiguous,
        )
    def _finish(self, session: ConversationSession, result: TurnResult) -> TurnResult:
        session.waiting_for_answer = result.waiting_for_answer
        session.expected_input = result.expected_input
        session.last_turn_status = result.status
        session.messages.append({"role": "assistant", "content": result.utterance})
        session.history.append({"node_id": result.node_id, "status": result.status, "utterance": result.utterance, "waiting_for_answer": result.waiting_for_answer, "expected_input": result.expected_input.model_dump() if result.expected_input else None, "parsed_answer": result.parsed_answer.model_dump() if result.parsed_answer else None})
        session.updated_at = datetime.utcnow()
        logger.info("TURN END session_id=%s status=%s current_node=%s waiting=%s", session.session_id, result.status, session.current_node, result.waiting_for_answer)
        return result

    def _sources(self, procedure: FaultProcedure) -> list[KnowledgeSearchResult]:
        return [KnowledgeSearchResult(heading=procedure.title, content=procedure.symptom, source=procedure.source_file, vehicle=procedure.vehicle, fault_id=procedure.id, title=fault_display_name(procedure.id))]


class ConversationOrchestrator:
    def __init__(self, engine: TroubleshootingEngine) -> None:
        self.engine = engine

    def get_turn(self, session: ConversationSession) -> TurnResult:
        return self.engine.get_current_turn(session)

    def submit_user_answer(self, session: ConversationSession, text: str) -> TurnResult:
        return self.engine.handle_user_message(session, text)



def node_context_for_interpreter(node: TroubleshootingNode) -> dict[str, Any]:
    extras = node.model_extra or {}
    return {
        "node_id": node.id,
        "type": node.type,
        "question": node.question,
        "action": node.action,
        "device": node.device,
        "location": node.location,
        "description": node.description,
        "options": list(node.options.keys()),
        "condition": node.condition.model_dump() if node.condition else None,
        "extra": {key: value for key, value in extras.items() if key in {"normal_state", "alias", "target_position", "car_description", "car_position"}},
    }

def resolve_path(path: str) -> Path:
    configured = Path(path)
    return configured if configured.is_absolute() else ROOT_DIR / configured


def load_fault_yaml(path: Path) -> FaultProcedure:
    raw = strip_markdown_fence(path.read_text(encoding="utf-8"))
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or "procedure" not in data:
        raise ValueError("YAML must contain top-level procedure")
    procedure = FaultProcedure.model_validate(data["procedure"])
    procedure.source_file = str(path)
    return procedure


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text



def likely_vehicle_hint(text: str) -> bool:
    compact = normalize_text(text).upper()
    return bool(re.search(r"EMU|[789]00|七百|八百|九百", compact))

def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def normalize_vehicle(text: str | None) -> str | None:
    if not text:
        return None
    compact = normalize_text(text).upper()
    match = re.search(r"EMU?(700|800|900)", compact)
    if match:
        return f"EMU{match.group(1)}"
    match = re.search(r"(?<!\d)(700|800|900)(型)?(?!\d)", compact)
    if match:
        return f"EMU{match.group(1)}"
    return None


def normalize_fault(text: str | None) -> str | None:
    if not text:
        return None
    compact = normalize_text(text)
    if "vcb" in compact and any(term in compact for term in ["不閉合", "無法閉合", "合不起來", "不會閉合", "未閉合"]):
        return "vcb_not_close"
    return None


def fault_display_name(fault_id: str | None) -> str:
    if fault_id == "vcb_not_close":
        return "VCB不閉合"
    return fault_id or "未知故障"


def query_tokens(query: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", query) if token.strip()]


def expected_input_for_node(node: TroubleshootingNode) -> ExpectedInput | None:
    if node.type == "decision":
        if node.condition:
            return ExpectedInput(field=node.condition.parameter or node.id, type="number", unit=node.condition.unit, options=list(node.options.keys()))
        if is_boolean_options(node.options):
            return ExpectedInput(field=node.id, type="boolean", options=list(node.options.keys()))
        return ExpectedInput(field=node.id, type="enum", options=list(node.options.keys()))
    if node.type in {"action", "information"}:
        return ExpectedInput(field=node.id, type="action_done")
    return None


def is_boolean_options(options: dict[str, str]) -> bool:
    labels = set(options.keys())
    return bool(labels) and all(label.startswith("是") or label.startswith("否") or label in {"兩組皆閉合", "僅一組閉合"} for label in labels)


def parse_answer_for_node(node: TroubleshootingNode, text: str) -> ParsedAnswer:
    expected = expected_input_for_node(node)
    raw = text.strip()
    if not expected:
        return ParsedAnswer(understood=True, value=raw, raw=text)
    if contains_ambiguity(raw):
        return ParsedAnswer(understood=False, ambiguous=True, raw=text, reason="回答含模糊詞")
    if expected.type == "number":
        value = parse_number(raw)
        if value is None:
            return ParsedAnswer(understood=False, raw=text, reason="需要數值")
        return ParsedAnswer(understood=True, value=value, raw=text)
    if expected.type == "boolean":
        value = parse_boolean(raw)
        if value is None:
            return ParsedAnswer(understood=False, raw=text, reason="需要明確是/否")
        return ParsedAnswer(understood=True, value=value, raw=text)
    if expected.type == "enum":
        option = match_option({option: option for option in expected.options}, raw)
        if not option:
            return ParsedAnswer(understood=False, raw=text, reason="需要選項答案")
        return ParsedAnswer(understood=True, value=option, raw=text)
    if expected.type == "action_done":
        if any(pattern.lower() in normalize_text(raw).lower() for pattern in ACTION_DONE_PATTERNS):
            return ParsedAnswer(understood=True, value=True, raw=text)
        return ParsedAnswer(understood=False, raw=text, reason="需要確認此操作已完成")
    return ParsedAnswer(understood=True, value=raw, raw=text)


def contains_ambiguity(text: str) -> bool:
    compact = normalize_text(text)
    return any(pattern in compact for pattern in AMBIGUOUS_PATTERNS)


def is_side_question(text: str) -> bool:
    compact = normalize_text(text)
    return any(pattern in compact for pattern in SIDE_LOCATION_PATTERNS)


def parse_number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match:
        return float(match.group(0))
    zh = text.replace("二十五", "25").replace("二十六", "26").replace("二十七", "27").replace("二十四", "24")
    match = re.search(r"-?\d+(?:\.\d+)?", zh)
    return float(match.group(0)) if match else None


def parse_boolean(text: str) -> bool | None:
    compact = normalize_text(text)
    if "都正常" in compact or "全部正常" in compact or "後面的都正常" in compact:
        return None
    if any(pattern.lower() in compact.lower() for pattern in NO_PATTERNS):
        return False
    if any(pattern.lower() in compact.lower() for pattern in YES_PATTERNS):
        return True
    return None


def evaluate_condition(condition: FaultCondition, value: float | bool | str) -> bool:
    op = condition.operator
    if op in {">", "gt"}:
        return float(value) > float(condition.value)
    if op in {">=", "gte"}:
        return float(value) >= float(condition.value)
    if op in {"<", "lt"}:
        return float(value) < float(condition.value)
    if op in {"<=", "lte"}:
        return float(value) <= float(condition.value)
    if op in {"==", "eq"}:
        return value == condition.value
    if op in {"!=", "neq"}:
        return value != condition.value
    if op == "between":
        return float(condition.min) <= float(value) <= float(condition.max)
    if op == "outside":
        return not (float(condition.min) <= float(value) <= float(condition.max))
    if op == "is_true":
        return bool(value) is True
    if op == "is_false":
        return bool(value) is False
    raise ValueError(f"Unsupported operator: {op}")


def option_for_boolean(options: dict[str, str], value: bool) -> str | None:
    preferred = ["是", "是但仍不閉合", "兩組皆閉合"] if value else ["否", "僅一組閉合"]
    for label in preferred:
        if label in options:
            return options[label]
    for label, target in options.items():
        if value and label.startswith("是"):
            return target
        if not value and label.startswith("否"):
            return target
    return None


def match_option(options: dict[str, str], message: str) -> str | None:
    compact = normalize_text(message)
    for label in options:
        normalized = normalize_text(label)
        if normalized in compact or compact in normalized:
            return label
    if "出庫" in compact or "整備" in compact:
        return next((label for label in options if "出庫" in label), None)
    if "行駛" in compact or "運轉" in compact:
        return next((label for label in options if "行駛" in label), None)
    if "單組" in compact or "一組" in compact:
        return next((label for label in options if "單組" in label or "僅一組" in label), None)
    if "兩組" in compact or "雙組" in compact:
        return next((label for label in options if "兩組" in label), None)
    number = parse_number(message)
    if number is not None:
        as_int = str(int(number)) if number.is_integer() else str(number)
        if as_int in options:
            return as_int
    return None


def render_action(node: TroubleshootingNode) -> str:
    pieces = []
    if node.step:
        pieces.append(f"步驟 {node.step}")
    if node.action:
        pieces.append(node.action)
    if node.location:
        pieces.append(f"位置：{node.location}")
    if node.device:
        pieces.append(f"設備：{node.device}")
    if node.description:
        pieces.append(node.description)
    return "。".join(pieces) + "。完成後請回答：完成。"


def render_information(node: TroubleshootingNode) -> str:
    extras = node.model_extra or {}
    description = extras.get("car_description") or node.description or "請確認相關資訊"
    position = extras.get("car_position")
    if position:
        return f"第 {position} 車為{description}。確認後請回答：完成。"
    return f"{description}。確認後請回答：完成。"


def render_question(node: TroubleshootingNode) -> str:
    return node.question or "請提供判斷結果。"


def repeat_current_prompt(node: TroubleshootingNode) -> str:
    if node.type == "decision":
        return render_question(node)
    if node.type == "action":
        return "完成後請回答：完成。"
    return "請針對目前步驟回答。"


def clarification_for_node(node: TroubleshootingNode) -> str:
    expected = expected_input_for_node(node)
    if expected and expected.type == "number":
        unit = expected.unit or ""
        return f"請直接告訴我目前{expected.field or '數值'}的數值，例如 25{unit}。"
    if expected and expected.type == "boolean":
        return f"請明確回答目前這一項：{render_question(node)}"
    if expected and expected.type == "enum":
        return f"請依目前這一題回答：{render_question(node)} 可回答：{'、'.join(expected.options)}。"
    if expected and expected.type == "action_done":
        return "請先完成目前這一步，完成後回答：完成。"
    return repeat_current_prompt(node)







