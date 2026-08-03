from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from astrbot.core.config import AstrBotConfig

from .context_utils import call_event_hook, call_handler

if TYPE_CHECKING:
    from astrbot.core.interaction.middleware import InteractionMiddleware
    from astrbot.core.interaction.personal_runtime import PersonalRuntimeManager
    from astrbot.core.output_lifecycle import (
        PreOutputProcessor,
        TurnDeliveryCoordinator,
    )
    from astrbot.core.star import PluginManager


@dataclass
class PipelineContext:
    """上下文对象，包含管道执行所需的上下文信息"""

    astrbot_config: AstrBotConfig  # AstrBot 配置对象
    plugin_manager: PluginManager  # 插件管理器对象
    astrbot_config_id: str
    interaction_middleware: InteractionMiddleware | None = None
    personal_runtime_manager: PersonalRuntimeManager | None = None
    pre_output_processor: PreOutputProcessor | None = None
    turn_delivery_coordinator: TurnDeliveryCoordinator | None = None
    call_handler = call_handler
    call_event_hook = call_event_hook
