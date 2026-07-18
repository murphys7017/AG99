from unittest.mock import MagicMock

import pytest

from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_chain_delivery import deliver_message_chain
from astrbot.core.message.message_event_result import MessageChain


@pytest.mark.asyncio
async def test_delivery_preserves_logical_tts_segments_across_physical_sends():
    delivered = []

    def metadata(message_id: str, attachment: str) -> dict:
        return {
            "output_segment": {
                "turn_id": "turn-1",
                "message_id": message_id,
                "tts": {
                    "tts_request_id": f"tts-{message_id}",
                    "status": "succeeded",
                },
            },
            "audio_attachment": attachment,
        }

    async def send_with_extras(chain, extras):
        delivered.append((chain, extras))

    await deliver_message_chain(
        MagicMock(),
        MessageChain(
            [
                Record(
                    file="one.wav",
                    delivery_metadata=metadata("message-1", "present"),
                ),
                Plain(
                    "one",
                    delivery_metadata=metadata("message-1", "absent"),
                ),
                Record(
                    file="two.wav",
                    delivery_metadata=metadata("message-2", "present"),
                ),
                Plain(
                    "two",
                    delivery_metadata=metadata("message-2", "absent"),
                ),
            ]
        ),
        send_message=send_with_extras,
    )

    assert [item[1]["output_segment"]["message_id"] for item in delivered] == [
        "message-1",
        "message-2",
        "message-1",
        "message-2",
    ]
    assert [item[1]["audio_attachment"] for item in delivered] == [
        "present",
        "present",
        "absent",
        "absent",
    ]
