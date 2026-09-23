import asyncio

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
async def test_registry_replaces_session_when_configuration_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "astrbot.core.executors.session_registry.CodexSessionManager", _Manager
    )
    root = tmp_path / "root"
    root.mkdir()
    key = ExternalExecutorSessionKey("codex_cli", "bot-a", "session-a", root, root)
    registry = ExternalExecutorSessionRegistry()
    first = await registry.get_or_create(key=key, executor_config={"model": "a"})
    second = await registry.get_or_create(key=key, executor_config={"model": "b"})
    assert first is not second
    assert first.closed is True
    assert second.closed is False
    await registry.aclose()


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


@pytest.mark.asyncio
async def test_registry_starts_different_keys_without_serializing_io(monkeypatch, tmp_path):
    start_count = 0
    all_started = asyncio.Event()
    release = asyncio.Event()

    class _SlowManager(_Manager):
        async def start(self):
            nonlocal start_count
            start_count += 1
            if start_count == 2:
                all_started.set()
            await release.wait()
            self.started = True

    monkeypatch.setattr(
        "astrbot.core.executors.session_registry.CodexSessionManager", _SlowManager
    )
    root = tmp_path / "root"
    root.mkdir()
    registry = ExternalExecutorSessionRegistry()
    key_a = ExternalExecutorSessionKey("codex_cli", "bot", "a", root, root)
    key_b = ExternalExecutorSessionKey("codex_cli", "bot", "b", root, root)
    task_a = asyncio.create_task(
        registry.get_or_create(key=key_a, executor_config={})
    )
    task_b = asyncio.create_task(
        registry.get_or_create(key=key_b, executor_config={})
    )
    await asyncio.wait_for(all_started.wait(), timeout=1)
    release.set()
    await asyncio.gather(task_a, task_b)
    await registry.aclose()


@pytest.mark.asyncio
async def test_registry_serializes_configuration_replacement(monkeypatch, tmp_path):
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class _VersionedManager(_Manager):
        async def start(self):
            if self.kwargs["request_timeout"] == 5.0:
                first_started.set()
                await release_first.wait()
            self.started = True

    monkeypatch.setattr(
        "astrbot.core.executors.session_registry.CodexSessionManager",
        _VersionedManager,
    )
    root = tmp_path / "root"
    root.mkdir()
    key = ExternalExecutorSessionKey("codex_cli", "bot", "session", root, root)
    registry = ExternalExecutorSessionRegistry()
    first = asyncio.create_task(
        registry.get_or_create(
            key=key,
            executor_config={"request_timeout": 5.0},
        )
    )
    await first_started.wait()
    second = asyncio.create_task(
        registry.get_or_create(
            key=key,
            executor_config={"request_timeout": 10.0},
        )
    )
    release_first.set()
    first_manager, second_manager = await asyncio.gather(first, second)
    assert first_manager is not second_manager
    assert first_manager.closed is True
    assert second_manager.closed is False
    await registry.aclose()
