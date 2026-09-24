from collections.abc import AsyncGenerator, Mapping

from astrbot.core import logger
from astrbot.core.interaction.turn_state import get_interaction_turn_runtime_config
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.session_llm_manager import SessionServiceManager

from ...context import PipelineContext
from ...stage import Stage
from .agent_sub_stages.internal import InternalAgentSubStage
from .agent_sub_stages.third_party import ThirdPartyAgentSubStage


class AgentRequestSubStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.internal_agent_sub_stage = InternalAgentSubStage()
        self.third_party_agent_sub_stage = ThirdPartyAgentSubStage()
        await self.internal_agent_sub_stage.initialize(ctx)
        await self.third_party_agent_sub_stage.initialize(ctx)

    @staticmethod
    def _resolve_provider_wake_prefix(runtime_config: Mapping[str, object]) -> str:
        provider_settings = runtime_config.get("provider_settings", {})
        raw_prefix = (
            provider_settings.get("wake_prefix", "")
            if isinstance(provider_settings, Mapping)
            else ""
        )
        provider_prefix = raw_prefix if isinstance(raw_prefix, str) else ""
        wake_prefixes = runtime_config.get("wake_prefix", [])
        if isinstance(wake_prefixes, str):
            wake_prefixes = [wake_prefixes]
        if not isinstance(wake_prefixes, list):
            wake_prefixes = []
        for bot_prefix in wake_prefixes:
            if isinstance(bot_prefix, str) and provider_prefix.startswith(bot_prefix):
                return provider_prefix[len(bot_prefix) :]
        return provider_prefix

    async def process(self, event: AstrMessageEvent) -> AsyncGenerator[None, None]:
        runtime_config = get_interaction_turn_runtime_config(event)
        if not isinstance(runtime_config, Mapping):
            runtime_config = self.ctx.astrbot_config
        provider_settings = runtime_config.get("provider_settings", {})
        if not isinstance(provider_settings, Mapping) or not provider_settings.get(
            "enable", False
        ):
            logger.debug(
                "This pipeline does not enable AI capability, skip processing."
            )
            return

        if not await SessionServiceManager.should_process_llm_request(event):
            logger.debug(
                f"The session {event.unified_msg_origin} has disabled AI capability, skipping processing."
            )
            return

        runner_type = str(provider_settings.get("agent_runner_type", "local")).lower()
        agent_sub_stage = (
            self.internal_agent_sub_stage
            if runner_type in {"local", "codex_cli"}
            else self.third_party_agent_sub_stage
        )
        provider_wake_prefix = self._resolve_provider_wake_prefix(runtime_config)
        async for resp in agent_sub_stage.process(event, provider_wake_prefix):
            yield resp
