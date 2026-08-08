from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType


class _RecordingConfigRouter:
    def __init__(self) -> None:
        self.origins: list[str] = []

    def get_conf_id_for_umop(self, umo: str) -> str | None:
        self.origins.append(umo)
        if umo == "alice:FriendMessage:815049548":
            return "alice-config"
        return None


def test_message_session_uses_canonical_umo_for_config_routing() -> None:
    default_config = object()
    alice_config = object()
    router = _RecordingConfigRouter()
    manager = AstrBotConfigManager.__new__(AstrBotConfigManager)
    manager.ucr = router
    manager.abconf_data = {
        "alice-config": {
            "path": "alice.json",
            "name": "Alice",
        }
    }
    manager.confs = {
        "default": default_config,
        "alice-config": alice_config,
    }
    session = MessageSession(
        "alice",
        MessageType.FRIEND_MESSAGE,
        "815049548",
    )

    assert manager.get_conf(session) is alice_config
    assert manager.get_conf_info(session)["id"] == "alice-config"
    assert router.origins == [
        "alice:FriendMessage:815049548",
        "alice:FriendMessage:815049548",
    ]
