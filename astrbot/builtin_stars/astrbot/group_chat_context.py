import asyncio
import datetime
import random
import uuid
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from astrbot import logger
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import (
    At,
    AtAll,
    Face,
    File,
    Forward,
    Image,
    Plain,
    Record,
    Reply,
    Video,
)
from astrbot.api.platform import MessageType
from astrbot.api.provider import Provider, ProviderRequest
from astrbot.core.agent.message import TextPart
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.prompt import PromptExtension, PromptExtensionCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig

GROUP_HISTORY_HEADER = (
    "<system_reminder>"
    "You are in a group chat. "
    "Each sender is a distinct person; never merge identities based on nickname. "
    "Use user_id as the stable identity when available. "
    "The current speaker is identified separately in request_context/user_info, "
    "and the messages below are prior group messages after your last reply:\n"
    "--- BEGIN CONTEXT ---\n"
)
GROUP_HISTORY_FOOTER = "\n--- END CONTEXT ---\n</system_reminder>"
DEFAULT_GROUP_MESSAGE_MAX_CNT = 300
GROUP_CONTEXT_RECORD_ID_EXTRA = "_group_context_record_id"
GROUP_CONTEXT_RAW_IDX_EXTRA = "_group_context_raw_idx"
GROUP_CONTEXT_PROMPT_CONSUMED_EXTRA = "_group_context_prompt_consumed"


class GroupChatContext(PromptExtensionCollectorInterface):
    """Group chat context awareness with prompt-pipeline and legacy request exits."""

    def __init__(self, acm: AstrBotConfigManager, context: star.Context) -> None:
        self.acm = acm
        self.context = context
        self._locks: dict[str, asyncio.Lock] = {}
        self.raw_records: dict[str, deque[str]] = defaultdict(deque)
        self._record_ids: dict[str, deque[str]] = defaultdict(deque)

    @property
    def plugin_id(self) -> str:
        return "astrbot_group_chat_context"

    @property
    def priority(self) -> int:
        return 30

    def _get_lock(self, umo: str) -> asyncio.Lock:
        lock = self._locks.get(umo)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[umo] = lock
        return lock

    def cfg(self, event: AstrMessageEvent) -> dict:
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        group_context_cfg = cfg.get("provider_ltm_settings", {})
        provider_settings = cfg.get("provider_settings", {})
        image_caption_prompt = provider_settings.get("image_caption_prompt", "")
        image_caption_provider_id = group_context_cfg.get("image_caption_provider_id")
        image_caption = group_context_cfg.get("image_caption", False) and bool(
            image_caption_provider_id
        )
        active_reply = group_context_cfg.get("active_reply", {})
        return {
            "group_message_max_cnt": _positive_int(
                group_context_cfg.get(
                    "group_message_max_cnt",
                    DEFAULT_GROUP_MESSAGE_MAX_CNT,
                ),
                DEFAULT_GROUP_MESSAGE_MAX_CNT,
            ),
            "image_caption": image_caption,
            "image_caption_prompt": image_caption_prompt,
            "image_caption_provider_id": image_caption_provider_id,
            "image_caption_whitelist": group_context_cfg.get(
                "image_caption_whitelist", []
            ),
            "enable_active_reply": active_reply.get("enable", False),
            "ar_method": active_reply.get("method", "possibility_reply"),
            "ar_possibility": active_reply.get("possibility_reply", 0),
            "ar_prompt": active_reply.get("prompt", ""),
            "ar_whitelist": active_reply.get("whitelist", []),
        }

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: star.Context,
        config: "MainAgentBuildConfig",
        provider_request: ProviderRequest | None = None,
    ) -> list[PromptExtension]:
        del plugin_context, provider_request
        if _resolve_prompt_pipeline_mode(config) != "apply_visible":
            return []
        if not self.group_context_enabled(event):
            return []

        records = await self._snapshot_records_before_current(event)
        if not records:
            return []

        event.set_extra(GROUP_CONTEXT_PROMPT_CONSUMED_EXTRA, True)
        return [
            PromptExtension(
                plugin_id=self.plugin_id,
                mount="conversation",
                title="Group Chat Context",
                value={
                    "format": "group_recent_v1",
                    "records": records,
                    "text": _format_group_history_block(records),
                },
                value_kind="mapping",
                order=30,
                meta={
                    "record_count": len(records),
                    "targets": ["router", "persona", "core"],
                    "context_slot": "conversation.group_recent",
                    "context_category": "conversation",
                },
            )
        ]

    def group_context_enabled(self, event: AstrMessageEvent) -> bool:
        settings = self.context.get_config(umo=event.unified_msg_origin).get(
            "provider_ltm_settings",
            {},
        )
        return bool(settings.get("group_icl_enable", False))

    async def get_image_caption(
        self,
        image_url: str,
        image_caption_provider_id: str,
        image_caption_prompt: str,
    ) -> str:
        if not image_caption_provider_id:
            provider = self.context.get_using_provider()
        else:
            provider = self.context.get_provider_by_id(image_caption_provider_id)
            if not provider:
                raise Exception(f"没有找到 ID 为 {image_caption_provider_id} 的提供商")
        if not isinstance(provider, Provider):
            raise Exception(f"提供商类型错误({type(provider)})，无法获取图片描述")
        response = await provider.text_chat(
            prompt=image_caption_prompt,
            session_id=uuid.uuid4().hex,
            image_urls=[image_url],
            persist=False,
        )
        return response.completion_text

    async def need_active_reply(self, event: AstrMessageEvent) -> bool:
        cfg = self.cfg(event)
        if not cfg["enable_active_reply"]:
            return False
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return False
        if event.is_at_or_wake_command:
            return False
        if cfg["ar_whitelist"] and (
            event.unified_msg_origin not in cfg["ar_whitelist"]
            and (
                event.get_group_id() and event.get_group_id() not in cfg["ar_whitelist"]
            )
        ):
            return False
        match cfg["ar_method"]:
            case "possibility_reply":
                return random.random() < cfg["ar_possibility"]
        return False

    async def remove_session(self, event: AstrMessageEvent) -> int:
        umo = event.unified_msg_origin
        lock = self._get_lock(umo)
        async with lock:
            cnt = len(self.raw_records.get(umo, deque()))
            self.raw_records.pop(umo, None)
            self._record_ids.pop(umo, None)
        self._locks.pop(umo, None)
        return cnt

    async def handle_message(self, event: AstrMessageEvent) -> None:
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return
        if event.is_at_or_wake_command:
            return

        umo = event.unified_msg_origin
        cfg = self.cfg(event)
        final_message = await self._format_message(event, cfg)

        async with self._get_lock(umo):
            records = self.raw_records[umo]
            record_ids = self._record_ids[umo]
            record_id = uuid.uuid4().hex
            records.append(final_message)
            record_ids.append(record_id)
            _trim_left(records, cfg["group_message_max_cnt"], record_ids)
            event.set_extra(GROUP_CONTEXT_RECORD_ID_EXTRA, record_id)
            event.set_extra(GROUP_CONTEXT_RAW_IDX_EXTRA, len(records) - 1)
            event.set_extra(GROUP_CONTEXT_PROMPT_CONSUMED_EXTRA, False)

        logger.debug(f"group_chat_context | {umo} | {final_message}")

    async def on_req_llm(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if event.get_extra(GROUP_CONTEXT_PROMPT_CONSUMED_EXTRA, False):
            return
        if not self.group_context_enabled(event):
            return

        records = await self._snapshot_records_before_current(event)
        if records:
            req.extra_user_content_parts.append(
                TextPart(text=_format_group_history_block(records))
            )

    async def _snapshot_records_before_current(
        self,
        event: AstrMessageEvent,
    ) -> list[str]:
        umo = event.unified_msg_origin
        record_id = event.get_extra(GROUP_CONTEXT_RECORD_ID_EXTRA, None)
        prompt_idx = event.get_extra(GROUP_CONTEXT_RAW_IDX_EXTRA, -1)
        async with self._get_lock(umo):
            records = self.raw_records.get(umo)
            if not records:
                return []

            raw_list = list(records)
            id_list = list(self._record_ids.get(umo, deque()))
            if not isinstance(record_id, str) and (
                not isinstance(prompt_idx, int) or prompt_idx < 0
            ):
                return raw_list
            if isinstance(record_id, str) and record_id in id_list:
                prompt_idx = id_list.index(record_id)

            if prompt_idx >= len(raw_list):
                return []

            return raw_list[:prompt_idx]

    async def _format_message(self, event: AstrMessageEvent, cfg: dict) -> str:
        datetime_str = datetime.datetime.now().strftime("%H:%M:%S")
        sender = event.message_obj.sender
        nickname = _normalize_identity_text(getattr(sender, "nickname", None))
        user_id = _normalize_identity_text(getattr(sender, "user_id", None))
        sender_label = nickname or "Unknown"
        if user_id:
            sender_label += f" (user_id={user_id})"
        parts = [f"[{sender_label}/{datetime_str}]: "]

        for comp in event.get_messages():
            if isinstance(comp, Plain):
                parts.append(f" {comp.text}")
            elif isinstance(comp, Image):
                if cfg["image_caption"] and _image_caption_allowed(event, cfg):
                    try:
                        url = comp.url if comp.url else comp.file
                        if not url:
                            raise Exception("图片 URL 为空")
                        caption = await self.get_image_caption(
                            url,
                            cfg["image_caption_provider_id"],
                            cfg["image_caption_prompt"],
                        )
                        parts.append(f" [Image: {caption}]")
                    except Exception as e:
                        logger.error(f"获取图片描述失败: {e}")
                else:
                    parts.append(" [Image]")
            elif isinstance(comp, At):
                is_at_self = str(comp.qq) in (
                    event.get_self_id(),
                    "all",
                )
                if is_at_self:
                    parts.insert(1, "[DIRECTED AT YOU] ")
                parts.append(f" [At: {comp.name}]")
            elif isinstance(comp, Reply):
                quoted_sender = _format_quoted_sender(comp)
                if comp.message_str:
                    parts.append(
                        f" [Quote({quoted_sender}: {_truncate_reply_text(comp.message_str)})]"
                    )
                elif comp.chain:
                    chain_desc = _describe_chain(comp.chain)
                    parts.append(f" [Quote({quoted_sender}: {chain_desc})]")
                else:
                    parts.append(" [Quote]")

        return "".join(parts)


_MAX_REPLY_TEXT_LENGTH = 200


def _normalize_identity_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _format_quoted_sender(reply: Reply) -> str:
    nickname = _normalize_identity_text(reply.sender_nickname) or "Unknown"
    sender_id = _normalize_identity_text(reply.sender_id)
    if sender_id and sender_id != "0":
        return f"{nickname} (user_id={sender_id})"
    return nickname


def _describe_chain(chain: list) -> str:
    """Summarize message chain content for quoted reply display."""
    desc = []
    for comp in chain:
        if isinstance(comp, Plain) and getattr(comp, "text", None):
            desc.append(comp.text)
        elif isinstance(comp, Image):
            desc.append("[Image]")
        elif isinstance(comp, At):
            name = getattr(comp, "name", "") or getattr(comp, "qq", "")
            desc.append(f"[At: {name}]")
        elif isinstance(comp, Record):
            desc.append("[Voice]")
        elif isinstance(comp, Video):
            desc.append("[Video]")
        elif isinstance(comp, File):
            desc.append(f"[File: {getattr(comp, 'name', '') or ''}]")
        elif isinstance(comp, Forward):
            desc.append("[Forward]")
        elif isinstance(comp, AtAll):
            desc.append("[At: All]")
        elif isinstance(comp, Face):
            desc.append(f"[Sticker: {getattr(comp, 'id', '')}]")
        elif isinstance(comp, Reply):
            desc.append("[Quote]")
        else:
            desc.append(f"[{comp.__class__.__name__}]")
    return "".join(desc) or "[Unknown]"


def _truncate_reply_text(text: str) -> str:
    """Truncate overly long quoted reply text."""
    if len(text) <= _MAX_REPLY_TEXT_LENGTH:
        return text
    return text[:_MAX_REPLY_TEXT_LENGTH] + "..."


def _positive_int(value, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _image_caption_allowed(event: AstrMessageEvent, cfg: dict) -> bool:
    whitelist = _normalize_whitelist(cfg.get("image_caption_whitelist", []))
    if not whitelist:
        return True

    group_id = event.get_group_id()
    allowed_ids = {event.unified_msg_origin}
    if group_id:
        allowed_ids.add(str(group_id))
    return bool(allowed_ids & whitelist)


def _normalize_whitelist(value: object) -> set[str]:
    if isinstance(value, str):
        items = [value]
    else:
        try:
            items = list(value)
        except TypeError:
            items = []
    return {str(item).strip() for item in items if str(item).strip()}


def _resolve_prompt_pipeline_mode(config: "MainAgentBuildConfig") -> str:
    mode = (getattr(config, "prompt_pipeline_mode", "") or "").strip().lower()
    return mode or "apply_visible"


def _trim_left(
    records: deque[str],
    max_records: int,
    record_ids: deque[str] | None = None,
) -> None:
    while len(records) > max_records:
        records.popleft()
        if record_ids:
            record_ids.popleft()


def _format_group_history_block(records: list[str]) -> str:
    return GROUP_HISTORY_HEADER + "\n".join(records) + GROUP_HISTORY_FOOTER
