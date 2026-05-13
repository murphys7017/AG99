from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from .manager import PostProcessManager
from .types import PostProcessContext, PostProcessor, PostProcessTrigger

if TYPE_CHECKING:
    from astrbot.core.db.po import Conversation

POSTPROCESS_MANAGER = PostProcessManager()


def get_postprocess_manager() -> PostProcessManager:
    return POSTPROCESS_MANAGER


def register_postprocessor(processor: PostProcessor) -> bool:
    return POSTPROCESS_MANAGER.register(processor)


def unregister_postprocessor(processor: PostProcessor) -> bool:
    return POSTPROCESS_MANAGER.unregister(processor)


def build_postprocess_context(
    *,
    event,
    trigger: PostProcessTrigger,
    llm_response: LLMResponse | None = None,
    provider_request: ProviderRequest | None = None,
    plugin_context=None,
    conversation: Conversation | None = None,
    turn_id: str | None = None,
    visible_outputs: list[dict] | None = None,
    turn_material: dict[str, object] | None = None,
) -> PostProcessContext:
    req = provider_request or event.get_extra("provider_request")
    resolved_conversation = conversation
    if resolved_conversation is None and isinstance(req, ProviderRequest):
        resolved_conversation = req.conversation
    resolved_turn_id = turn_id
    if resolved_turn_id is None:
        raw_turn_id = event.get_extra("_turn_id")
        resolved_turn_id = str(raw_turn_id).strip() if raw_turn_id else None
    resolved_visible_outputs = visible_outputs
    if resolved_visible_outputs is None:
        raw_visible_outputs = event.get_extra("_visible_turn_outputs", None)
        if raw_visible_outputs is None:
            raw_visible_outputs = event.get_extra("_postprocess_visible_outputs", [])
        resolved_visible_outputs = (
            list(raw_visible_outputs) if isinstance(raw_visible_outputs, list) else []
        )
    return PostProcessContext(
        event=event,
        trigger=trigger,
        provider_request=req if isinstance(req, ProviderRequest) else None,
        llm_response=llm_response,
        conversation=resolved_conversation,
        turn_id=resolved_turn_id,
        visible_outputs=[
            dict(item) for item in resolved_visible_outputs if isinstance(item, dict)
        ],
        turn_material=dict(turn_material) if isinstance(turn_material, dict) else None,
        timestamp=datetime.now(timezone.utc),
        debug_meta={"plugin_context": plugin_context} if plugin_context is not None else {},
    )


async def resolve_postprocess_conversation(
    *,
    event,
    provider_request: ProviderRequest | None = None,
    plugin_context=None,
) -> Conversation | None:
    req = provider_request or event.get_extra("provider_request")
    if isinstance(req, ProviderRequest) and req.conversation is not None:
        return req.conversation

    conversation = event.get_extra("conversation")
    if conversation is not None:
        return conversation

    conversation_manager = getattr(plugin_context, "conversation_manager", None)
    if conversation_manager is None:
        return None

    conversation_id = await conversation_manager.get_curr_conversation_id(
        event.unified_msg_origin
    )
    if not conversation_id:
        return None
    return await conversation_manager.get_conversation(
        event.unified_msg_origin,
        conversation_id,
    )


async def dispatch_postprocess(
    *,
    event,
    trigger: PostProcessTrigger,
    llm_response: LLMResponse | None = None,
    provider_request: ProviderRequest | None = None,
    plugin_context=None,
    conversation: Conversation | None = None,
    turn_id: str | None = None,
    visible_outputs: list[dict] | None = None,
    turn_material: dict[str, object] | None = None,
) -> None:
    if not POSTPROCESS_MANAGER.has_processors(trigger):
        logger.debug("postprocess(%s): skipped before context build", trigger.value)
        return

    resolved_conversation = conversation or await resolve_postprocess_conversation(
        event=event,
        provider_request=provider_request,
        plugin_context=plugin_context,
    )
    ctx = build_postprocess_context(
        event=event,
        trigger=trigger,
        llm_response=llm_response,
        provider_request=provider_request,
        conversation=resolved_conversation,
        turn_id=turn_id,
        visible_outputs=visible_outputs,
        turn_material=turn_material,
        plugin_context=plugin_context,
    )
    await POSTPROCESS_MANAGER.dispatch(trigger, ctx)
