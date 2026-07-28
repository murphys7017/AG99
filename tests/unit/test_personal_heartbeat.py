from types import SimpleNamespace

import pytest

from astrbot.core.interaction.personal_heartbeat import PersonalHeartbeatSource
from astrbot.core.platform.astr_message_event import MessageSesion
from astrbot.core.platform.platform_metadata import PlatformMetadata


class _Context:
    def __init__(self, session, metadata):
        self._session = session
        self._platform = SimpleNamespace(meta=lambda: metadata)

    def get_runtime_observation_targets(self):
        return (self._session,)

    def get_platform_inst(self, platform_id):
        assert platform_id == self._session.platform_id
        return self._platform


class _ConfigManager:
    def __init__(self, config):
        self._config = config

    def get_conf(self, _session):
        return self._config

    def get_conf_info(self, _session):
        return {"id": "default"}


class _RuntimeManager:
    def __init__(self):
        self.observations = []
        self.idle_initiations = []

    async def submit_observation(self, observation, **_kwargs):
        self.observations.append(observation)
        return SimpleNamespace(
            status=SimpleNamespace(value="ignored"),
            reason_codes=("heartbeat_without_material",),
        )

    async def submit_idle_initiation(self, target, **kwargs):
        self.idle_initiations.append((target, kwargs))
        return SimpleNamespace(
            status=SimpleNamespace(value="admitted"),
            reason_codes=(),
        )


@pytest.mark.asyncio
async def test_heartbeat_diagnostics_report_last_empty_inbox_admission():
    session = MessageSesion.from_str("test:FriendMessage:target")
    metadata = PlatformMetadata(
        name="test",
        description="test",
        id="test",
        support_proactive_message=True,
        support_personal_runtime=True,
    )
    config = {
        "interaction_middleware": {
            "personal_heartbeat_enabled": True,
            "personal_heartbeat_interval_seconds": 30,
        }
    }
    runtime_manager = _RuntimeManager()
    source = PersonalHeartbeatSource(
        context=_Context(session, metadata),
        config_manager=_ConfigManager(config),
        runtime_manager=runtime_manager,
    )

    await source.tick()

    diagnostics = source.diagnostics_view()
    assert len(runtime_manager.observations) == 1
    target = diagnostics["targets"][0]
    assert target["umo"] == "test:FriendMessage:target"
    assert target["heartbeat_enabled"] is True
    assert target["interval_seconds"] == 30.0
    assert target["scheduler_state"] == "scheduled"
    assert target["last_submission_status"] == "ignored"
    assert target["last_submission_reason_codes"] == ["heartbeat_without_material"]
    assert target["last_submission_at"] is not None
    assert target["next_tick_at"] == pytest.approx(target["last_submission_at"] + 30)
    assert 0 <= target["seconds_until_next_tick"] <= 30


@pytest.mark.asyncio
async def test_heartbeat_submits_explicitly_enabled_idle_initiation():
    session = MessageSesion.from_str("test:FriendMessage:target")
    metadata = PlatformMetadata(
        name="test",
        description="test",
        id="test",
        support_proactive_message=True,
        support_personal_runtime=True,
    )
    config = {
        "interaction_middleware": {
            "personal_heartbeat_enabled": True,
            "personal_heartbeat_interval_seconds": 30,
            "personal_idle_initiation_enabled": True,
            "personal_idle_initiation_after_seconds": 120,
        }
    }
    runtime_manager = _RuntimeManager()
    source = PersonalHeartbeatSource(
        context=_Context(session, metadata),
        config_manager=_ConfigManager(config),
        runtime_manager=runtime_manager,
    )

    results = await source.tick()

    assert len(results) == 2
    assert len(runtime_manager.idle_initiations) == 1
    target_session, kwargs = runtime_manager.idle_initiations[0]
    assert target_session.unified_msg_origin == "test:FriendMessage:target"
    assert kwargs["minimum_idle_seconds"] == 120.0
    target = source.diagnostics_view()["targets"][0]
    assert target["idle_initiation_enabled"] is True
    assert target["idle_initiation_after_seconds"] == 120.0
    assert target["last_idle_initiation_status"] == "admitted"
