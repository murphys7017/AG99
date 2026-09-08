"""Shared execution path for proactive Core turns without a platform Event."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from astrbot.core.agent.tool import ToolSet
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.tools.message_tools import SendMessageToUserTool

if TYPE_CHECKING:
    from astrbot.core.cron.events import CronMessageEvent


@dataclass(slots=True)
class ProactiveAgentTurnResult:
    """Completed synthetic-event Core turn and the request used to run it."""

    event: CronMessageEvent
    request: ProviderRequest
    response: Any | None


async def run_proactive_agent_turn(
    *,
    context: Any,
    session: MessageSession,
    message: str,
    extras: dict[str, Any],
    role: str | None,
    config: Any,
    system_prompt: str,
    prompt: str,
    require_delivery_tool: bool,
    include_history_fences: bool,
) -> ProactiveAgentTurnResult | None:
    """Run one proactive Core turn through the standard Main Agent builder.

    Cron and detached background tools have no ordinary platform Event after the
    originating turn. They still share the same synthetic event, history,
    optional delivery tool, Core build, and runner lifecycle.
    """
    # Kept local to avoid making the Core builder import this proactive helper.
    from astrbot.core.astr_main_agent import _get_session_conv, build_main_agent
    from astrbot.core.cron.events import CronMessageEvent

    event = CronMessageEvent(
        context=context,
        session=session,
        message=message,
        extras=extras,
        message_type=session.message_type,
    )
    if role is not None:
        event.role = role

    request = ProviderRequest()
    conversation = await _get_session_conv(event=event, plugin_context=context)
    request.conversation = conversation
    history = json.loads(conversation.history)
    if history:
        request.contexts = history
        history_dump = request._print_friendly_context()
        request.contexts = []
        if include_history_fences:
            request.system_prompt += (
                "\n\nBellow is you and user previous conversation history:\n"
                f"---\n{history_dump}\n---\n"
            )
        else:
            request.system_prompt += (
                "\n\nBellow is you and user previous conversation history:\n"
                f"{history_dump}"
            )

    request.system_prompt += system_prompt
    request.prompt = prompt
    if require_delivery_tool:
        request.func_tool = ToolSet()
        request.func_tool.add_tool(
            context.get_llm_tool_manager().get_builtin_tool(SendMessageToUserTool)
        )

    result = await build_main_agent(
        event=event,
        plugin_context=context,
        config=config,
        req=request,
    )
    if result is None:
        return None

    async for _ in result.agent_runner.step_until_done(30):
        pass
    return ProactiveAgentTurnResult(
        event=event,
        request=request,
        response=result.agent_runner.get_final_llm_resp(),
    )


__all__ = ["ProactiveAgentTurnResult", "run_proactive_agent_turn"]
