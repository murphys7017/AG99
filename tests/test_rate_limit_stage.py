import asyncio
from datetime import datetime as real_datetime
from datetime import timedelta
from types import SimpleNamespace

import pytest

from astrbot.core.interaction.turn_state import (
    set_interaction_turn_configuration_selection,
)
from astrbot.core.pipeline.rate_limit_check import stage as rate_limit_stage


def _rate_config(*, count: int, seconds: int, strategy: str) -> dict:
    return {
        "platform_settings": {
            "rate_limit": {
                "count": count,
                "time": seconds,
                "strategy": strategy,
            }
        }
    }


class FakeEvent:
    """Minimal message event used by the rate-limit stage tests."""

    session_id = "test-session"
    unified_msg_origin = "test-platform:GroupMessage:test-session"

    def __init__(self) -> None:
        self._extras: dict[str, object] = {}
        self.stopped = False

    def get_extra(self, key: str, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key: str, value) -> None:
        self._extras[key] = value

    def stop_event(self) -> None:
        """Stop event propagation for discard-strategy compatibility."""
        self.stopped = True


@pytest.mark.asyncio
async def test_different_platforms_do_not_share_rate_limit_state():
    limiter = rate_limit_stage.RateLimitStage()
    await limiter.initialize(
        SimpleNamespace(
            astrbot_config=_rate_config(count=1, seconds=60, strategy="discard")
        )
    )

    first_event = FakeEvent()
    first_event.session_id = "same-session"
    first_event.unified_msg_origin = "platform-a:GroupMessage:same-session"
    second_event = FakeEvent()
    second_event.session_id = "same-session"
    second_event.unified_msg_origin = "platform-b:GroupMessage:same-session"

    await limiter.process(first_event)
    await limiter.process(second_event)

    assert set(limiter.event_timestamps) == {
        first_event.unified_msg_origin,
        second_event.unified_msg_origin,
    }


@pytest.mark.asyncio
async def test_stalled_concurrent_events_use_current_time_after_lock(monkeypatch):
    """Ensure queued events do not reuse timestamps captured before lock waits."""
    virtual_seconds = 0.0
    sleep_durations: list[float] = []
    real_sleep = asyncio.sleep
    base_time = real_datetime(2026, 1, 1)

    class FakeDateTime(real_datetime):
        """Subclass of datetime with a deterministic now()."""

        @classmethod
        def now(cls) -> real_datetime:
            return base_time + timedelta(seconds=virtual_seconds)

    async def fake_sleep(duration: float) -> None:
        nonlocal virtual_seconds
        sleep_durations.append(duration)
        target_time = virtual_seconds + duration
        await real_sleep(0)
        virtual_seconds = target_time

    monkeypatch.setattr(rate_limit_stage, "datetime", FakeDateTime)
    monkeypatch.setattr(rate_limit_stage.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(rate_limit_stage.logger, "info", lambda *args, **kwargs: None)

    limiter = rate_limit_stage.RateLimitStage()
    await limiter.initialize(
        SimpleNamespace(
            astrbot_config=_rate_config(count=2, seconds=60, strategy="stall")
        )
    )

    await asyncio.gather(*(limiter.process(FakeEvent()) for _ in range(5)))

    expected_stall = timedelta(seconds=60).total_seconds() + 0.3
    assert sleep_durations == pytest.approx([expected_stall, expected_stall])
    timestamps = list(limiter.event_timestamps[FakeEvent.unified_msg_origin])
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_rate_limit_uses_frozen_turn_configuration_not_pipeline_default():
    limiter = rate_limit_stage.RateLimitStage()
    await limiter.initialize(
        SimpleNamespace(
            astrbot_config=_rate_config(count=0, seconds=60, strategy="discard")
        )
    )
    first = FakeEvent()
    second = FakeEvent()
    selected_config = _rate_config(count=1, seconds=60, strategy="discard")
    for event in (first, second):
        set_interaction_turn_configuration_selection(
            event,
            config_id="selected",
            runtime_config=selected_config,
            adapter_binding_id="selected-binding",
            provider_references={},
        )

    await limiter.process(first)
    await limiter.process(second)

    assert second.stopped is True
