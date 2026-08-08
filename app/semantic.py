from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.models import ExpectedInput, ParsedAnswer

logger = logging.getLogger(__name__)

AMBIGUOUS_WORDS = ["好像", "應該", "可能", "大概", "也許", "不確定", "不知道", "沒注意", "看不清", "不清楚", "差不多", "正常吧"]


class SemanticJSON(BaseModel):
    understood: bool
    ambiguous: bool = False
    value: Any = None
    vehicle: str | None = None
    fault_id: str | None = None
    reason: str | None = None


class NaturalLanguageInterpreter:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        self.model = settings.openai_text_model

    def interpret_vehicle(self, raw: str, available_vehicles: list[str], prompt: str) -> ParsedAnswer:
        logger.info("RAW USER INPUT vehicle raw=%r", raw)
        fast = fast_vehicle(raw, available_vehicles)
        if fast:
            logger.info("FAST PATH vehicle=%s", fast)
            return ParsedAnswer(understood=True, ambiguous=False, value=fast, raw=raw, source="deterministic")
        logger.info("FAST PATH vehicle no match")
        result = self._ai_interpret(
            task="vehicle",
            raw=raw,
            context={"prompt": prompt, "available_vehicles": available_vehicles},
            allowed_values=available_vehicles,
        )
        value = result.vehicle or result.value
        if value not in available_vehicles:
            return ParsedAnswer(understood=False, ambiguous=True, raw=raw, reason=result.reason or "車型不在已載入清單", source="ai")
        logger.info("AI SEMANTIC INTERPRETATION vehicle=%s", value)
        return ParsedAnswer(understood=result.understood, ambiguous=result.ambiguous, value=value, raw=raw, reason=result.reason, source="ai")

    def interpret_fault(self, raw: str, vehicle: str | None, available_faults: list[dict[str, str]], prompt: str) -> ParsedAnswer:
        logger.info("RAW USER INPUT fault raw=%r", raw)
        fast = fast_fault(raw, available_faults)
        if fast:
            logger.info("FAST PATH fault_id=%s", fast)
            return ParsedAnswer(understood=True, ambiguous=False, value=fast, raw=raw, source="deterministic")
        logger.info("FAST PATH fault no match")
        allowed = [fault["fault_id"] for fault in available_faults]
        result = self._ai_interpret(
            task="fault",
            raw=raw,
            context={"prompt": prompt, "vehicle": vehicle, "available_faults": available_faults},
            allowed_values=allowed,
        )
        value = result.fault_id or result.value
        if value not in allowed:
            return ParsedAnswer(understood=False, ambiguous=True, raw=raw, reason=result.reason or "故障不在已載入清單", source="ai")
        logger.info("AI SEMANTIC INTERPRETATION fault_id=%s", value)
        return ParsedAnswer(understood=result.understood, ambiguous=result.ambiguous, value=value, raw=raw, reason=result.reason, source="ai")

    def interpret_answer(self, raw: str, expected: ExpectedInput | None, node_context: dict[str, Any]) -> ParsedAnswer:
        logger.info("RAW USER INPUT node_answer raw=%r expected=%s", raw, expected.model_dump() if expected else None)
        fast = fast_answer(raw, expected)
        if fast is not None:
            logger.info("FAST PATH answer value=%r", fast.value)
            return fast
        logger.info("FAST PATH answer no match")
        if expected is None:
            return ParsedAnswer(understood=True, ambiguous=False, value=raw, raw=raw, source="deterministic")
        result = self._ai_interpret(
            task="answer",
            raw=raw,
            context={"expected_input": expected.model_dump(), "node": node_context},
            allowed_values=expected.options,
        )
        parsed = parsed_from_semantic(result, expected, raw)
        logger.info("AI SEMANTIC INTERPRETATION answer value=%r understood=%s ambiguous=%s", parsed.value, parsed.understood, parsed.ambiguous)
        return parsed

    def _ai_interpret(self, task: Literal["vehicle", "fault", "answer"], raw: str, context: dict[str, Any], allowed_values: list[str]) -> SemanticJSON:
        if not self.client:
            logger.warning("AI semantic interpreter unavailable: OPENAI_API_KEY not configured")
            return SemanticJSON(understood=False, ambiguous=True, reason="OpenAI API key 尚未設定")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "understood": {"type": "boolean"},
                "ambiguous": {"type": "boolean"},
                "value": {"type": ["string", "number", "boolean", "null"]},
                "vehicle": {"type": ["string", "null"]},
                "fault_id": {"type": ["string", "null"]},
                "reason": {"type": ["string", "null"]},
            },
            "required": ["understood", "ambiguous", "value", "vehicle", "fault_id", "reason"],
        }
        system = (
            "你是列車故障排除系統的語意理解器。你只把使用者自然語言轉成 structured value。"
            "禁止輸出 next_node、branch、goto 或任何流程決策。若語意不足或超出 allowed_values，回 ambiguous=true。"
        )
        user = {
            "task": task,
            "raw_user_text": raw,
            "context": context,
            "allowed_values": allowed_values,
            "output_rules": {
                "vehicle": "task=vehicle 時只能從 allowed_values 選 vehicle",
                "fault": "task=fault 時只能從 allowed_values 選 fault_id",
                "answer": "task=answer 時只解析目前問題的 value，不可解析未問到的後續資訊",
            },
        }
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                response_format={"type": "json_schema", "json_schema": {"name": "semantic_interpretation", "strict": True, "schema": schema}},
                temperature=0,
                timeout=10,
            )
            content = response.choices[0].message.content or "{}"
            return SemanticJSON.model_validate_json(content)
        except Exception as exc:
            logger.exception("AI semantic interpretation failed task=%s", task)
            return SemanticJSON(understood=False, ambiguous=True, reason=f"AI semantic interpretation failed: {exc}")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def fast_vehicle(text: str, available_vehicles: list[str]) -> str | None:
    compact = normalize_text(text).upper()
    match = re.search(r"EMU?(700|800|900)", compact)
    if match:
        vehicle = f"EMU{match.group(1)}"
        return vehicle if vehicle in available_vehicles else None
    match = re.search(r"(?<!\d)(700|800|900)(型)?(?!\d)", compact)
    if match:
        vehicle = f"EMU{match.group(1)}"
        return vehicle if vehicle in available_vehicles else None
    chinese = {"七百": "EMU700", "八百": "EMU800", "九百": "EMU900"}
    compact_original = normalize_text(text)
    for token, vehicle in chinese.items():
        if compact_original in {token, f"{token}型", f"{token}形", f"{token}新", f"{token}線", f"emu{token}"} and vehicle in available_vehicles:
            return vehicle
    return None



def vehicle_asr_hint(text: str, available_vehicles: list[str]) -> str | None:
    compact = normalize_text(text)
    hints = {"七百": "EMU700", "八百": "EMU800", "九百": "EMU900"}
    for token, vehicle in hints.items():
        if token in compact and vehicle in available_vehicles:
            return vehicle
    return None

def fast_fault(text: str, available_faults: list[dict[str, str]]) -> str | None:
    compact = normalize_text(text)
    fault_ids = {fault["fault_id"] for fault in available_faults}
    if "vcb_not_close" in fault_ids and ("vcb" in compact or "斷路器" in compact) and any(term in compact for term in ["不閉合", "無法閉合", "合不起來", "沒閉合", "沒有閉合", "不閉和", "不閉盒", "沒合"]):
        return "vcb_not_close"
    return None


def fast_answer(text: str, expected: ExpectedInput | None) -> ParsedAnswer | None:
    if expected is None:
        return ParsedAnswer(understood=True, ambiguous=False, value=text, raw=text, source="deterministic")
    compact = normalize_text(text)
    if any(word in compact for word in AMBIGUOUS_WORDS):
        return ParsedAnswer(understood=False, ambiguous=True, raw=text, reason="回答含模糊詞", source="deterministic")
    if expected.type == "number":
        value = parse_number(text)
        if value is None:
            return None
        return ParsedAnswer(understood=True, ambiguous=False, value=value, raw=text, source="deterministic")
    if expected.type == "boolean":
        if "都正常" in compact or "全部正常" in compact or "後面的都正常" in compact:
            return ParsedAnswer(understood=False, ambiguous=True, raw=text, reason="不可用籠統回答跳過多個節點", source="deterministic")
        value = parse_boolean(text)
        if value is None:
            return None
        return ParsedAnswer(understood=True, ambiguous=False, value=value, raw=text, source="deterministic")
    if expected.type == "enum":
        value = match_option(expected.options, text)
        if value is None:
            return None
        return ParsedAnswer(understood=True, ambiguous=False, value=value, raw=text, source="deterministic")
    if expected.type == "action_done":
        if any(token in compact for token in ["完成", "好了", "已完成", "做完", "處理完", "確認完", "ok"]):
            return ParsedAnswer(understood=True, ambiguous=False, value=True, raw=text, source="deterministic")
        return None
    return None


def parsed_from_semantic(result: SemanticJSON, expected: ExpectedInput, raw: str) -> ParsedAnswer:
    if not result.understood or result.ambiguous:
        return ParsedAnswer(understood=result.understood, ambiguous=result.ambiguous, value=result.value, raw=raw, reason=result.reason, source="ai")
    value = result.value
    if expected.type == "number":
        try:
            return ParsedAnswer(understood=True, ambiguous=False, value=float(value), raw=raw, reason=result.reason, source="ai")
        except (TypeError, ValueError):
            return ParsedAnswer(understood=False, ambiguous=True, raw=raw, reason="AI 回傳非數值", source="ai")
    if expected.type == "boolean":
        if isinstance(value, bool):
            return ParsedAnswer(understood=True, ambiguous=False, value=value, raw=raw, reason=result.reason, source="ai")
        return ParsedAnswer(understood=False, ambiguous=True, raw=raw, reason="AI 回傳非布林", source="ai")
    if expected.type == "enum":
        if isinstance(value, str) and value in expected.options:
            return ParsedAnswer(understood=True, ambiguous=False, value=value, raw=raw, reason=result.reason, source="ai")
        return ParsedAnswer(understood=False, ambiguous=True, raw=raw, reason="AI 回傳不在選項內", source="ai")
    if expected.type == "action_done":
        if value is True:
            return ParsedAnswer(understood=True, ambiguous=False, value=True, raw=raw, reason=result.reason, source="ai")
        return ParsedAnswer(understood=False, ambiguous=True, raw=raw, reason="尚未確認目前操作完成", source="ai")
    return ParsedAnswer(understood=True, ambiguous=False, value=value, raw=raw, reason=result.reason, source="ai")


def parse_number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match:
        return float(match.group(0))
    replacements = {
        "二十五點二": "25.2",
        "二十五": "25",
        "二十六": "26",
        "二十七": "27",
        "二十四": "24",
        "十九": "19",
    }
    normalized = text
    for zh, digit in replacements.items():
        normalized = normalized.replace(zh, digit)
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    return float(match.group(0)) if match else None


def parse_boolean(text: str) -> bool | None:
    compact = normalize_text(text)
    if "都正常" in compact or "全部正常" in compact or "後面的都正常" in compact:
        return None
    if any(token in compact for token in ["沒有", "沒", "無", "未", "不亮", "沒亮", "都沒亮", "沒有激磁", "無激磁", "off", "無效"]):
        return False
    if any(token in compact for token in ["有", "是", "亮", "都有亮", "點亮", "有激磁", "有作動", "吸起來", "正常", "on"]):
        return True
    return None


def match_option(options: list[str], text: str) -> str | None:
    compact = normalize_text(text)
    for option in options:
        normalized = normalize_text(option)
        if normalized in compact or compact in normalized:
            return option
    if "出庫" in compact or "整備" in compact:
        return next((option for option in options if "出庫" in option), None)
    if "行駛" in compact or "運轉" in compact:
        return next((option for option in options if "行駛" in option), None)
    if "單組" in compact or "一組" in compact:
        return next((option for option in options if "單組" in option or "僅一組" in option), None)
    if "兩組" in compact or "雙組" in compact:
        return next((option for option in options if "兩組" in option), None)
    number = parse_number(text)
    if number is not None:
        as_int = str(int(number)) if number.is_integer() else str(number)
        return next((option for option in options if option == as_int), None)
    return None






