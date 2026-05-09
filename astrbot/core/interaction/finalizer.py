from __future__ import annotations

import asyncio
import re
from typing import Any

from astrbot import logger
from astrbot.core.provider import Provider
from astrbot.core.star.context import Context

from .core_bridge import get_interaction_decision
from .turn_state import record_interaction_turn_failure
from .types import FinalizerMode, InteractionAgentConfig

_STRUCTURED_MARKERS = (
    "```",
    "{",
    "}",
    "[",
    "]",
    "tool",
    "function",
    "traceback",
    "error:",
    "result:",
)


class InteractionFinalizerError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def should_finalize_response(
    event,
    core_result_text: str,
    config: InteractionAgentConfig,
) -> bool:
    text = (core_result_text or "").strip()
    if not text:
        logger.debug(
            "Interaction finalizer skipped: platform_id=%s session_id=%s reason=empty_core_result",
            event.get_platform_id(),
            event.session_id,
        )
        return False
    if config.finalizer_mode == FinalizerMode.OFF:
        logger.debug(
            "Interaction finalizer skipped: platform_id=%s session_id=%s reason=mode_off",
            event.get_platform_id(),
            event.session_id,
        )
        return False
    if config.finalizer_mode == FinalizerMode.FORCE:
        logger.debug(
            "Interaction finalizer selected: platform_id=%s session_id=%s reason=mode_force",
            event.get_platform_id(),
            event.session_id,
        )
        return True
    if len(text) > 500:
        logger.debug(
            "Interaction finalizer selected: platform_id=%s session_id=%s reason=long_text length=%s",
            event.get_platform_id(),
            event.session_id,
            len(text),
        )
        return True
    lower_text = text.lower()
    if any(marker in lower_text for marker in _STRUCTURED_MARKERS):
        logger.debug(
            "Interaction finalizer selected: platform_id=%s session_id=%s reason=structured_marker",
            event.get_platform_id(),
            event.session_id,
        )
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) >= 4:
        bullet_count = sum(
            1 for line in lines if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line)
        )
        if bullet_count >= 3:
            logger.debug(
                "Interaction finalizer selected: platform_id=%s session_id=%s reason=bullet_list bullet_count=%s",
                event.get_platform_id(),
                event.session_id,
                bullet_count,
            )
            return True
    conversational_marks = ("嗯", "好呀", "好的", "我来", "你", "啦", "呢", "吧")
    selected = (
        not any(mark in text for mark in conversational_marks) and len(text) > 120
    )
    logger.debug(
        "Interaction finalizer %s: platform_id=%s session_id=%s reason=auto_conversational_check length=%s",
        "selected" if selected else "skipped",
        event.get_platform_id(),
        event.session_id,
        len(text),
    )
    return selected


def build_finalizer_prompt(
    *,
    user_input: str,
    immediate_reply: str | None,
    core_result_text: str,
    decision_payload: dict[str, Any] | None,
) -> str:
    return (
        "你是 AstrBot interaction middleware 的最终表达层。\n"
        "请把核心执行层的结果改写成自然、口语化、有人格感的中文回复。\n"
        "要求：\n"
        "- 保留核心结果里的事实、数字、结论，不要编造。\n"
        "- 不要提到 middleware、core、工具调用、JSON、系统提示。\n"
        "- 不要过度扩写，保持简洁。\n"
        "- 如果核心结果本身已经自然，就只做轻微润色。\n\n"
        f"用户输入：{user_input or ''}\n"
        f"本轮先前的短回复：{immediate_reply or ''}\n"
        f"决策信息：{decision_payload or {}}\n"
        f"核心结果：\n{core_result_text}\n\n"
        "请只输出最终要发给用户的文本。"
    )


async def finalize_response(
    *,
    event,
    plugin_context: Context | None,
    config: InteractionAgentConfig,
    core_result_text: str,
    immediate_reply: str | None = None,
) -> str | None:
    if not should_finalize_response(event, core_result_text, config):
        return None
    if plugin_context is None:
        event.set_extra("_interaction_finalizer_failed", True)
        event.set_extra(
            "_interaction_finalizer_failure_reason",
            "plugin_context_unavailable",
        )
        record_interaction_turn_failure(
            event,
            stage="finalizer",
            reason="plugin_context_unavailable",
            user_visible_action="none",
        )
        raise InteractionFinalizerError("plugin_context_unavailable")

    provider = plugin_context.get_provider_by_id(config.finalizer_provider_id)
    if not isinstance(provider, Provider):
        log_method = (
            logger.error
            if config.finalizer_mode == FinalizerMode.FORCE
            else logger.info
        )
        log_method(
            "Interaction finalizer skipped: provider unavailable provider_id=%s",
            config.finalizer_provider_id,
        )
        event.set_extra("_interaction_finalizer_failed", True)
        event.set_extra(
            "_interaction_finalizer_failure_reason",
            "provider_unavailable",
        )
        record_interaction_turn_failure(
            event,
            stage="finalizer",
            reason="provider_unavailable",
            user_visible_action="none",
        )
        raise InteractionFinalizerError("provider_unavailable")

    decision = get_interaction_decision(event)
    decision_payload = decision.to_dict() if decision is not None else None
    prompt = build_finalizer_prompt(
        user_input=event.message_str,
        immediate_reply=immediate_reply,
        core_result_text=core_result_text,
        decision_payload=decision_payload,
    )
    try:
        logger.debug(
            "Interaction finalizer model request: provider_id=%s model=%s",
            config.finalizer_provider_id,
            config.finalizer_model or provider.get_model(),
        )
        response = await asyncio.wait_for(
            provider.text_chat(
                prompt=prompt,
                system_prompt="",
                model=config.finalizer_model or None,
                temperature=config.finalizer_temperature,
                max_tokens=config.finalizer_max_tokens,
            ),
            timeout=config.decision_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("Interaction finalizer timed out")
        event.set_extra("_interaction_finalizer_failed", True)
        event.set_extra("_interaction_finalizer_failure_reason", "timeout")
        record_interaction_turn_failure(
            event,
            stage="finalizer",
            reason="timeout",
            user_visible_action="none",
        )
        raise InteractionFinalizerError("timeout") from None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Interaction finalizer failed: %s", exc, exc_info=True)
        event.set_extra("_interaction_finalizer_failed", True)
        event.set_extra("_interaction_finalizer_failure_reason", "model_error")
        record_interaction_turn_failure(
            event,
            stage="finalizer",
            reason="model_error",
            exception=exc,
            user_visible_action="none",
        )
        raise InteractionFinalizerError("model_error", str(exc)) from exc

    text = (response.completion_text or "").strip()
    if not text:
        logger.warning("Interaction finalizer returned empty text")
        event.set_extra("_interaction_finalizer_failed", True)
        event.set_extra("_interaction_finalizer_failure_reason", "empty_output")
        record_interaction_turn_failure(
            event,
            stage="finalizer",
            reason="empty_output",
            user_visible_action="none",
        )
        raise InteractionFinalizerError("empty_output")
    logger.debug(
        "Interaction finalizer completed: platform_id=%s session_id=%s output_length=%s",
        event.get_platform_id(),
        event.session_id,
        len(text),
    )
    return text
