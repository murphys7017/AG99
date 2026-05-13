from __future__ import annotations

from astrbot import logger
from astrbot.core.postprocess import register_postprocessor, unregister_postprocessor
from astrbot.core.postprocess.types import PostProcessContext, PostProcessTrigger

from .turn_state import record_interaction_turn_failure


class InteractionConversationPostProcessor:
    name = "interaction_conversation_postprocessor"
    triggers = (PostProcessTrigger.AFTER_TURN_COMPLETED,)

    async def run(self, ctx: PostProcessContext) -> None:
        turn_id = str(ctx.turn_id or "").strip()
        if not turn_id:
            return
        if not isinstance(ctx.turn_material, dict):
            return
        material_turn_id = str(ctx.turn_material.get("turn_id", "") or "").strip()
        if material_turn_id != turn_id:
            return

        plugin_context = ctx.debug_meta.get("plugin_context")
        if plugin_context is None:
            return
        conversation_manager = getattr(plugin_context, "conversation_manager", None)
        if conversation_manager is None:
            return

        user_text = str(ctx.turn_material.get("user_text", "") or "").strip()
        assistant_text = str(ctx.turn_material.get("assistant_text", "") or "").strip()
        if not user_text or not assistant_text:
            return

        event = ctx.event
        try:
            conversation_id = await conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
            if not conversation_id:
                conversation_id = await conversation_manager.new_conversation(
                    event.unified_msg_origin,
                    event.get_platform_id(),
                )
            await conversation_manager.add_message_pair(
                conversation_id,
                user_message={"role": "user", "content": user_text},
                assistant_message={"role": "assistant", "content": assistant_text},
            )
        except Exception as exc:  # noqa: BLE001
            event.set_extra("_interaction_conversation_history_failed", True)
            event.set_extra(
                "_interaction_conversation_history_failure_reason",
                str(exc),
            )
            record_interaction_turn_failure(
                event,
                stage="conversation_history",
                reason="persist_failed",
                exception=exc,
                user_visible_action="continue_turn_completion",
            )
            logger.error(
                "Interaction conversation persistence failed: platform_id=%s session_id=%s turn_id=%s error=%s",
                event.get_platform_id(),
                event.session_id,
                event.get_extra("_turn_id"),
                exc,
                exc_info=True,
            )


_INTERACTION_CONVERSATION_POSTPROCESSOR: (
    InteractionConversationPostProcessor | None
) = None


def register_interaction_conversation_postprocessor() -> (
    InteractionConversationPostProcessor
):
    global _INTERACTION_CONVERSATION_POSTPROCESSOR

    processor = _INTERACTION_CONVERSATION_POSTPROCESSOR
    if processor is None:
        processor = InteractionConversationPostProcessor()
        _INTERACTION_CONVERSATION_POSTPROCESSOR = processor

    register_postprocessor(processor)
    return processor


def unregister_interaction_conversation_postprocessor() -> bool:
    if _INTERACTION_CONVERSATION_POSTPROCESSOR is None:
        return False
    return unregister_postprocessor(_INTERACTION_CONVERSATION_POSTPROCESSOR)


def reset_interaction_conversation_postprocessor() -> bool:
    global _INTERACTION_CONVERSATION_POSTPROCESSOR

    removed = unregister_interaction_conversation_postprocessor()
    _INTERACTION_CONVERSATION_POSTPROCESSOR = None
    return removed
