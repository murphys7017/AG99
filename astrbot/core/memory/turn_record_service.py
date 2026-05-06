from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from .store import MemoryStore
from .types import MemoryUpdateRequest, TurnRecord


class TurnRecordService:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def build_turn_record(self, req: MemoryUpdateRequest) -> TurnRecord:
        turn_id = (req.turn_id or "").strip() or str(uuid4())
        return TurnRecord(
            turn_id=turn_id,
            umo=req.umo,
            conversation_id=req.conversation_id,
            platform_id=req.platform_id,
            platform_user_key=req.platform_user_key,
            canonical_user_id=req.canonical_user_id,
            session_id=req.session_id,
            user_message=dict(req.user_message),
            assistant_message=dict(req.assistant_message),
            message_timestamp=req.message_timestamp,
            source_refs=list(req.source_refs),
            created_at=datetime.now(UTC),
        )

    async def ingest_turn(self, req: MemoryUpdateRequest) -> TurnRecord:
        record = await self.build_turn_record(req)
        return await self.store.save_turn_record(record)
