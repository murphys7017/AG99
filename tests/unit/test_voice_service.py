from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.message.components import Record
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.voice import (
    VoiceServiceError,
    resolve_stt_provider,
    resolve_tts_provider,
    synthesize_text,
    transcribe_record,
)


@pytest.fixture
def voice_event():
    platform_meta = PlatformMetadata(
        name="webchat",
        description="webchat",
        id="webchat",
    )
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "webchat"
    message.session_id = "webchat!user!session123"
    message.message_id = "msg123"
    message.sender = MessageMember(user_id="user123", nickname="TestUser")
    return AstrMessageEvent(
        message_str="hello",
        message_obj=message,
        platform_meta=platform_meta,
        session_id="webchat!user!session123",
    )


class ProviderMeta:
    id = "voice-provider"


class FakeSTTProvider:
    def meta(self):
        return ProviderMeta()

    async def get_text(self, audio_url: str) -> str:
        assert audio_url == "voice.wav"
        return "recognized"


class FakeTTSProvider:
    def meta(self):
        return ProviderMeta()

    async def get_audio(self, text: str) -> str:
        assert text == "spoken"
        return "spoken.wav"


def test_resolve_voice_provider_reports_missing_context(voice_event):
    with pytest.raises(VoiceServiceError) as exc_info:
        resolve_stt_provider(None, voice_event, stage="unit.stt")

    assert exc_info.value.reason == "plugin_context_unavailable"
    assert exc_info.value.stage == "unit.stt"


def test_resolve_voice_provider_reports_missing_provider(voice_event):
    plugin_context = MagicMock()
    plugin_context.get_using_tts_provider.return_value = None

    with pytest.raises(VoiceServiceError) as exc_info:
        resolve_tts_provider(plugin_context, voice_event, stage="unit.tts")

    assert exc_info.value.reason == "provider_unavailable"
    assert exc_info.value.stage == "unit.tts"


@pytest.mark.asyncio
async def test_transcribe_record_returns_metadata(voice_event):
    record = Record.fromFileSystem("voice.wav")

    with patch.object(
        Record,
        "convert_to_file_path",
        new=AsyncMock(return_value="voice.wav"),
    ):
        result = await transcribe_record(
            MagicMock(),
            voice_event,
            record,
            provider=FakeSTTProvider(),
            stage="unit",
        )

    assert result.text == "recognized"
    assert result.audio_path == "voice.wav"
    assert result.provider_id == "voice-provider"
    assert result.metadata["stage"] == "unit"
    assert result.metadata["provider_id"] == "voice-provider"


@pytest.mark.asyncio
async def test_synthesize_text_registers_file_when_requested(voice_event):
    with patch(
        "astrbot.core.voice.service.file_token_service.register_file",
        new=AsyncMock(return_value="token-1"),
    ):
        result = await synthesize_text(
            MagicMock(),
            voice_event,
            "spoken",
            provider=FakeTTSProvider(),
            stage="unit",
            use_file_service=True,
            callback_api_base="http://localhost:6185",
        )

    assert result.text == "spoken"
    assert result.audio_path == "spoken.wav"
    assert result.audio_url == "http://localhost:6185/api/file/token-1"
    assert result.delivered_file == "http://localhost:6185/api/file/token-1"
    assert result.provider_id == "voice-provider"
    assert result.metadata["stage"] == "unit"


@pytest.mark.asyncio
async def test_synthesize_text_wraps_file_registration_failure(voice_event):
    with patch(
        "astrbot.core.voice.service.file_token_service.register_file",
        new=AsyncMock(side_effect=RuntimeError("registry down")),
    ):
        with pytest.raises(VoiceServiceError) as exc_info:
            await synthesize_text(
                MagicMock(),
                voice_event,
                "spoken",
                provider=FakeTTSProvider(),
                stage="unit",
                use_file_service=True,
                callback_api_base="http://localhost:6185",
            )

    assert exc_info.value.reason == "file_registration_failed"
    assert exc_info.value.stage == "unit"
    assert exc_info.value.provider_id == "voice-provider"
    assert exc_info.value.metadata["audio_path"] == "spoken.wav"


@pytest.mark.asyncio
async def test_synthesize_text_can_require_file_registration_config(voice_event):
    with pytest.raises(VoiceServiceError) as exc_info:
        await synthesize_text(
            MagicMock(),
            voice_event,
            "spoken",
            provider=FakeTTSProvider(),
            stage="unit",
            use_file_service=True,
            callback_api_base="",
            require_file_registration_config=True,
        )

    assert exc_info.value.reason == "file_registration_config_missing"
    assert exc_info.value.stage == "unit"
    assert exc_info.value.provider_id == "voice-provider"
    assert exc_info.value.metadata["audio_path"] == "spoken.wav"
