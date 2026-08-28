from asyncio import Event, create_task, sleep
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.persona_resolution import resolve_event_persona


def _make_event():
    extras: dict[str, object] = {}
    event = MagicMock()
    event.unified_msg_origin = "test:FriendMessage:user"
    event.get_platform_name.return_value = "test"
    event.get_extra.side_effect = lambda key, default=None: extras.get(key, default)
    event.set_extra.side_effect = lambda key, value: extras.__setitem__(key, value)
    return event


@pytest.mark.asyncio
async def test_event_persona_resolution_shares_one_inflight_result():
    event = _make_event()
    started = Event()
    release = Event()
    async def resolve_selected_persona(**_kwargs):
        started.set()
        await release.wait()
        return ("persona-a", {"name": "persona-a"}, None, False)

    resolver = AsyncMock(side_effect=resolve_selected_persona)
    persona_manager = MagicMock(resolve_selected_persona=resolver)

    first = create_task(
        resolve_event_persona(
            event=event,
            persona_manager=persona_manager,
            conversation_persona_id="persona-a",
            provider_settings={"default_personality": "persona-a"},
        )
    )
    await started.wait()
    second = create_task(
        resolve_event_persona(
            event=event,
            persona_manager=persona_manager,
            conversation_persona_id="persona-a",
            provider_settings={"default_personality": "persona-a"},
        )
    )
    await sleep(0)
    assert resolver.await_count == 1

    release.set()
    first_result, second_result = await first, await second

    assert first_result == second_result
    assert first_result.persona_id == "persona-a"
    assert first_result.persona == {"name": "persona-a"}

    cached_result = await resolve_event_persona(
        event=event,
        persona_manager=persona_manager,
        conversation_persona_id="persona-a",
        provider_settings={"default_personality": "persona-a"},
    )
    assert cached_result == first_result
    assert resolver.await_count == 1

    await resolve_event_persona(
        event=event,
        persona_manager=persona_manager,
        conversation_persona_id="persona-a",
        provider_settings={"default_personality": "persona-b"},
    )
    assert resolver.await_count == 2


@pytest.mark.asyncio
async def test_event_persona_resolution_does_not_cache_failures():
    event = _make_event()
    resolver = AsyncMock(side_effect=RuntimeError("persona unavailable"))
    persona_manager = MagicMock(resolve_selected_persona=resolver)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="persona unavailable"):
            await resolve_event_persona(
                event=event,
                persona_manager=persona_manager,
                conversation_persona_id=None,
                provider_settings={"default_personality": "persona-a"},
            )

    assert resolver.await_count == 2
