from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from math import isfinite
from typing import TYPE_CHECKING, Any

from astrbot.core.platform.message_session import MessageSession

if TYPE_CHECKING:
    from .observation_inbox import ObservationAdmissionResult


_SensorSubmitter = Callable[..., Awaitable[Any]]


class RuntimeObservationSensorHandle:
    """Bound plugin handle for submitting structured Runtime Observations."""

    __slots__ = ("_registration_id", "_submitter")

    def __init__(
        self,
        *,
        registration_id: int,
        submitter: _SensorSubmitter,
    ) -> None:
        self._registration_id = registration_id
        self._submitter = submitter

    async def submit(
        self,
        *,
        kind: str,
        session: str | MessageSession | None = None,
        payload: Mapping[str, Any] | None = None,
        expires_in_seconds: float = 300.0,
        coalesce_key: str | None = None,
        correlation_id: str | None = None,
    ) -> ObservationAdmissionResult | None:
        """Submit one fact to the existing Personal Runtime Observation Inbox.

        ``session=None`` uses the configured proactive-message target. Payloads
        are facts rather than messages: do not include user text, prompts, or
        final reply material.
        """
        return await self._submitter(
            self._registration_id,
            kind,
            session,
            payload,
            _validate_expiry(expires_in_seconds),
            _clean_optional_identifier(coalesce_key, field_name="coalesce_key"),
            _clean_optional_identifier(correlation_id, field_name="correlation_id"),
        )


def normalize_runtime_sensor_identifier(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"Runtime Observation sensor {field_name} is required")
    if len(normalized) > 128:
        raise ValueError(
            f"Runtime Observation sensor {field_name} must be at most 128 characters"
        )
    if not all(char.isalnum() or char in {"-", "_", "."} for char in normalized):
        raise ValueError(
            f"Runtime Observation sensor {field_name} only allows letters, digits, "
            "hyphens, underscores, and dots"
        )
    return normalized


def validate_runtime_observation_kind(value: object) -> str:
    kind = normalize_runtime_sensor_identifier(value, field_name="kind")
    if kind in {"personal_action", "proactive_output"}:
        raise ValueError(f"Runtime Observation kind is reserved: {kind}")
    return kind


def validate_runtime_observation_payload(
    payload: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise TypeError("Runtime Observation sensor payload must be a mapping")
    _validate_payload_value(payload, path="")
    return payload


def _validate_expiry(value: object) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "Runtime Observation expires_in_seconds must be a finite number"
        ) from exc
    if not isfinite(seconds) or seconds <= 0:
        raise ValueError(
            "Runtime Observation expires_in_seconds must be a positive finite number"
        )
    return seconds


def _clean_optional_identifier(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return normalize_runtime_sensor_identifier(value, field_name=field_name)


def _validate_payload_value(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Runtime Observation payload keys must be non-empty strings")
            key_path = f"{path}.{key}" if path else key
            if key.casefold() in {
                "visible_reply_material",
                "reply",
                "text",
                "message",
                "content",
                "prompt",
                "raw_message",
                "raw_text",
                "user_message",
                "assistant_message",
            }:
                raise ValueError(
                    "Runtime Observation payload cannot contain message or reply "
                    f"material: {key_path}"
                )
            _validate_payload_value(item, path=key_path)
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _validate_payload_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if len(value) > 256 or "\n" in value or "\r" in value:
            raise ValueError(
                "Runtime Observation payload strings must be short scalar facts, "
                f"not free-form text: {path}"
            )
        return
    if isinstance(value, float) and not isfinite(value):
        raise ValueError(
            f"Runtime Observation payload numbers must be finite: {path}"
        )
    if value is None or isinstance(value, bool | int | float):
        return
    raise TypeError(
        f"Unsupported Runtime Observation payload value at {path}: {type(value)!r}"
    )


__all__ = [
    "RuntimeObservationSensorHandle",
    "normalize_runtime_sensor_identifier",
    "validate_runtime_observation_kind",
    "validate_runtime_observation_payload",
]
