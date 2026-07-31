"""Task-local capture for tool output that must remain model-visible."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain


@dataclass(slots=True)
class ToolOutputCapture:
    """Collect legacy tool output without delivering it to the platform.

    Persona tool calls use this to turn old ``event.send()`` calls into tool
    material. The context variable keeps the behavior scoped to the executing
    tool task instead of changing the shared event for the entire turn.
    """

    messages: list[MessageChain] = field(default_factory=list)
    owner_task: asyncio.Task | None = None

    def capture(self, message: MessageChain | str | None) -> None:
        if isinstance(message, str):
            message = MessageChain(chain=[Plain(message)])
        if message is not None:
            self.messages.append(message.derive(list(message.chain)))

    async def capture_stream(
        self,
        generator: AsyncGenerator[MessageChain, None],
    ) -> None:
        async for message in generator:
            self.capture(message)

    def drain(self) -> list[MessageChain]:
        messages = self.messages
        self.messages = []
        return messages


_active_tool_output_capture: ContextVar[ToolOutputCapture | None] = ContextVar(
    "astrbot_active_tool_output_capture",
    default=None,
)


def get_active_tool_output_capture() -> ToolOutputCapture | None:
    """Return the capture active for the current tool task, if any."""

    capture = _active_tool_output_capture.get()
    if capture is None or capture.owner_task is not asyncio.current_task():
        return None
    return capture


@contextmanager
def activate_tool_output_capture(capture: ToolOutputCapture) -> Iterator[None]:
    """Make ``capture`` visible to output entry points for one tool step."""

    capture.owner_task = asyncio.current_task()
    token = _active_tool_output_capture.set(capture)
    try:
        yield
    finally:
        _active_tool_output_capture.reset(token)
        capture.owner_task = None
