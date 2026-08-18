from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from astrbot.core.memory import MemoryStore, PersonaStateService


@pytest.mark.asyncio
async def test_persona_state_first_evolution_can_be_rolled_back(
    temp_dir: Path,
) -> None:
    store = MemoryStore(db_path=temp_dir / "memory.db")
    service = PersonaStateService(store)
    now = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    try:
        result = await service.apply_reflection(
            "user-1",
            persona_id="persona-1",
            deltas={
                "familiarity": 0.5,
                "trust": 0.2,
                "directness_preference": -0.5,
            },
            confidence=0.9,
            reason="Repeated direct collaboration",
            source_refs=["experience:exp-1"],
            now=now,
        )

        assert result is not None
        assert result.state is not None
        assert result.state.familiarity == 0.08
        assert result.state.trust == 0.58
        assert result.state.directness_preference == 0.42
        assert result.log.before_state is None

        rolled_back = await service.rollback(result.log.log_id, now=now + timedelta(hours=1))

        assert rolled_back.state is None
        assert rolled_back.log.after_state == {}
        assert await service.get_state("user-1") is None
        assert len(await service.list_evolution_logs("user-1")) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_persona_state_rollback_restores_previous_state(
    temp_dir: Path,
) -> None:
    store = MemoryStore(db_path=temp_dir / "memory.db")
    service = PersonaStateService(store)
    first_at = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)
    second_at = first_at + timedelta(hours=25)
    try:
        first = await service.apply_reflection(
            "user-2",
            persona_id="persona-1",
            deltas={"warmth": 0.06},
            confidence=0.8,
            now=first_at,
        )
        second = await service.apply_reflection(
            "user-2",
            persona_id="persona-1",
            deltas={"warmth": -0.04},
            confidence=0.8,
            now=second_at,
        )

        assert first is not None and first.state is not None
        assert second is not None and second.state is not None
        assert second.state.warmth == pytest.approx(0.52)

        rolled_back = await service.rollback(
            second.log.log_id,
            now=second_at + timedelta(hours=1),
        )

        assert rolled_back.state is not None
        assert rolled_back.state.state_id == first.state.state_id
        assert rolled_back.state.warmth == pytest.approx(first.state.warmth)
    finally:
        await store.close()
