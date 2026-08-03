from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Iterable
from typing import Any

from .store import MemoryStore
from .types import JsonDict, TurnRecord


def normalize_message_payload(message: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {"role": "", "content": ""}

    role = str(message.get("role", "") or "")
    content = extract_message_text(message)
    normalized = {"role": role, "content": content}

    if name := message.get("name"):
        normalized["name"] = name
    return normalized


def extract_message_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        return _clean_text(_strip_system_reminder(content))

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "text":
                parts.append(str(item.get("text", "") or ""))
            elif item_type == "image_url":
                parts.append("[image]")
            elif isinstance(item_type, str) and item_type.strip():
                parts.append("[attachment]")
        return _clean_text(
            _strip_system_reminder(" ".join(part for part in parts if part))
        )

    if content is None and message.get("tool_calls"):
        return "[tool_call]"

    return ""


def extract_turn_payloads(
    messages: Iterable[dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[JsonDict]:
    payloads: list[JsonDict] | deque[JsonDict]
    payloads = deque(maxlen=max(0, limit)) if limit is not None else []
    pending_user: dict[str, Any] | None = None
    candidate_assistant: dict[str, Any] | None = None

    for raw_message in messages:
        if not isinstance(raw_message, dict):
            continue

        message = normalize_message_payload(raw_message)
        role = str(raw_message.get("role", "") or "")

        if role == "user":
            _finalize_pending_turn(payloads, pending_user, candidate_assistant)
            pending_user = message
            candidate_assistant = None
            continue

        if role != "assistant":
            if role == "tool":
                continue
            continue

        if pending_user is None:
            if message.get("content") and not _is_intermediate_assistant(
                raw_message,
                message,
            ):
                payloads.append(
                    {
                        "user_message": {},
                        "assistant_message": message,
                        "assistant_only": True,
                    }
                )
            continue

        if not pending_user.get("content"):
            pending_user = None
            candidate_assistant = None
            continue

        if _is_intermediate_assistant(raw_message, message):
            if message.get("content"):
                candidate_assistant = message
            continue

        if not message.get("content"):
            continue

        payloads.append(
            {
                "user_message": pending_user,
                "assistant_message": message,
            }
        )
        pending_user = None
        candidate_assistant = None

    _finalize_pending_turn(payloads, pending_user, candidate_assistant)

    return list(payloads)


def parse_conversation_history(
    history: str | list[dict[str, Any]] | None,
) -> list[dict]:
    if isinstance(history, list):
        return [item for item in history if isinstance(item, dict)]
    if not isinstance(history, str) or not history.strip():
        return []

    try:
        payload = json.loads(history)
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


class RecentConversationSource:
    def __init__(self, store: MemoryStore, recent_turns_window: int = 8) -> None:
        self.store = store
        self.recent_turns_window = recent_turns_window

    async def get_recent_turn_payloads(
        self,
        *,
        conversation_history: list[dict[str, Any]] | None = None,
        umo: str | None = None,
        conversation_id: str | None = None,
        limit: int | None = None,
    ) -> list[JsonDict]:
        effective_limit = limit or self.recent_turns_window

        if conversation_history:
            payloads = extract_turn_payloads(conversation_history)
            return payloads[-effective_limit:]

        if not umo:
            return []

        records = await self.store.get_recent_turn_records(
            umo,
            effective_limit,
            conversation_id=conversation_id,
        )
        records.reverse()
        return [self._turn_record_to_payload(record) for record in records]

    @staticmethod
    def get_latest_turn_payload(
        conversation_history: list[dict[str, Any]] | None,
    ) -> JsonDict | None:
        if not conversation_history:
            return None
        payloads = extract_turn_payloads(conversation_history)
        if not payloads:
            return None
        return payloads[-1]

    @staticmethod
    def _turn_record_to_payload(record: TurnRecord) -> JsonDict:
        user_message = normalize_message_payload(record.user_message)
        return {
            "user_message": user_message if user_message.get("content") else {},
            "assistant_message": normalize_message_payload(record.assistant_message),
            "assistant_only": not bool(user_message.get("content")),
        }


def _strip_system_reminder(text: str) -> str:
    return re.sub(r"<system_reminder>.*?</system_reminder>", "", text, flags=re.DOTALL)


def _clean_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _is_intermediate_assistant(
    raw_message: dict[str, Any],
    normalized_message: dict[str, Any],
) -> bool:
    if raw_message.get("tool_calls"):
        return True

    content = normalized_message.get("content")
    if not isinstance(content, str):
        return False
    return content == "[tool_call]"


def _finalize_pending_turn(
    payloads: list[JsonDict] | deque[JsonDict],
    pending_user: dict[str, Any] | None,
    candidate_assistant: dict[str, Any] | None,
) -> None:
    if pending_user is None or candidate_assistant is None:
        return
    if not pending_user.get("content") or not candidate_assistant.get("content"):
        return

    payloads.append(
        {
            "user_message": pending_user,
            "assistant_message": candidate_assistant,
        }
    )
