"""Shared tolerant parsing for model-produced JSON objects."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from json_repair import repair_json

from astrbot.core import logger


def extract_json_object(text: object) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    cleaned = _clean_json_candidate(text)
    payload = _parse_jsonish_dict(cleaned)
    if payload is not None:
        return payload

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        payload = _parse_jsonish_dict(cleaned[start : end + 1])
        if payload is not None:
            return payload
    if start >= 0:
        payload = _parse_jsonish_dict(_balance_json_delimiters(cleaned[start:]))
        if payload is not None:
            return payload

    try:
        repaired = repair_json(cleaned, return_objects=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("JSON repair failed: %s", exc)
        return None
    return repaired if isinstance(repaired, dict) else None


def _clean_json_candidate(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
    return fenced.group(1).strip() if fenced else cleaned


def _parse_jsonish_dict(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return payload if isinstance(payload, dict) else None
    try:
        payload = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _balance_json_delimiters(text: str) -> str:
    closers: list[str] = []
    in_string = False
    escape = False
    quote_char = '"'
    for char in text:
        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote_char:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote_char = char
        elif char == "{":
            closers.append("}")
        elif char == "[":
            closers.append("]")
        elif char in {"}", "]"} and closers and char == closers[-1]:
            closers.pop()
    if in_string:
        text += quote_char
    return text + "".join(reversed(closers))


__all__ = ["extract_json_object"]
