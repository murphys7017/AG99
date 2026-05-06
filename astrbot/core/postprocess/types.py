from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from astrbot.core.provider.entities import LLMResponse, ProviderRequest

if TYPE_CHECKING:
    from astrbot.core.db.po import Conversation
    from astrbot.core.platform.astr_message_event import AstrMessageEvent


class PostProcessTrigger(str, Enum):
    ON_LLM_RESPONSE = "on_llm_response"
    AFTER_MESSAGE_SENT = "after_message_sent"
    AFTER_TURN_COMPLETED = "after_turn_completed"


@dataclass(slots=True)
class PostProcessContext:
    event: AstrMessageEvent
    trigger: PostProcessTrigger
    provider_request: ProviderRequest | None = None
    llm_response: LLMResponse | None = None
    conversation: Conversation | None = None
    turn_id: str | None = None
    visible_outputs: list[dict[str, Any]] = field(default_factory=list)
    turn_material: dict[str, Any] | None = None
    timestamp: datetime | None = None
    debug_meta: dict[str, Any] = field(default_factory=dict)


class PostProcessor(Protocol):
    name: str
    triggers: tuple[PostProcessTrigger, ...]

    async def run(self, ctx: PostProcessContext) -> None: ...
