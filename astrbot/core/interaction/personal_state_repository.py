from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core.db import BaseDatabase

from .personal_state import PersonalPersistentState

if TYPE_CHECKING:
    from .personal_runtime import PersonalRuntimeKey


class PersonalStateRepository:
    """Persistence boundary for restart-safe Personal Runtime control state."""

    def __init__(self, db: BaseDatabase) -> None:
        self._db = db

    async def load(
        self,
        key: PersonalRuntimeKey,
    ) -> PersonalPersistentState | None:
        record = await self._db.get_personal_runtime_state(
            key.config_id,
            key.persona_id,
            key.audience_key,
            key.privacy_scope,
        )
        if record is None:
            return None
        return PersonalPersistentState(
            last_expression_at=record.last_expression_at,
            reply_cooldown_until=record.reply_cooldown_until,
            no_action_cooldown_until=record.no_action_cooldown_until,
            mute_until=record.mute_until,
            usage_day=record.usage_day,
            daily_policy_calls=max(0, int(record.daily_policy_calls)),
            daily_proactive_outputs=max(0, int(record.daily_proactive_outputs)),
        )

    async def save(
        self,
        key: PersonalRuntimeKey,
        state: PersonalPersistentState,
    ) -> None:
        await self._db.upsert_personal_runtime_state(
            config_id=key.config_id,
            persona_id=key.persona_id,
            audience_key=key.audience_key,
            privacy_scope=key.privacy_scope,
            last_expression_at=state.last_expression_at,
            reply_cooldown_until=state.reply_cooldown_until,
            no_action_cooldown_until=state.no_action_cooldown_until,
            mute_until=state.mute_until,
            usage_day=state.usage_day,
            daily_policy_calls=state.daily_policy_calls,
            daily_proactive_outputs=state.daily_proactive_outputs,
        )


__all__ = ["PersonalStateRepository"]
