from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


@dataclass(slots=True)
class InteractionMemorySnapshot:
    session_id: str
    persona_id: str = ""
    recent_turns: list[dict[str, str]] = field(default_factory=list)
    speaking_style_notes: list[str] = field(default_factory=list)
    user_preferences: list[str] = field(default_factory=list)
    relationship_notes: list[str] = field(default_factory=list)
    recent_topics: list[str] = field(default_factory=list)
    ongoing_threads: list[str] = field(default_factory=list)
    last_impression_summary: str = ""

    @classmethod
    def from_mapping(
        cls,
        session_id: str,
        payload: object,
    ) -> InteractionMemorySnapshot:
        if not isinstance(payload, dict):
            return cls(session_id=session_id)
        return cls(
            session_id=session_id,
            persona_id=str(payload.get("persona_id", "") or ""),
            recent_turns=_coerce_turn_list(payload.get("recent_turns")),
            speaking_style_notes=_coerce_str_list(payload.get("speaking_style_notes")),
            user_preferences=_coerce_str_list(payload.get("user_preferences")),
            relationship_notes=_coerce_str_list(payload.get("relationship_notes")),
            recent_topics=_coerce_str_list(payload.get("recent_topics")),
            ongoing_threads=_coerce_str_list(payload.get("ongoing_threads")),
            last_impression_summary=str(
                payload.get("last_impression_summary", "") or ""
            ),
        )


def _coerce_str_list(payload: object) -> list[str]:
    if not isinstance(payload, list):
        return []
    return [str(item).strip() for item in payload if str(item).strip()]


def _coerce_turn_list(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        return []
    turns: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        user_text = str(item.get("user", "") or "").strip()
        assistant_text = str(item.get("assistant", "") or "").strip()
        turn_id = str(item.get("turn_id", "") or "").strip()
        if not user_text and not assistant_text:
            continue
        turn = {
            "user": user_text,
            "assistant": assistant_text,
        }
        if turn_id:
            turn["turn_id"] = turn_id
        turns.append(turn)
    return turns[-12:]


class InteractionMemoryStore:
    def __init__(self) -> None:
        self._base_dir = Path(get_astrbot_data_path()) / "interaction_memory"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[Path, asyncio.Lock] = {}

    def _get_session_path(self, session_id: str) -> Path:
        safe_name = (
            session_id.replace(":", "__")
            .replace("/", "_")
            .replace("\\", "_")
            .replace("!", "_")
        )
        return self._base_dir / f"{safe_name}.json"

    def _get_session_lock(self, path: Path) -> asyncio.Lock:
        lock = self._locks.get(path)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[path] = lock
        return lock

    async def load_interaction_memory(
        self,
        session_id: str,
        persona_id: str,
    ) -> InteractionMemorySnapshot:
        path = self._get_session_path(session_id)
        async with self._get_session_lock(path):
            return await self._load_interaction_memory_unlocked(
                path,
                session_id,
                persona_id,
            )

    async def save_interaction_memory(
        self,
        session_id: str,
        snapshot: InteractionMemorySnapshot,
    ) -> None:
        path = self._get_session_path(session_id)
        async with self._get_session_lock(path):
            await self._save_interaction_memory_unlocked(path, snapshot)

    async def update_interaction_memory(
        self,
        session_id: str,
        persona_id: str,
        updater: Callable[[InteractionMemorySnapshot], InteractionMemorySnapshot],
    ) -> InteractionMemorySnapshot:
        path = self._get_session_path(session_id)
        async with self._get_session_lock(path):
            snapshot = await self._load_interaction_memory_unlocked(
                path,
                session_id,
                persona_id,
            )
            updated = updater(snapshot)
            await self._save_interaction_memory_unlocked(path, updated)
            return updated

    async def _load_interaction_memory_unlocked(
        self,
        path: Path,
        session_id: str,
        persona_id: str,
    ) -> InteractionMemorySnapshot:
        if not await asyncio.to_thread(path.exists):
            return InteractionMemorySnapshot(
                session_id=session_id, persona_id=persona_id
            )
        try:
            payload_text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            payload = json.loads(payload_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load interaction memory: session_id=%s path=%s error=%s",
                session_id,
                path,
                exc,
            )
            return InteractionMemorySnapshot(
                session_id=session_id, persona_id=persona_id
            )
        snapshot = InteractionMemorySnapshot.from_mapping(session_id, payload)
        if persona_id and not snapshot.persona_id:
            snapshot.persona_id = persona_id
        return snapshot

    @staticmethod
    async def _save_interaction_memory_unlocked(
        path: Path,
        snapshot: InteractionMemorySnapshot,
    ) -> None:
        payload = json.dumps(asdict(snapshot), ensure_ascii=False, indent=2)
        await asyncio.to_thread(path.write_text, payload, encoding="utf-8")


def build_interaction_memory_payload(
    snapshot: InteractionMemorySnapshot,
) -> dict[str, Any]:
    return {
        "persona_id": snapshot.persona_id,
        "recent_turns": list(snapshot.recent_turns),
        "speaking_style_notes": list(snapshot.speaking_style_notes),
        "user_preferences": list(snapshot.user_preferences),
        "relationship_notes": list(snapshot.relationship_notes),
        "recent_topics": list(snapshot.recent_topics),
        "ongoing_threads": list(snapshot.ongoing_threads),
        "last_impression_summary": snapshot.last_impression_summary,
    }


def update_interaction_memory_from_turn(
    snapshot: InteractionMemorySnapshot,
    *,
    user_text: str,
    visible_reply: str | None,
    turn_id: str | None = None,
) -> InteractionMemorySnapshot:
    user_text = (user_text or "").strip()
    visible_reply = (visible_reply or "").strip()
    if user_text:
        snapshot.recent_topics = [user_text[:80], *snapshot.recent_topics][:6]
    if user_text or visible_reply:
        clean_turn_id = (turn_id or "").strip()
        new_turn = {
            "user": user_text[:500],
            "assistant": visible_reply[:500],
        }
        if clean_turn_id:
            new_turn["turn_id"] = clean_turn_id
        remaining_turns = []
        for turn in snapshot.recent_turns:
            if clean_turn_id and turn.get("turn_id") == clean_turn_id:
                continue
            remaining_turns.append(turn)
        snapshot.recent_turns = [new_turn, *remaining_turns][:12]
    if visible_reply:
        snapshot.last_impression_summary = visible_reply[:160]
    return snapshot


def build_interaction_memory_reply_from_visible_outputs(
    visible_outputs: list[dict[str, Any]] | None,
    *,
    turn_id: str | None = None,
    utterances: list[Any] | None = None,
) -> str:
    if isinstance(utterances, list):
        parts: list[str] = []
        for u in utterances:
            kind = str(getattr(u, "kind", "") or "")
            if kind == "stream_interjection":
                continue
            if not bool(getattr(u, "memory_relevant", True)):
                continue
            text = str(getattr(u, "text", "") or "").strip()
            if text:
                parts.append(text)
        if parts:
            return " ".join(parts).strip()

    if not isinstance(visible_outputs, list):
        return ""
    clean_turn_id = (turn_id or "").strip()
    parts: list[str] = []
    for item in visible_outputs:
        if not isinstance(item, dict):
            continue
        if (
            clean_turn_id
            and str(item.get("turn_id", "") or "").strip() != clean_turn_id
        ):
            continue
        if not bool(item.get("memory_relevant", True)):
            continue
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        parts.append(text)
    return " ".join(parts).strip()
