import pytest

from astrbot.core.executors.external import ExternalExecutorSessionKey
from astrbot.core.executors.session_registry import ExternalExecutorSessionRegistry


class _Manager:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.__class__.instances.append(self)

    async def start(self):
        self.started = True

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_registry_reuses_key_and_closes_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "astrbot.core.executors.session_registry.CodexSessionManager", _Manager
    )
    root = tmp_path / "root"
    root.mkdir()
    key = ExternalExecutorSessionKey(
        executor_id="codex_cli",
        runtime_config_id="bot-a",
        session_id="session-a",
        workspace_root=root,
        workspace=root,
    )
    registry = ExternalExecutorSessionRegistry()
    first = await registry.get_or_create(key=key, executor_config={})
    second = await registry.get_or_create(key=key, executor_config={})
    assert first is second
    assert len(_Manager.instances) == 1
    await registry.aclose()
    assert first.closed is True


@pytest.mark.asyncio
async def test_registry_rejects_command_arguments(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    key = ExternalExecutorSessionKey("codex_cli", "bot", "session", root, root)
    with pytest.raises(ValueError, match="must not contain command arguments"):
        await ExternalExecutorSessionRegistry().get_or_create(
            key=key,
            executor_config={"executable": "codex --unsafe"},
        )
