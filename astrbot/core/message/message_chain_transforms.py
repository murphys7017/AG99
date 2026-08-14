from __future__ import annotations

from astrbot.core.message.components import BaseMessageComponent, Plain
from astrbot.core.message.message_event_result import MessageChain


def replace_plain_text_preserving_components(
    message: MessageChain,
    text: str,
) -> MessageChain:
    """Replace all text with one segment while preserving rich component order."""
    replacement_added = False
    transformed: list[BaseMessageComponent] = []
    for component in message.chain:
        if not isinstance(component, Plain):
            transformed.append(component)
            continue
        if not replacement_added:
            if text:
                transformed.append(Plain(text))
            replacement_added = True
    if not replacement_added and text:
        transformed.insert(0, Plain(text))
    return message.derive(transformed)


def replace_leading_plain_components(
    message: MessageChain,
    replacement: BaseMessageComponent,
) -> MessageChain:
    """Replace the leading text run without dropping later rich components."""
    leading_plain_count = 0
    for component in message.chain:
        if not isinstance(component, Plain):
            break
        leading_plain_count += 1
    if leading_plain_count == 0:
        return message
    return message.derive([replacement, *message.chain[leading_plain_count:]])
