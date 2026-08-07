from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from enum import Enum
from typing import Any

from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.path_util import file_uri_to_path

_MEDIA_REFERENCE_KEYS = frozenset({"file", "file_", "path", "url", "cover"})


def is_plain_text_message(message: MessageChain) -> bool:
    return bool(message.chain) and all(
        isinstance(component, Plain) for component in message.chain
    )


def fingerprint_visible_message(message: MessageChain) -> str | None:
    """Fingerprint one visible component chain without retaining media payloads."""
    if not message.chain:
        return None
    payload = {
        "chain": [_component_payload(component) for component in message.chain],
        "type": message.type,
        "use_markdown": message.use_markdown_,
        "use_t2i": message.use_t2i_,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _component_payload(component: Any) -> Any:
    try:
        payload = component.toDict()
    except Exception:
        payload = {
            "type": f"{type(component).__module__}.{type(component).__qualname__}",
            "value": str(component),
        }
    return _normalize(payload)


def _normalize(value: Any, *, field_name: str = "") -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, bytes | bytearray | memoryview):
        content = bytes(value)
        return {
            "kind": "inline_bytes",
            "length": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    if isinstance(value, Enum):
        return _normalize(value.value, field_name=field_name)
    if isinstance(value, str) and field_name in _MEDIA_REFERENCE_KEYS:
        return _normalize_media_reference(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "value": str(value),
    }


def _normalize_media_reference(value: str) -> Any:
    if not value:
        return ""
    if value.startswith(("base64://", "data:")):
        encoded = value.encode("utf-8")
        return {
            "kind": "inline_media",
            "length": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    if value.startswith(("http://", "https://")):
        return {"kind": "remote_media", "value": value}

    path_value = file_uri_to_path(value) if value.startswith("file:") else value
    canonical = os.path.normcase(os.path.abspath(os.path.expanduser(path_value)))
    try:
        stat = os.stat(canonical)
    except OSError:
        return {"kind": "local_media", "path": canonical, "available": False}
    return {
        "kind": "local_media",
        "path": canonical,
        "available": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
