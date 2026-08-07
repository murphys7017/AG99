from __future__ import annotations

import asyncio
from collections.abc import Mapping

from astrbot import logger

from .turn_state import record_interaction_turn_failure

CONVERSATION_COMMITTED_TURN_ID_EXTRA = (
    "_interaction_conversation_committed_turn_id"
)


async def commit_interaction_conversation_turn(
    *,
    event,
    plugin_context,
    turn_id: str,
    turn_material: dict[str, object],
) -> bool:
    """Commit the canonical visible turn before the next routed turn starts."""
    resolved_turn_id = str(turn_id or "").strip()
    material_turn_id = str(turn_material.get("turn_id", "") or "").strip()
    if not resolved_turn_id or material_turn_id != resolved_turn_id:
        return False
    if event.get_extra(CONVERSATION_COMMITTED_TURN_ID_EXTRA) == resolved_turn_id:
        return True

    conversation_manager = getattr(plugin_context, "conversation_manager", None)
    if conversation_manager is None:
        return False

    user_message = turn_material.get("user_message")
    assistant_text = str(turn_material.get("assistant_text", "") or "").strip()
    assistant_artifacts = turn_material.get("assistant_artifacts", [])
    if not isinstance(assistant_artifacts, list):
        assistant_artifacts = []
    source = str(turn_material.get("source", "platform") or "platform")
    is_observation = source == "observation"
    if not assistant_text and not assistant_artifacts:
        return False
    if not is_observation and not isinstance(user_message, dict):
        return False

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            event_extras = event.get_extra(default={})
            fixed_conversation_required = (
                isinstance(event_extras, Mapping)
                and "_interaction_fixed_conversation_id" in event_extras
            )
            fixed_conversation_id = str(
                event.get_extra("_interaction_fixed_conversation_id", "") or ""
            ).strip()
            if fixed_conversation_required and not fixed_conversation_id:
                event.set_extra(
                    "_interaction_delayed_history_skipped_reason",
                    "parent_conversation_unavailable",
                )
                return True
            conversation_id = await conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
            if fixed_conversation_required:
                if conversation_id != fixed_conversation_id:
                    event.set_extra(
                        "_interaction_delayed_history_skipped_reason",
                        "parent_conversation_changed",
                    )
                    return True
                parent_conversation = await conversation_manager.get_conversation(
                    event.unified_msg_origin,
                    fixed_conversation_id,
                )
                if parent_conversation is None:
                    event.set_extra(
                        "_interaction_delayed_history_skipped_reason",
                        "parent_conversation_unavailable",
                    )
                    return True
                conversation_id = fixed_conversation_id
            if not conversation_id:
                conversation_id = await conversation_manager.new_conversation(
                    event.unified_msg_origin,
                    event.get_platform_id(),
                )
            assistant_message = {"role": "assistant", "content": assistant_text}
            if assistant_artifacts:
                assistant_message["_astrbot_artifacts"] = list(assistant_artifacts)
            assistant_metadata = turn_material.get("assistant_metadata")
            if isinstance(assistant_metadata, Mapping):
                assistant_message["_astrbot_metadata"] = dict(assistant_metadata)
            if is_observation:
                await conversation_manager.append_assistant_turn(
                    conversation_id,
                    turn_id=resolved_turn_id,
                    assistant_message=assistant_message,
                )
            else:
                await conversation_manager.append_dialogue_turn(
                    conversation_id,
                    turn_id=resolved_turn_id,
                    user_message=user_message,
                    assistant_message=assistant_message,
                )
            event.set_extra(CONVERSATION_COMMITTED_TURN_ID_EXTRA, resolved_turn_id)
            event.set_extra(
                "_interaction_committed_conversation_id",
                conversation_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.05 * (2**attempt))

    if last_error is not None:
        event.set_extra("_interaction_conversation_history_failed", True)
        event.set_extra(
            "_interaction_conversation_history_failure_reason",
            str(last_error),
        )
        record_interaction_turn_failure(
            event,
            stage="conversation_history",
            reason="persist_failed",
            exception=last_error,
            user_visible_action="turn_failed_after_visible_output",
        )
        logger.error(
            "Interaction conversation persistence failed: platform_id=%s session_id=%s turn_id=%s error=%s",
            event.get_platform_id(),
            event.session_id,
            resolved_turn_id,
            last_error,
        )
    return False
