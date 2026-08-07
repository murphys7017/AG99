from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.path_util import file_uri_to_path


def serialize_assistant_message_chain(message: MessageChain) -> list[dict[str, Any]]:
    serialized = []
    for component in message.chain:
        try:
            payload = component.toDict()
        except Exception:
            component_type = getattr(component, "type", type(component).__name__)
            payload = {"type": str(component_type), "data": {}}
        serialized.append(_artifact_safe(payload))
    return serialized


def _artifact_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in {"file", "file_", "path", "url", "cover"}:
                summary = _media_reference_summary(item)
                if summary is not None:
                    result[key_text] = summary
                    continue
            result[key_text] = _artifact_safe(item)
        return result
    if isinstance(value, list | tuple):
        return [_artifact_safe(item) for item in value]
    return _json_safe(value)


def _media_reference_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith(("base64://", "data:")):
        return {
            "kind": "inline_media",
            "encoded_length": len(value),
        }
    if value.startswith(("http://", "https://")):
        return None
    path_value = file_uri_to_path(value) if value.startswith("file:") else value
    if not os.path.exists(path_value):
        return None
    path = Path(path_value)
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    return {
        "kind": "local_media",
        "name": path.name,
        "size_bytes": size,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
