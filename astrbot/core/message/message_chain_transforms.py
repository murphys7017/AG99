from __future__ import annotations

from astrbot.core.message.components import BaseMessageComponent, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.tts_expression_tags import strip_minimax_tts_expression_tags


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


def strip_minimax_tts_expression_tags_from_plain_components(
    message: MessageChain,
) -> MessageChain:
    """Remove MiniMax TTS control tags while preserving rich components."""
    transformed: list[BaseMessageComponent] = []
    changed = False
    for component in message.chain:
        if not isinstance(component, Plain):
            transformed.append(component)
            continue
        text = strip_minimax_tts_expression_tags(component.text)
        if text == component.text:
            transformed.append(component)
            continue
        transformed.append(
            Plain(
                text,
                delivery_metadata=dict(component.delivery_metadata),
            ),
        )
        changed = True
    return message.derive(transformed) if changed else message
