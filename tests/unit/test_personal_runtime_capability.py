from types import SimpleNamespace

from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.context import Context


class _Platform:
    def __init__(self, metadata: PlatformMetadata) -> None:
        self._metadata = metadata

    def meta(self) -> PlatformMetadata:
        return self._metadata


def _context_for_target(metadata: PlatformMetadata) -> Context:
    context = Context.__new__(Context)
    context._config = {
        "platform_settings": {
            "proactive_message_target": "demo:FriendMessage:target",
            "personal_runtime_observation_targets": ["demo:FriendMessage:target"],
        }
    }
    context.platform_manager = SimpleNamespace(platform_insts=[_Platform(metadata)])
    return context


def _metadata(*, support_personal_runtime: bool = False) -> PlatformMetadata:
    return PlatformMetadata(
        name="demo",
        description="demo",
        id="demo",
        support_proactive_message=True,
        support_personal_runtime=support_personal_runtime,
    )


def test_personal_runtime_targets_require_explicit_adapter_support():
    context = _context_for_target(_metadata())

    assert context.get_proactive_message_target() is not None
    assert context.get_runtime_observation_targets() == ()


def test_personal_runtime_targets_accept_explicit_adapter_support():
    context = _context_for_target(_metadata(support_personal_runtime=True))

    targets = context.get_runtime_observation_targets()

    assert len(targets) == 1
    assert str(targets[0]) == "demo:FriendMessage:target"
