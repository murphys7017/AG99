import base64
import json
import os
import shutil
import uuid

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import File, Image, Json, Plain, Record
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .webchat_queue_mgr import webchat_queue_mgr

attachments_dir = os.path.join(get_astrbot_data_path(), "attachments")


def _extract_conversation_id(session_id: str) -> str:
    """Extract raw webchat conversation id from event/session id."""
    if session_id.startswith("webchat!"):
        parts = session_id.split("!", 2)
        if len(parts) == 3:
            return parts[2]
    return session_id


class WebChatMessageEvent(AstrMessageEvent):
    def __init__(self, message_str, message_obj, platform_meta, session_id) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        os.makedirs(attachments_dir, exist_ok=True)

    @staticmethod
    async def _send(
        message_id: str,
        message: MessageChain | None,
        session_id: str,
        streaming: bool = False,
        emit_complete: bool = False,
        platform_extras: dict | None = None,
    ) -> str | None:
        request_id = str(message_id)
        conversation_id = _extract_conversation_id(session_id)
        web_chat_back_queue = webchat_queue_mgr.get_or_create_back_queue(
            request_id,
            conversation_id,
        )
        if not message:
            await web_chat_back_queue.put(
                {
                    "type": "end",
                    "data": "",
                    "streaming": False,
                    "message_id": message_id,
                    "platform_extras": platform_extras or {},
                },  # end means this request is finished
            )
            return

        data = ""
        for comp in message.chain:
            if isinstance(comp, Plain):
                data = comp.text
                await web_chat_back_queue.put(
                    {
                        "type": "plain",
                        "data": data,
                        "streaming": streaming,
                        "chain_type": message.type,
                        "message_id": message_id,
                        "platform_extras": platform_extras or {},
                    },
                )
            elif isinstance(comp, Json):
                await web_chat_back_queue.put(
                    {
                        "type": "plain",
                        "data": json.dumps(comp.data, ensure_ascii=False),
                        "streaming": streaming,
                        "chain_type": message.type,
                        "message_id": message_id,
                        "platform_extras": platform_extras or {},
                    },
                )
            elif isinstance(comp, Image):
                # save image to local
                filename = f"{str(uuid.uuid4())}.jpg"
                path = os.path.join(attachments_dir, filename)
                image_base64 = await comp.convert_to_base64()
                with open(path, "wb") as f:
                    f.write(base64.b64decode(image_base64))
                data = f"[IMAGE]{filename}"
                await web_chat_back_queue.put(
                    {
                        "type": "image",
                        "data": data,
                        "streaming": streaming,
                        "message_id": message_id,
                        "platform_extras": platform_extras or {},
                    },
                )
            elif isinstance(comp, Record):
                # save record to local
                filename = f"{str(uuid.uuid4())}.wav"
                path = os.path.join(attachments_dir, filename)
                record_base64 = await comp.convert_to_base64()
                with open(path, "wb") as f:
                    f.write(base64.b64decode(record_base64))
                data = f"[RECORD]{filename}"
                await web_chat_back_queue.put(
                    {
                        "type": "record",
                        "data": data,
                        "streaming": streaming,
                        "message_id": message_id,
                        "platform_extras": platform_extras or {},
                    },
                )
            elif isinstance(comp, File):
                # save file to local
                file_path = await comp.get_file()
                original_name = comp.name or os.path.basename(file_path)
                ext = os.path.splitext(original_name)[1] or ""
                filename = f"{uuid.uuid4()!s}{ext}"
                dest_path = os.path.join(attachments_dir, filename)
                shutil.copy2(file_path, dest_path)
                data = f"[FILE]{filename}"
                await web_chat_back_queue.put(
                    {
                        "type": "file",
                        "data": data,
                        "streaming": streaming,
                        "message_id": message_id,
                        "platform_extras": platform_extras or {},
                    },
                )
            else:
                logger.debug(f"webchat 忽略: {comp.type}")

        if emit_complete:
            await web_chat_back_queue.put(
                {
                    "type": "complete",
                    "data": data,
                    "streaming": streaming,
                    "chain_type": message.type,
                    "message_id": message_id,
                    "platform_extras": platform_extras or {},
                },
            )

        return data

    async def send(self, message: MessageChain | None) -> None:
        logger.debug(
            "WebChat send falling back to legacy path: session_id=%s message_id=%s",
            self.session_id,
            self.message_obj.message_id,
        )
        message_id = self.message_obj.message_id
        await WebChatMessageEvent._send(message_id, message, session_id=self.session_id)
        await super().send(MessageChain([]))

    async def send_interaction_message(
        self,
        message: MessageChain,
        *,
        platform_extras: dict | None = None,
        record_send_operation: bool = True,
    ) -> None:
        await WebChatMessageEvent._send(
            self.message_obj.message_id,
            message,
            session_id=self.session_id,
            platform_extras=platform_extras,
        )
        if record_send_operation:
            await super().send(MessageChain([]))

    async def complete_visible_turn(self) -> None:
        if self.get_extra("_visible_turn_completion_sent", False):
            return
        await WebChatMessageEvent._send(
            self.message_obj.message_id,
            None,
            session_id=self.session_id,
        )
        self.set_extra("_visible_turn_completion_sent", True)
        self.set_extra("_platform_completion_signal_sent", True)

    def requires_visible_turn_completion(self) -> bool:
        return True

    async def send_interaction_streaming(
        self,
        generator,
        *,
        platform_extras: dict | None = None,
        use_fallback: bool = False,
    ) -> None:
        await WebChatMessageEvent._send_streaming_via_back_queue(
            message_id=self.message_obj.message_id,
            session_id=self.session_id,
            generator=generator,
            platform_extras=platform_extras,
        )

    @staticmethod
    async def _send_streaming_via_back_queue(
        message_id: str,
        session_id: str,
        generator,
        platform_extras: dict | None = None,
    ) -> None:
        final_data = ""
        reasoning_content = ""
        request_id = str(message_id)
        conversation_id = _extract_conversation_id(session_id)
        web_chat_back_queue = webchat_queue_mgr.get_or_create_back_queue(
            request_id,
            conversation_id,
        )
        async for chain in generator:
            # 处理音频流（Live Mode）
            if chain.type == "audio_chunk":
                # 音频流数据，直接发送
                audio_b64 = ""
                text = None

                if chain.chain and isinstance(chain.chain[0], Plain):
                    audio_b64 = chain.chain[0].text

                if len(chain.chain) > 1 and isinstance(chain.chain[1], Json):
                    text = chain.chain[1].data.get("text")

                payload = {
                    "type": "audio_chunk",
                    "data": audio_b64,
                    "streaming": True,
                    "message_id": message_id,
                    "platform_extras": platform_extras or {},
                }
                if text:
                    payload["text"] = text

                await web_chat_back_queue.put(payload)
                continue

            # if chain.type == "break" and final_data:
            #     # 分割符
            #     await web_chat_back_queue.put(
            #         {
            #             "type": "break",  # break means a segment end
            #             "data": final_data,
            #             "streaming": True,
            #         },
            #     )
            #     final_data = ""
            #     continue

            r = await WebChatMessageEvent._send(
                message_id=message_id,
                message=chain,
                session_id=session_id,
                streaming=True,
                platform_extras=platform_extras,
            )
            if not r:
                continue
            if chain.type == "reasoning":
                reasoning_content += chain.get_plain_text()
            else:
                final_data += r

        await web_chat_back_queue.put(
            {
                "type": "complete",  # complete means we return the final result
                "data": final_data,
                "reasoning": reasoning_content,
                "streaming": True,
                "message_id": message_id,
                "platform_extras": platform_extras or {},
            },
        )

    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        logger.debug(
            "WebChat streaming falling back to legacy path: session_id=%s message_id=%s use_fallback=%s",
            self.session_id,
            self.message_obj.message_id,
            use_fallback,
        )
        await WebChatMessageEvent._send_streaming_via_back_queue(
            message_id=self.message_obj.message_id,
            session_id=self.session_id,
            generator=generator,
        )
        await super().send_streaming(generator, use_fallback)
