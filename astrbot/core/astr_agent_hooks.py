from typing import Any

from mcp.types import CallToolResult

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.interaction.plugin_runtime import PLUGIN_RUNTIME_TARGET_CORE
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.postprocess import dispatch_postprocess
from astrbot.core.postprocess.types import PostProcessTrigger
from astrbot.core.star.star_handler import EventType


class MainAgentHooks(BaseAgentRunHooks[AstrAgentContext]):
    async def on_agent_begin(
        self, run_context: ContextWrapper[AstrAgentContext]
    ) -> None:
        await call_event_hook(
            run_context.context.event,
            EventType.OnAgentBeginEvent,
            run_context,
            execution_surface=PLUGIN_RUNTIME_TARGET_CORE,
        )

    async def on_agent_done(self, run_context, llm_response) -> None:
        # 执行事件钩子
        if llm_response and llm_response.reasoning_content:
            # we will use this in result_decorate stage to inject reasoning content to chain
            run_context.context.event.set_extra(
                "_llm_reasoning_content", llm_response.reasoning_content
            )

        await call_event_hook(
            run_context.context.event,
            EventType.OnLLMResponseEvent,
            llm_response,
            execution_surface=PLUGIN_RUNTIME_TARGET_CORE,
        )
        await call_event_hook(
            run_context.context.event,
            EventType.OnAgentDoneEvent,
            run_context,
            llm_response,
            execution_surface=PLUGIN_RUNTIME_TARGET_CORE,
        )
        if run_context.context.event.is_stopped():
            return

        await dispatch_postprocess(
            event=run_context.context.event,
            trigger=PostProcessTrigger.ON_LLM_RESPONSE,
            llm_response=llm_response,
            plugin_context=run_context.context.context,
        )

    async def on_tool_start(
        self,
        run_context: ContextWrapper[AstrAgentContext],
        tool: FunctionTool[Any],
        tool_args: dict | None,
    ) -> None:
        await call_event_hook(
            run_context.context.event,
            EventType.OnUsingLLMToolEvent,
            tool,
            tool_args,
            execution_surface=PLUGIN_RUNTIME_TARGET_CORE,
        )

    async def on_tool_end(
        self,
        run_context: ContextWrapper[AstrAgentContext],
        tool: FunctionTool[Any],
        tool_args: dict | None,
        tool_result: CallToolResult | None,
    ) -> None:
        run_context.context.event.clear_result()
        await call_event_hook(
            run_context.context.event,
            EventType.OnLLMToolRespondEvent,
            tool,
            tool_args,
            tool_result,
            execution_surface=PLUGIN_RUNTIME_TARGET_CORE,
        )


class EmptyAgentHooks(BaseAgentRunHooks[AstrAgentContext]):
    pass


MAIN_AGENT_HOOKS = MainAgentHooks()
