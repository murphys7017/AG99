"""Small JSON-RPC 2.0 codec used by the Codex app-server session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class CodexProtocolError(RuntimeError):
    """Raised when the app-server emits an invalid or failed RPC message."""


@dataclass(frozen=True, slots=True)
class JsonRpcError:
    code: int
    message: str
    data: Any = None


def encode_request(request_id: int, method: str, params: Any = None) -> bytes:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def encode_notification(method: str, params: Any = None) -> bytes:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def decode_message(raw: bytes | str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise CodexProtocolError("Codex app-server emitted invalid JSON") from exc
    if not isinstance(value, dict):
        raise CodexProtocolError("Codex app-server emitted an invalid JSON-RPC object")
    if value.get("jsonrpc", "2.0") != "2.0":
        raise CodexProtocolError("Codex app-server emitted an unsupported JSON-RPC version")
    if "method" not in value and "id" not in value:
        raise CodexProtocolError("Codex JSON-RPC object has neither method nor id")
    return value


def parse_error(message: dict[str, Any]) -> JsonRpcError | None:
    error = message.get("error")
    if error is None:
        return None
    if not isinstance(error, dict):
        return JsonRpcError(-32000, str(error))
    try:
        code = int(error.get("code", -32000))
    except (TypeError, ValueError):
        code = -32000
    return JsonRpcError(code, str(error.get("message", "JSON-RPC request failed")), error.get("data"))


__all__ = [
    "CodexProtocolError",
    "JsonRpcError",
    "decode_message",
    "encode_notification",
    "encode_request",
    "parse_error",
]
