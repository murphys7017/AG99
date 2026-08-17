from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

SpeechCueKind = Literal[
    "breath",
    "sigh",
    "laugh",
    "chuckle",
    "hesitate",
    "emphasis",
]
SpeechCuePosition = Literal["before", "after"]

SPEECH_CUE_KINDS: tuple[SpeechCueKind, ...] = (
    "breath",
    "sigh",
    "laugh",
    "chuckle",
    "hesitate",
    "emphasis",
)
SPEECH_CUE_POSITIONS: tuple[SpeechCuePosition, ...] = ("before", "after")
MAX_SPEECH_CUES = 8


@dataclass(frozen=True, slots=True)
class SpeechCue:
    kind: SpeechCueKind
    phrase_index: int
    position: SpeechCuePosition

    def to_dict(self) -> dict[str, str | int]:
        return {
            "kind": self.kind,
            "phrase_index": self.phrase_index,
            "position": self.position,
        }


def build_speech_cue_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": MAX_SPEECH_CUES,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": list(SPEECH_CUE_KINDS)},
                "phrase_index": {"type": "integer", "minimum": 0},
                "position": {
                    "type": "string",
                    "enum": list(SPEECH_CUE_POSITIONS),
                },
            },
            "required": ["kind", "phrase_index", "position"],
        },
    }


def build_speech_cue_guidance() -> str:
    return (
        "speech_cues 是独立于 spoken_reply 的可选语音表演提示。"
        "只在确实有助于表达时少量使用；没有需要时返回空数组。"
        "kind 只能是 breath、sigh、laugh、chuckle、hesitate、emphasis。"
        "phrase_index 使用从 0 开始的短语序号，position 只能是 before 或 after。"
        "不要把标签、控制语法或括号指令写进 spoken_reply。"
        "speech_cues 不表达 Live2D 参数、动作方向或精确时间。"
    )


def normalize_speech_cues(
    value: object,
) -> tuple[list[SpeechCue], list[dict[str, Any]]]:
    if value is None:
        return [], []
    if not isinstance(value, list | tuple):
        return [], [{"reason": "speech_cues_not_array"}]

    cues: list[SpeechCue] = []
    issues: list[dict[str, Any]] = []
    for index, item in enumerate(value[:MAX_SPEECH_CUES]):
        if isinstance(item, SpeechCue):
            item = item.to_dict()
        if not isinstance(item, dict):
            issues.append({"index": index, "reason": "speech_cue_not_object"})
            continue

        kind = str(item.get("kind", "") or "").strip().lower()
        if kind not in SPEECH_CUE_KINDS:
            issues.append({"index": index, "reason": "unsupported_speech_cue_kind"})
            continue

        phrase_index = item.get("phrase_index")
        if (
            isinstance(phrase_index, bool)
            or not isinstance(phrase_index, int)
            or phrase_index < 0
        ):
            issues.append({"index": index, "reason": "invalid_phrase_index"})
            continue

        position = str(item.get("position", "") or "").strip().lower()
        if position not in SPEECH_CUE_POSITIONS:
            issues.append({"index": index, "reason": "unsupported_cue_position"})
            continue

        cues.append(
            SpeechCue(
                kind=cast(SpeechCueKind, kind),
                phrase_index=phrase_index,
                position=cast(SpeechCuePosition, position),
            )
        )

    if len(value) > MAX_SPEECH_CUES:
        issues.append(
            {
                "index": MAX_SPEECH_CUES,
                "reason": "speech_cue_limit_exceeded",
                "limit": MAX_SPEECH_CUES,
            }
        )
    return cues, issues


__all__ = [
    "MAX_SPEECH_CUES",
    "SPEECH_CUE_KINDS",
    "SPEECH_CUE_POSITIONS",
    "SpeechCue",
    "SpeechCueKind",
    "SpeechCuePosition",
    "build_speech_cue_guidance",
    "build_speech_cue_schema",
    "normalize_speech_cues",
]
