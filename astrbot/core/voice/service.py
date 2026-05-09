from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrbot.core import file_token_service
from astrbot.core.message.components import Record
from astrbot.core.platform.astr_message_event import AstrMessageEvent


class VoiceServiceError(RuntimeError):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        stage: str | None = None,
        provider_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.stage = stage
        self.provider_id = provider_id
        self.metadata = dict(metadata or {})


@dataclass(slots=True)
class SpeechToTextResult:
    text: str
    audio_path: str
    provider_id: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class TextToSpeechResult:
    text: str
    audio_path: str
    audio_url: str | None
    provider_id: str
    metadata: dict[str, Any]

    @property
    def delivered_file(self) -> str:
        return self.audio_url or self.audio_path


def _provider_id(provider: Any) -> str:
    meta = provider.meta() if hasattr(provider, "meta") else None
    meta_id = getattr(meta, "id", None)
    if meta_id:
        return str(meta_id)
    provider_config = getattr(provider, "provider_config", None)
    if isinstance(provider_config, dict):
        configured_id = provider_config.get("id")
        if configured_id:
            return str(configured_id)
    return provider.__class__.__name__


def resolve_stt_provider(
    plugin_context: Any,
    event: AstrMessageEvent,
    *,
    stage: str | None = None,
) -> Any:
    if plugin_context is None:
        raise VoiceServiceError(
            "plugin_context_unavailable",
            "Voice STT plugin context unavailable",
            stage=stage,
        )
    get_provider = getattr(plugin_context, "get_using_stt_provider", None)
    provider = (
        get_provider(event.unified_msg_origin) if callable(get_provider) else None
    )
    if provider is None:
        raise VoiceServiceError(
            "provider_unavailable",
            "Voice STT provider unavailable",
            stage=stage,
        )
    return provider


def resolve_tts_provider(
    plugin_context: Any,
    event: AstrMessageEvent,
    *,
    stage: str | None = None,
) -> Any:
    if plugin_context is None:
        raise VoiceServiceError(
            "plugin_context_unavailable",
            "Voice TTS plugin context unavailable",
            stage=stage,
        )
    get_provider = getattr(plugin_context, "get_using_tts_provider", None)
    provider = (
        get_provider(event.unified_msg_origin) if callable(get_provider) else None
    )
    if provider is None:
        raise VoiceServiceError(
            "provider_unavailable",
            "Voice TTS provider unavailable",
            stage=stage,
        )
    return provider


async def transcribe_record(
    plugin_context: Any,
    event: AstrMessageEvent,
    record: Record,
    *,
    provider: Any | None = None,
    stage: str,
) -> SpeechToTextResult:
    stt_provider = provider or resolve_stt_provider(
        plugin_context,
        event,
        stage=stage,
    )
    provider_id = _provider_id(stt_provider)
    try:
        audio_path = await record.convert_to_file_path()
    except Exception as exc:
        raise VoiceServiceError(
            "audio_path_resolution_failed",
            str(exc),
            stage=stage,
            provider_id=provider_id,
        ) from exc
    try:
        text = await stt_provider.get_text(audio_url=audio_path)
    except FileNotFoundError as exc:
        raise VoiceServiceError(
            "source_unavailable",
            str(exc),
            stage=stage,
            provider_id=provider_id,
            metadata={"audio_path": audio_path},
        ) from exc
    except Exception as exc:
        raise VoiceServiceError(
            "provider_error",
            str(exc),
            stage=stage,
            provider_id=provider_id,
            metadata={"audio_path": audio_path},
        ) from exc
    text = str(text or "").strip()
    if not text:
        raise VoiceServiceError(
            "empty_transcription",
            "Voice STT returned empty text",
            stage=stage,
            provider_id=provider_id,
            metadata={"audio_path": audio_path},
        )
    return SpeechToTextResult(
        text=text,
        audio_path=audio_path,
        provider_id=provider_id,
        metadata={
            "stage": stage,
            "provider_id": provider_id,
            "audio_path": audio_path,
        },
    )


async def register_tts_file_if_needed(
    audio_path: str,
    *,
    use_file_service: bool,
    callback_api_base: str | None,
    require_file_registration_config: bool = False,
    stage: str | None = None,
    provider_id: str | None = None,
) -> str | None:
    if not use_file_service:
        return None
    if not callback_api_base:
        if require_file_registration_config:
            raise VoiceServiceError(
                "file_registration_config_missing",
                "Voice TTS file service requested without callback_api_base",
                stage=stage,
                provider_id=provider_id,
                metadata={"audio_path": audio_path},
            )
        return None
    try:
        token = await file_token_service.register_file(audio_path)
    except Exception as exc:
        raise VoiceServiceError(
            "file_registration_failed",
            str(exc),
            stage=stage,
            provider_id=provider_id,
            metadata={"audio_path": audio_path},
        ) from exc
    return f"{callback_api_base}/api/file/{token}"


async def synthesize_text(
    plugin_context: Any,
    event: AstrMessageEvent,
    text: str,
    *,
    provider: Any | None = None,
    stage: str,
    use_file_service: bool = False,
    callback_api_base: str | None = None,
    require_file_registration_config: bool = False,
) -> TextToSpeechResult:
    source_text = str(text or "")
    if not source_text.strip():
        raise VoiceServiceError(
            "empty_text",
            "Voice TTS source text is empty",
            stage=stage,
        )
    tts_provider = provider or resolve_tts_provider(
        plugin_context,
        event,
        stage=stage,
    )
    provider_id = _provider_id(tts_provider)
    try:
        audio_path = await tts_provider.get_audio(source_text)
    except Exception as exc:
        raise VoiceServiceError(
            "provider_error",
            str(exc),
            stage=stage,
            provider_id=provider_id,
            metadata={"source_text": source_text},
        ) from exc
    audio_path = str(audio_path or "").strip()
    if not audio_path:
        raise VoiceServiceError(
            "empty_audio_path",
            "Voice TTS returned empty audio path",
            stage=stage,
            provider_id=provider_id,
            metadata={"source_text": source_text},
        )
    audio_url = await register_tts_file_if_needed(
        audio_path,
        use_file_service=use_file_service,
        callback_api_base=callback_api_base,
        require_file_registration_config=require_file_registration_config,
        stage=stage,
        provider_id=provider_id,
    )
    return TextToSpeechResult(
        text=source_text,
        audio_path=audio_path,
        audio_url=audio_url,
        provider_id=provider_id,
        metadata={
            "stage": stage,
            "provider_id": provider_id,
            "source_text": source_text,
            "audio_path": audio_path,
            "audio_url": audio_url,
        },
    )
