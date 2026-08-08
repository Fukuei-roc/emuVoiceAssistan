from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime

from openai import APIStatusError, AuthenticationError, OpenAI, OpenAIError

from app.config import settings
from app.knowledge import MarkdownKnowledgeBase
from app.models import ChatRequest, ChatResponse, ConversationSession

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是 EMU800 型電聯車故障處理訓練助手。

你的任務是根據系統提供的 EMU800 故障處理知識，逐步引導司機員確認故障。

規則：
1. 目前車型固定為 EMU800。
2. 只能依本輪提供的【允許引用的手冊內容】回答故障處置。
3. 不得使用常識、推測、其他車型經驗或未提供的手冊內容補足答案。
4. 資料沒有記載時，直接說目前資料不足，並請使用者補充故障現象或查閱手冊。
5. 故障排除應逐步進行。
6. 每次原則上只問一個最重要的問題。
7. 不要一次把完整故障處理手冊全部念出來。
8. 使用者回答後，要根據回答繼續下一個合理步驟。
9. 已經確認過的資訊不要重複詢問。
10. 涉及數值時，保留手冊中的原始門檻。
11. 涉及強迫激磁、隔離、SIV轉供、降弓、KEY OFF、考克等操作，不得自行增加或簡化。
12. 若知識內容寫「上述處理無效」，只有在使用者表示前一步無效後才能進入下一步。
13. 若使用者表示故障已恢復，停止繼續後續處置。
14. 回答適合語音播放，簡短、直接、清楚。
15. 不需要長篇解釋，除非使用者詢問原因。
16. 若檢索結果中有多個章節，優先使用最符合目前故障與目前對話的章節；不要混用不相關章節。
17. 回答前先在內部判斷目前應停留的手冊步驟，但不要輸出推理過程。
18. 若下一步在【允許引用的手冊內容】中找不到，不得自行延伸。
19. 一次只能提出一個問句；不要把多個確認項目合併在同一則回答。
20. 若使用者只回答數值、狀態或短語，必須先判斷它是否是在回答上一輪問題；例如上一輪詢問電車線電壓時，使用者回答「25KV」就是電車線電壓為 25 kV。
21. 若已能判斷使用者回答落在手冊正常範圍或異常範圍，直接依手冊進入下一個步驟，不要重複詢問同一問題。
"""


class ChatService:
    def __init__(self, knowledge: MarkdownKnowledgeBase) -> None:
        self.knowledge = knowledge
        self.sessions: dict[str, ConversationSession] = {}

    def chat(self, request: ChatRequest) -> ChatResponse:
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key 尚未設定")

        start = time.perf_counter()
        session = self._get_session(request.session_id)
        sections = self.knowledge.search(request.message, limit=3)
        if not sections and session.last_knowledge_sections:
            sections = session.last_knowledge_sections

        prompt = self._build_user_prompt(request.message, session, sections)
        client = OpenAI(api_key=settings.openai_api_key)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in session.messages[-8:]:
            messages.append(item)
        messages.append({"role": "user", "content": prompt})

        try:
            response = client.responses.create(
                model=settings.openai_text_model,
                input=messages,
            )
        except AuthenticationError as exc:
            raise RuntimeError("OpenAI API key 無效或已被拒絕") from exc
        except APIStatusError as exc:
            raise RuntimeError(f"OpenAI API 回傳錯誤：HTTP {exc.status_code}") from exc
        except OpenAIError as exc:
            raise RuntimeError(f"OpenAI API 呼叫失敗：{exc.__class__.__name__}") from exc
        reply = response.output_text.strip()
        latency_ms = int((time.perf_counter() - start) * 1000)

        session.messages.append({"role": "user", "content": request.message})
        session.messages.append({"role": "assistant", "content": reply})
        session.last_knowledge_sections = sections
        session.updated_at = datetime.utcnow()
        logger.info("OpenAI text request latency_ms=%s session=%s", latency_ms, session.session_id)

        return ChatResponse(session_id=session.session_id, reply=reply, sources=sections, latency_ms=latency_ms)

    def _get_session(self, session_id: str | None) -> ConversationSession:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        new_session_id = session_id or str(uuid.uuid4())
        session = ConversationSession(session_id=new_session_id, vehicle=settings.vehicle)
        self.sessions[new_session_id] = session
        return session

    def _build_user_prompt(self, message: str, session: ConversationSession, sections: list) -> str:
        knowledge_text = "\n\n".join(
            f"[{index + 1}] {section.heading}\n來源：{section.source}\n{section.content}"
            for index, section in enumerate(sections)
        )
        recent_dialogue = "\n".join(f"{item['role']}: {item['content']}" for item in session.messages[-8:])
        return f"""你必須嚴格依照下列限制回答：

- 只能根據【允許引用的手冊內容】回答。
- 不得引用未出現在【允許引用的手冊內容】中的故障處置、數值、設備名稱或操作。
- 若【允許引用的手冊內容】不足以決定下一步，回答「目前資料不足」，並只問一個必要的釐清問題。
- 若使用者已回答前一步，請沿用目前對話，不要重新開始流程。
- 不要混用不相關章節；優先使用最符合目前故障現象與對話的章節。
- 一次只能問一個問題；如果手冊下一步有多個確認項目，只問最先需要確認的那一項。
- 若使用者輸入像「25KV」、「有」、「沒有」、「正常」、「無效」這種短回答，先視為對上一個問題的回答，再依手冊決定下一步。
- 若上一題是電車線電壓，且使用者回答 25KV，應判斷其在 19.0 kV ～ 27.5 kV 正常範圍內，然後依手冊進入 VCBOTR 確認。

【允許引用的手冊內容開始】
{knowledge_text or "目前沒有檢索到相關章節。"}
【允許引用的手冊內容結束】

目前對話：
{recent_dialogue or "尚無。"}

使用者：
{message}
"""
