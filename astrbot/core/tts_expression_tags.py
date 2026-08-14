"""MiniMax TTS expression-tag guidance and display cleanup."""

from __future__ import annotations

import re
from collections.abc import Mapping

MINIMAX_TTS_PROVIDER_TYPE = "minimax_tts_api"
MINIMAX_TTS_EXPRESSION_MODELS = frozenset(
    {"speech-2.8-hd", "speech-2.8-turbo"},
)
MINIMAX_TTS_EXPRESSION_TAGS: tuple[tuple[str, str], ...] = (
    ("laughs", "笑声"),
    ("chuckle", "轻笑"),
    ("coughs", "咳嗽"),
    ("clear-throat", "清嗓子"),
    ("groans", "呻吟"),
    ("breath", "正常换气"),
    ("pant", "喘气"),
    ("inhale", "吸气"),
    ("exhale", "呼气"),
    ("gasps", "倒吸气"),
    ("sniffs", "吸鼻子"),
    ("sighs", "叹气"),
    ("snorts", "喷鼻息"),
    ("burps", "打嗝"),
    ("lip-smacking", "咂嘴"),
    ("humming", "哼唱"),
    ("hissing", "嘶嘶声"),
    ("emm", "嗯"),
    ("sneezes", "喷嚏"),
)
_MINIMAX_TTS_EXPRESSION_TAG_PATTERN = re.compile(
    r"\((?:"
    + "|".join(re.escape(tag) for tag, _ in MINIMAX_TTS_EXPRESSION_TAGS)
    + r")\)",
    re.IGNORECASE,
)


def normalize_minimax_tts_model(model: object) -> str:
    return str(model or "").strip().lower()


def supports_minimax_tts_expression_tags(
    provider_type: object,
    model: object,
) -> bool:
    return (
        str(provider_type or "").strip().lower() == MINIMAX_TTS_PROVIDER_TYPE
        and normalize_minimax_tts_model(model) in MINIMAX_TTS_EXPRESSION_MODELS
    )


def get_minimax_tts_expression_model(provider: object) -> str | None:
    provider_config = getattr(provider, "provider_config", {})
    if not isinstance(provider_config, Mapping):
        provider_config = {}
    provider_type = provider_config.get("type", "")
    get_model = getattr(provider, "get_model", None)
    model = get_model() if callable(get_model) else None
    model = model or provider_config.get("model", "")
    if not supports_minimax_tts_expression_tags(provider_type, model):
        return None
    return str(model).strip()


def is_minimax_tts_expression_enabled(
    plugin_context: object,
    event: object,
    tts_settings: Mapping[str, object],
) -> bool:
    if not bool(tts_settings.get("enable")):
        return False
    get_provider = getattr(plugin_context, "get_using_tts_provider", None)
    umo = getattr(event, "unified_msg_origin", None)
    if not callable(get_provider):
        return False
    try:
        provider = get_provider(umo)
    except Exception:
        return False
    return get_minimax_tts_expression_model(provider) is not None


def build_minimax_tts_expression_guidance(model: object) -> str:
    model_name = str(model or "").strip()
    tags = "、".join(
        f"({tag})（{description}）"
        for tag, description in MINIMAX_TTS_EXPRESSION_TAGS
    )
    return (
        f"当前回复将使用 MiniMax TTS 模型 {model_name} 合成语音。"
        "你可以根据语境在 spoken_reply 文本中自然、适度地插入语气词标签；"
        "不需要每次都使用，也不要为了使用标签而破坏句子。"
        f"支持的标签只有：{tags}。"
        "标签必须使用英文小括号格式，不要单独解释标签，也不要把标签放进 effect_calls。"
    )


def strip_minimax_tts_expression_tags(text: str) -> str:
    return _MINIMAX_TTS_EXPRESSION_TAG_PATTERN.sub("", str(text or ""))


__all__ = [
    "MINIMAX_TTS_EXPRESSION_MODELS",
    "MINIMAX_TTS_EXPRESSION_TAGS",
    "MINIMAX_TTS_PROVIDER_TYPE",
    "build_minimax_tts_expression_guidance",
    "get_minimax_tts_expression_model",
    "is_minimax_tts_expression_enabled",
    "normalize_minimax_tts_model",
    "strip_minimax_tts_expression_tags",
    "supports_minimax_tts_expression_tags",
]
