from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from astrbot.core import file_token_service, logger
from astrbot.core.message.components import Record
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.star_handler import EventType, star_handlers_registry


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
        self.state: TTSState | None = None


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
    state: TTSState

    @property
    def delivered_file(self) -> str:
        return self.audio_url or self.audio_path


TTSStatus = Literal["requested", "generating", "succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class TTSState:
    """Read-only state for one audio generation lifecycle.

    These states describe synthesis, not client-side audio playback.
    """

    turn_id: str
    message_id: str
    tts_request_id: str
    stage: str
    status: TTSStatus
    provider_id: str | None = None
    audio_path: str | None = None
    audio_url: str | None = None
    failure_code: str | None = None
    external_correlation_id: str | None = None

    def to_mapping(self) -> dict[str, str | None]:
        return {
            "turn_id": self.turn_id,
            "message_id": self.message_id,
            "tts_request_id": self.tts_request_id,
            "stage": self.stage,
            "status": self.status,
            "provider_id": self.provider_id,
            "audio_path": self.audio_path,
            "audio_url": self.audio_url,
            "failure_code": self.failure_code,
            "external_correlation_id": self.external_correlation_id,
        }


def build_tts_delivery_metadata(
    state: TTSState,
    *,
    audio_attachment: Literal["present", "absent"],
) -> dict[str, Any]:
    """Bind one physical send to its logical TTS output segment."""

    return {
        "output_segment": {
            "turn_id": state.turn_id,
            "message_id": state.message_id,
            "external_correlation_id": state.external_correlation_id,
            "tts": state.to_mapping(),
        },
        "audio_attachment": audio_attachment,
    }


async def _emit_tts_state(event: AstrMessageEvent, state: TTSState) -> None:
    handlers = star_handlers_registry.get_handlers_by_event_type(
        EventType.OnTTSStateChangedEvent,
        plugins_name=event.plugins_name,
    )
    for handler in handlers:
        try:
            await handler.handler(event, state)
        except Exception:  # noqa: BLE001
            logger.error(
                "TTS state listener failed: handler=%s request_id=%s status=%s",
                handler.handler_full_name,
                state.tts_request_id,
                state.status,
                exc_info=True,
            )


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
    turn_id: str | None = None,
    message_id: str | None = None,
    tts_request_id: str | None = None,
    external_correlation_id: str | None = None,
) -> TextToSpeechResult:
    source_text = str(text or "")
    if not source_text.strip():
        raise VoiceServiceError(
            "empty_text",
            "Voice TTS source text is empty",
            stage=stage,
        )
    resolved_turn_id = str(
        turn_id
        or event.get_extra("_turn_id")
        or getattr(event.message_obj, "message_id", "")
        or uuid.uuid4().hex
    )
    resolved_request_id = str(tts_request_id or uuid.uuid4().hex)
    resolved_external_correlation_id = str(
        external_correlation_id
        or event.get_extra("output_correlation_id")
        or ""
    ).strip() or None
    resolved_message_id = str(
        message_id or f"{resolved_turn_id}::tts::{resolved_request_id[:12]}"
    )
    await _emit_tts_state(
        event,
        TTSState(
            turn_id=resolved_turn_id,
            message_id=resolved_message_id,
            tts_request_id=resolved_request_id,
            stage=stage,
            status="requested",
            external_correlation_id=resolved_external_correlation_id,
        ),
    )

    provider_id: str | None = None
    try:
        tts_provider = provider or resolve_tts_provider(
            plugin_context,
            event,
            stage=stage,
        )
        provider_id = _provider_id(tts_provider)
        await _emit_tts_state(
            event,
            TTSState(
                turn_id=resolved_turn_id,
                message_id=resolved_message_id,
                tts_request_id=resolved_request_id,
                stage=stage,
                status="generating",
                provider_id=provider_id,
                external_correlation_id=resolved_external_correlation_id,
            ),
        )
        try:
            generated_path = await tts_provider.get_audio(source_text)
        except Exception as exc:
            raise VoiceServiceError(
                "provider_error",
                str(exc),
                stage=stage,
                provider_id=provider_id,
                metadata={"source_text": source_text},
            ) from exc
        audio_path = str(generated_path or "").strip()
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
        terminal_state = TTSState(
            turn_id=resolved_turn_id,
            message_id=resolved_message_id,
            tts_request_id=resolved_request_id,
            stage=stage,
            status="succeeded",
            provider_id=provider_id,
            audio_path=audio_path,
            audio_url=audio_url,
            external_correlation_id=resolved_external_correlation_id,
        )
        await _emit_tts_state(event, terminal_state)
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
                "turn_id": resolved_turn_id,
                "message_id": resolved_message_id,
                "tts_request_id": resolved_request_id,
            },
            state=terminal_state,
        )
    except VoiceServiceError as exc:
        failed_state = TTSState(
            turn_id=resolved_turn_id,
            message_id=resolved_message_id,
            tts_request_id=resolved_request_id,
            stage=stage,
            status="failed",
            provider_id=exc.provider_id or provider_id,
            failure_code=exc.reason,
            external_correlation_id=resolved_external_correlation_id,
        )
        exc.state = failed_state
        await _emit_tts_state(
            event,
            failed_state,
        )
        raise
    except Exception as exc:  # noqa: BLE001
        wrapped = VoiceServiceError(
            "internal_error",
            str(exc),
            stage=stage,
            provider_id=provider_id,
        )
        failed_state = TTSState(
            turn_id=resolved_turn_id,
            message_id=resolved_message_id,
            tts_request_id=resolved_request_id,
            stage=stage,
            status="failed",
            provider_id=provider_id,
            failure_code=wrapped.reason,
            external_correlation_id=resolved_external_correlation_id,
        )
        wrapped.state = failed_state
        await _emit_tts_state(
            event,
            failed_state,
        )
        raise wrapped from exc
