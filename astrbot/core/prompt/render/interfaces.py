"""Prompt render-layer interfaces and base renderer rules."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha1
from typing import TYPE_CHECKING, Any

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.skills.skill_manager import SkillInfo, build_skills_prompt
from astrbot.core.star.context import Context

from ..context_types import ContextPack, ContextSlot
from ..input_annotations import SUPPORTED_INPUT_ANNOTATION_FIELDS

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig

    from .prompt_tree import NodeRef, PromptBuilder


@dataclass
class RenderResult:
    """Unified render output for the prompt pipeline."""

    prompt_tree: PromptBuilder | None = None
    system_prompt: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_schema: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SerializedRenderValue:
    """Structured intermediate value produced by a renderer serializer."""

    slot_name: str
    group: str
    tag: str
    kind: str
    value: Any
    meta: dict[str, Any] = field(default_factory=dict)


class PromptSelectorInterface(ABC):
    """Abstract selector interface for prompt context packs."""

    @abstractmethod
    def select(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> ContextPack:
        """Select the context pack to pass into the render layer."""
        raise NotImplementedError

    async def select_async(
        self,
        pack: ContextPack,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> ContextPack:
        """Select the context pack asynchronously when a selector needs I/O."""
        return self.select(
            pack,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )


class BasePromptRenderer:
    """Base rule provider for prompt rendering."""

    ALL_SLOT_GROUPS: tuple[str, ...] = (
        "system",
        "persona",
        "policy",
        "input",
        "session",
        "conversation",
        "knowledge",
        "capability",
        "memory",
        "extension",
    )

    def get_name(self) -> str:
        """Return the stable renderer name."""
        return "base"

    def get_root_tag(self) -> str:
        """Return the prompt-tree root tag."""
        return "prompt"

    def get_enabled_slot_groups(self) -> tuple[str, ...]:
        """Return enabled logical data groups for this renderer."""
        return self.ALL_SLOT_GROUPS

    def include_session_in_system_prompt(self) -> bool:
        """Return whether session nodes remain in compiled system prompt."""
        return False

    def escape_render_text(self, text: str) -> str:
        """Escape raw text before inserting it into renderer-owned markup."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def get_node_structure(self) -> dict[str, str]:
        """Return the default physical node path for each logical group."""
        return {
            "system": "system/core",
            "persona": "system/persona",
            "policy": "system/policy",
            "input": "user_input",
            "session": "system/session",
            "conversation": "history/conversation",
            "knowledge": "context/knowledge",
            "capability": "system/capability",
            "memory": "context/memory",
            "extension": "system/extensions",
        }

    def render_prompt_tree(
        self,
        prompt_tree: PromptBuilder,
        *,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> RenderResult:
        """Compile the built prompt tree into generic prompt payload sections."""
        del event, plugin_context, config, provider_request
        rendered_groups = prompt_tree._root_node.meta.get("rendered_groups", [])
        rendered_slots = prompt_tree._root_node.meta.get("rendered_slots", [])
        self._prune_empty_prompt_tree(prompt_tree)
        system_prompt = self._compile_system_prompt(prompt_tree)
        messages = self._compile_messages(prompt_tree)
        tool_schema = self._compile_tool_schema(prompt_tree)
        return RenderResult(
            prompt_tree=prompt_tree,
            system_prompt=system_prompt,
            messages=messages,
            tool_schema=tool_schema,
            metadata=self._build_render_metadata(
                prompt_tree=prompt_tree,
                rendered_groups=rendered_groups,
                rendered_slots=rendered_slots,
                compiled_message_count=len(messages),
                compiled_tool_count=len(tool_schema or []),
            ),
        )

    def render_system_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del pack, resolve_node, event, plugin_context, config, provider_request

        rendered_slot_names: list[str] = []
        for slot_name, child_tag in (
            ("system.base", "base"),
            ("system.tool_call_instruction", "tool_call_instruction"),
            ("system.live_mode_prompt", "live_mode"),
        ):
            slot = self._find_slot(slots, slot_name)
            if slot is None:
                continue
            if self._add_text_tag(
                target,
                child_tag,
                self._stringify_slot_value(slot),
                meta=self._slot_meta(slot),
            ):
                rendered_slot_names.append(slot.name)

        workspace_prompt_slot = self._find_slot(slots, "system.workspace_extra_prompt")
        if self._render_mapping_slot(
            target,
            "workspace_extra_prompt",
            workspace_prompt_slot,
            body_keys=("path", "text"),
        ):
            rendered_slot_names.append("system.workspace_extra_prompt")
        return rendered_slot_names

    def render_persona_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del event, plugin_context, config, provider_request

        rendered_slot_names: list[str] = []

        segments_slot = self._find_slot(slots, "persona.segments")
        if segments_slot is not None and isinstance(segments_slot.value, dict):
            if self._render_persona_segments(target, segments_slot):
                rendered_slot_names.append(segments_slot.name)
        else:
            prompt_slot = self._find_slot(slots, "persona.prompt")
            if prompt_slot is not None and self._add_text_tag(
                target,
                "prompt",
                self._stringify_slot_value(prompt_slot),
                meta=self._slot_meta(prompt_slot),
            ):
                rendered_slot_names.append(prompt_slot.name)

        begin_dialogs_slot = pack.get_slot("persona.begin_dialogs")
        if begin_dialogs_slot is not None and isinstance(
            begin_dialogs_slot.value, list
        ):
            begin_target = resolve_node("history/begin_dialogs")
            if self._render_message_history(
                begin_target,
                begin_dialogs_slot.value,
                meta=self._slot_meta(begin_dialogs_slot),
            ):
                rendered_slot_names.append(begin_dialogs_slot.name)

        return rendered_slot_names

    def render_policy_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del pack, resolve_node, event, plugin_context, config, provider_request

        rendered_slot_names: list[str] = []
        for slot_name, child_tag in (
            ("policy.safety_prompt", "safety"),
            ("policy.sandbox_prompt", "sandbox"),
            ("policy.local_env_prompt", "local_env"),
        ):
            slot = self._find_slot(slots, slot_name)
            if slot is None:
                continue
            if self._add_text_tag(
                target,
                child_tag,
                self._stringify_slot_value(slot),
                meta=self._slot_meta(slot),
            ):
                rendered_slot_names.append(slot.name)
        return rendered_slot_names

    def render_input_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del pack, target, event, plugin_context, config, provider_request

        rendered_slot_names: list[str] = []

        text_slot = self._find_slot(slots, "input.text")
        if text_slot is not None:
            text_target = resolve_node("user_input/text")
            text_rendered = self._add_text_tag(
                text_target,
                "content",
                self._stringify_slot_value(text_slot),
                meta=self._slot_meta(text_slot),
            )
            annotation_rendered = self._render_annotation_mapping(
                text_target,
                self._extract_input_annotation_fields(text_slot.meta),
            )
            if text_rendered or annotation_rendered:
                rendered_slot_names.append(text_slot.name)

        quoted_text_slot = self._find_slot(slots, "input.quoted_text")
        if quoted_text_slot is not None:
            quoted_target = resolve_node("user_input/quoted")
            text_rendered = self._add_text_tag(
                quoted_target,
                "text",
                self._stringify_slot_value(quoted_text_slot),
                meta=self._slot_meta(quoted_text_slot),
            )
            annotation_rendered = self._render_annotation_mapping(
                quoted_target,
                self._extract_input_annotation_fields(quoted_text_slot.meta),
            )
            if text_rendered or annotation_rendered:
                rendered_slot_names.append(quoted_text_slot.name)

        quoted_images_slot = self._find_slot(slots, "input.quoted_images")
        if quoted_images_slot is not None:
            images_rendered = self._render_image_records(
                resolve_node("user_input/quoted/images"),
                quoted_images_slot,
            )
            annotations_rendered = self._render_input_annotation_records(
                resolve_node("user_input/quoted/image_annotations"),
                records=self._coerce_list(quoted_images_slot.value),
                item_tag="image_annotation",
                body_keys=("ref",),
            )
            if images_rendered or annotations_rendered:
                rendered_slot_names.append(quoted_images_slot.name)

        quoted_image_captions_slot = self._find_slot(
            slots, "input.quoted_image_captions"
        )
        if quoted_image_captions_slot is not None and self._render_record_items(
            resolve_node("user_input/quoted_image_captions"),
            quoted_image_captions_slot,
            item_tag="image_caption",
            body_keys=("ref", "caption"),
        ):
            rendered_slot_names.append(quoted_image_captions_slot.name)

        images_slot = self._find_slot(slots, "input.images")
        if images_slot is not None:
            images_rendered = self._render_image_records(
                resolve_node("user_input/attachments/images"),
                images_slot,
            )
            annotations_rendered = self._render_input_annotation_records(
                resolve_node("user_input/attachments/image_annotations"),
                records=self._coerce_list(images_slot.value),
                item_tag="image_annotation",
                body_keys=("ref",),
            )
            if images_rendered or annotations_rendered:
                rendered_slot_names.append(images_slot.name)

        image_captions_slot = self._find_slot(slots, "input.image_captions")
        if image_captions_slot is not None and self._render_record_items(
            resolve_node("user_input/attachments/image_captions"),
            image_captions_slot,
            item_tag="image_caption",
            body_keys=("ref", "caption"),
        ):
            rendered_slot_names.append(image_captions_slot.name)

        files_slot = self._find_slot(slots, "input.files")
        if files_slot is not None:
            files_rendered = self._render_file_records(
                resolve_node("user_input/attachments/files"),
                files_slot,
            )
            annotations_rendered = self._render_input_annotation_records(
                resolve_node("user_input/attachments/file_annotations"),
                records=self._coerce_list(files_slot.value),
                item_tag="file_annotation",
                body_keys=("name", "ref"),
                ref_resolver=self._resolve_file_ref,
            )
            if files_rendered or annotations_rendered:
                rendered_slot_names.append(files_slot.name)

        file_extracts_slot = self._find_slot(slots, "input.file_extracts")
        if file_extracts_slot is not None and self._render_record_items(
            resolve_node("user_input/attachments/file_extracts"),
            file_extracts_slot,
            item_tag="file_extract",
            body_keys=("name", "content"),
        ):
            rendered_slot_names.append(file_extracts_slot.name)

        return rendered_slot_names

    def render_session_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del pack, resolve_node, event, plugin_context, config, provider_request

        rendered_slot_names: list[str] = []

        datetime_slot = self._find_slot(slots, "session.datetime")
        if datetime_slot is not None and isinstance(datetime_slot.value, dict):
            payload = datetime_slot.value
            meta = self._slot_meta(
                datetime_slot,
                {
                    "iso": payload.get("iso"),
                    "timezone": payload.get("timezone"),
                    "source": payload.get("source"),
                },
            )
            if self._add_text_tag(
                target,
                "datetime",
                self._clean_text(payload.get("text")),
                meta=meta,
            ):
                rendered_slot_names.append(datetime_slot.name)

        user_info_slot = self._find_slot(slots, "session.user_info")
        if user_info_slot is not None and isinstance(user_info_slot.value, dict):
            payload = user_info_slot.value
            meta = self._slot_meta(
                user_info_slot,
                {
                    "user_id": payload.get("user_id"),
                    "umo": payload.get("umo"),
                    "group_id": payload.get("group_id"),
                },
            )
            user_ref = self._add_parent_tag(target, "user_info", meta=meta)
            fields_rendered = False
            fields_rendered |= self._add_text_tag(
                user_ref,
                "nickname",
                self._clean_text(payload.get("nickname")),
            )
            fields_rendered |= self._add_text_tag(
                user_ref,
                "platform_name",
                self._clean_text(payload.get("platform_name")),
            )
            fields_rendered |= self._add_text_tag(
                user_ref,
                "group_name",
                self._clean_text(payload.get("group_name")),
            )
            if isinstance(payload.get("is_group"), bool):
                fields_rendered |= self._add_text_tag(
                    user_ref,
                    "is_group",
                    "true" if payload["is_group"] else "false",
                )
            if fields_rendered:
                rendered_slot_names.append(user_info_slot.name)

        return rendered_slot_names

    def render_conversation_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del pack, resolve_node, event, plugin_context, config, provider_request

        history_slot = self._find_slot(slots, "conversation.history")
        if history_slot is None or not isinstance(history_slot.value, dict):
            return []

        payload = history_slot.value
        turns = payload.get("turns")
        if not isinstance(turns, list) or not turns:
            return []

        if self._render_turn_pairs(
            target,
            turns,
            meta=self._slot_meta(
                history_slot,
                {
                    "format": payload.get("format"),
                    "source": payload.get("source"),
                    "conversation_id": payload.get("conversation_id"),
                    "turn_count": payload.get("turn_count"),
                },
            ),
        ):
            return [history_slot.name]
        return []

    def render_knowledge_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del pack, resolve_node, event, plugin_context, config, provider_request

        knowledge_slot = self._find_slot(slots, "knowledge.snippets")
        if knowledge_slot is None or not isinstance(knowledge_slot.value, dict):
            return []

        payload = knowledge_slot.value
        if self._add_text_tag(
            target,
            "snippets",
            self._clean_text(payload.get("text")),
            meta=self._slot_meta(
                knowledge_slot,
                {
                    "format": payload.get("format"),
                    "query": payload.get("query"),
                },
            ),
        ):
            return [knowledge_slot.name]
        return []

    def render_capability_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del event, plugin_context, config, provider_request

        rendered_slot_names: list[str] = []

        skills_slot = self._find_slot(slots, "capability.skills_prompt")
        if skills_slot is not None and isinstance(skills_slot.value, dict):
            skills_inventory = self._coerce_list(skills_slot.value.get("skills"))
            skills_whitelist = self._extract_name_whitelist(
                pack.get_slot("persona.skills_whitelist")
            )
            filtered_skills = self._filter_named_items(
                skills_inventory, skills_whitelist
            )
            if filtered_skills:
                skill_infos = [
                    SkillInfo(
                        name=str(item.get("name", "")),
                        description=str(item.get("description", "")),
                        path=str(item.get("path", "")),
                        active=bool(item.get("active", False)),
                        source_type=str(item.get("source_type", "local_only")),
                        source_label=str(item.get("source_label", "local")),
                        local_exists=bool(item.get("local_exists", False)),
                        sandbox_exists=bool(item.get("sandbox_exists", False)),
                    )
                    for item in filtered_skills
                    if item.get("name")
                ]
                skills_prompt = (
                    build_skills_prompt(skill_infos) if skill_infos else None
                )
                if self._add_text_tag(
                    target,
                    "skills",
                    skills_prompt,
                    meta=self._slot_meta(
                        skills_slot,
                        {
                            "format": skills_slot.value.get("format"),
                            "runtime": skills_slot.value.get("runtime"),
                            "skill_count": len(skill_infos),
                            "whitelist": skills_whitelist,
                        },
                    ),
                ):
                    rendered_slot_names.append(skills_slot.name)

        router_slot = self._find_slot(slots, "capability.subagent_router_prompt")
        if router_slot is not None and self._add_text_tag(
            target,
            "subagent_router",
            self._stringify_slot_value(router_slot),
            meta=self._slot_meta(router_slot),
        ):
            rendered_slot_names.append(router_slot.name)

        tools_slot = self._find_slot(slots, "capability.tools_schema")
        if tools_slot is not None and isinstance(tools_slot.value, dict):
            tool_inventory = self._coerce_list(tools_slot.value.get("tools"))
            tools_whitelist = self._extract_name_whitelist(
                pack.get_slot("persona.tools_whitelist")
            )
            filtered_tools = self._filter_named_items(tool_inventory, tools_whitelist)
            if self._render_tool_inventory(
                resolve_node("tools/function_tools"),
                filtered_tools,
                meta=self._slot_meta(
                    tools_slot,
                    {
                        "format": tools_slot.value.get("format"),
                        "tool_count": len(filtered_tools),
                        "whitelist": tools_whitelist,
                    },
                ),
            ):
                rendered_slot_names.append(tools_slot.name)

        handoff_slot = self._find_slot(slots, "capability.subagent_handoff_tools")
        if handoff_slot is not None and isinstance(handoff_slot.value, dict):
            handoff_tools = self._coerce_list(handoff_slot.value.get("tools"))
            if self._render_handoff_inventory(
                resolve_node("tools/subagent_handoff"),
                handoff_tools,
                meta=self._slot_meta(
                    handoff_slot,
                    {
                        "format": handoff_slot.value.get("format"),
                        "tool_count": handoff_slot.value.get("tool_count"),
                        "main_enable": handoff_slot.value.get("main_enable"),
                        "remove_main_duplicate_tools": handoff_slot.value.get(
                            "remove_main_duplicate_tools"
                        ),
                    },
                ),
            ):
                rendered_slot_names.append(handoff_slot.name)

        return rendered_slot_names

    def render_memory_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del pack, resolve_node, event, plugin_context, config, provider_request

        rendered_slot_names: list[str] = []
        slot_map = {slot.name: slot for slot in slots}

        if self._render_mapping_slot(
            target,
            "topic_state",
            slot_map.get("memory.topic_state"),
            body_keys=("current_topic", "topic_summary", "topic_confidence"),
        ):
            rendered_slot_names.append("memory.topic_state")

        if self._render_mapping_slot(
            target,
            "short_term",
            slot_map.get("memory.short_term"),
            body_keys=("short_summary", "active_focus"),
        ):
            rendered_slot_names.append("memory.short_term")

        experiences_slot = slot_map.get("memory.experiences")
        if experiences_slot is not None and isinstance(experiences_slot.value, dict):
            items = self._coerce_list(experiences_slot.value.get("items"))
            if self._render_record_list(
                target,
                parent_tag="experiences",
                item_tag="experience",
                items=items,
                body_keys=(
                    "category",
                    "summary",
                    "detail_summary",
                    "importance",
                    "confidence",
                ),
                meta=self._slot_meta(
                    experiences_slot,
                    {"count": experiences_slot.value.get("count")},
                ),
            ):
                rendered_slot_names.append(experiences_slot.name)

        long_term_slot = slot_map.get("memory.long_term_memories")
        if long_term_slot is not None and isinstance(long_term_slot.value, dict):
            items = self._coerce_list(long_term_slot.value.get("items"))
            if self._render_record_list(
                target,
                parent_tag="long_term_memories",
                item_tag="memory",
                items=items,
                body_keys=(
                    "title",
                    "summary",
                    "category",
                    "tags",
                    "importance",
                    "confidence",
                ),
                meta=self._slot_meta(
                    long_term_slot,
                    {"count": long_term_slot.value.get("count")},
                ),
            ):
                rendered_slot_names.append(long_term_slot.name)

        if self._render_mapping_slot(
            target,
            "persona_state",
            slot_map.get("memory.persona_state"),
            body_keys=(
                "persona_id",
                "familiarity",
                "trust",
                "warmth",
                "formality_preference",
                "directness_preference",
            ),
        ):
            rendered_slot_names.append("memory.persona_state")

        return rendered_slot_names

    def render_extension_context(
        self,
        target: NodeRef,
        slots: list[ContextSlot],
        *,
        pack: ContextPack,
        resolve_node: Callable[[str], NodeRef],
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config: MainAgentBuildConfig | None = None,
        provider_request: ProviderRequest | None = None,
    ) -> list[str]:
        del target, pack, event, plugin_context, config, provider_request

        rendered_slot_names: list[str] = []
        mount_targets = {
            "system": "system/extensions",
            "context": "context/extensions",
            "input": "user_input/extensions",
            "conversation": "system/conversation_extensions",
            "memory": "context/memory/extensions",
            "capability": "system/capability/extensions",
        }

        for mount, node_path in mount_targets.items():
            slot = self._find_slot(slots, f"extension.{mount}")
            if slot is None:
                continue
            if self._render_prompt_extensions(resolve_node(node_path), slot):
                rendered_slot_names.append(slot.name)

        return rendered_slot_names

    def _compile_system_prompt(self, prompt_tree: PromptBuilder) -> str | None:
        system_node = self._find_tag_path(prompt_tree, "system")
        if system_node is None:
            return None

        if not self._system_prompt_has_visible_content(prompt_tree, system_node):
            return None
        rendered = self._render_system_prompt_text(prompt_tree, system_node)
        return rendered or None

    def _compile_messages(self, prompt_tree: PromptBuilder) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        for history_path in ("history/begin_dialogs", "history/conversation"):
            history_node = self._find_tag_path(prompt_tree, history_path)
            if history_node is None:
                continue
            messages.extend(self._compile_turn_messages(prompt_tree, history_node))

        for context_path in (
            "context/extensions",
            "context/memory",
            "context/knowledge",
        ):
            context_message = self._compile_context_message(
                prompt_tree,
                context_path,
            )
            if context_message is not None:
                messages.append(context_message)

        user_message = self._compile_user_input_message(prompt_tree)
        if user_message is not None:
            messages.append(user_message)

        return messages

    def _compile_context_message(
        self,
        prompt_tree: PromptBuilder,
        node_path: str,
    ) -> dict[str, Any] | None:
        context_node = self._find_tag_path(prompt_tree, node_path)
        if context_node is None:
            return None
        content = self._render_subtree_text(
            prompt_tree,
            context_node,
            include_root=True,
            escape_text=True,
        )
        if not content:
            return None
        return {
            "role": "user",
            "content": content,
            "_no_save": True,
        }

    def _compile_user_input_message(
        self,
        prompt_tree: PromptBuilder,
    ) -> dict[str, Any] | None:
        user_input_node = self._find_tag_path(prompt_tree, "user_input")
        if user_input_node is None:
            return None

        content_parts: list[dict[str, Any]] = []

        text_node = self._find_tag_path(prompt_tree, "user_input/text")
        current_text = (
            self._extract_child_tag_text(prompt_tree, text_node, "content")
            if text_node is not None
            else None
        )
        current_text_annotation = (
            self._extract_annotation_from_node(prompt_tree, text_node)
            if text_node is not None
            else {}
        )

        quoted_node = self._find_tag_path(prompt_tree, "user_input/quoted")
        quoted_text = (
            self._extract_child_tag_text(prompt_tree, quoted_node, "text")
            if quoted_node is not None
            else None
        )
        quoted_text_annotation = (
            self._extract_annotation_from_node(prompt_tree, quoted_node)
            if quoted_node is not None
            else {}
        )

        quoted_images_node = self._find_tag_path(
            prompt_tree, "user_input/quoted/images"
        )
        quoted_image_annotations_node = self._find_tag_path(
            prompt_tree, "user_input/quoted/image_annotations"
        )
        quoted_image_captions_node = self._find_tag_path(
            prompt_tree, "user_input/quoted_image_captions"
        )
        attachment_images_node = self._find_tag_path(
            prompt_tree, "user_input/attachments/images"
        )
        attachment_image_annotations_node = self._find_tag_path(
            prompt_tree, "user_input/attachments/image_annotations"
        )
        attachment_image_captions_node = self._find_tag_path(
            prompt_tree, "user_input/attachments/image_captions"
        )
        attachment_files_node = self._find_tag_path(
            prompt_tree, "user_input/attachments/files"
        )
        file_annotations_node = self._find_tag_path(
            prompt_tree, "user_input/attachments/file_annotations"
        )
        file_extracts_node = self._find_tag_path(
            prompt_tree, "user_input/attachments/file_extracts"
        )

        request_context_text = self._compile_request_context_text(prompt_tree)
        user_input_text = self._compile_user_input_text(
            current_text=current_text,
            quoted_text=quoted_text,
            current_text_annotation=current_text_annotation,
            quoted_text_annotation=quoted_text_annotation,
        )
        quoted_image_annotations_text = (
            self._render_subtree_text(
                prompt_tree,
                quoted_image_annotations_node,
                include_root=True,
                escape_text=True,
            )
            if quoted_image_annotations_node is not None
            else None
        )
        quoted_image_captions_text = (
            self._render_subtree_text(
                prompt_tree,
                quoted_image_captions_node,
                include_root=True,
                escape_text=True,
            )
            if quoted_image_captions_node is not None
            else None
        )
        attachment_image_annotations_text = (
            self._render_subtree_text(
                prompt_tree,
                attachment_image_annotations_node,
                include_root=True,
                escape_text=True,
            )
            if attachment_image_annotations_node is not None
            else None
        )
        attachment_image_captions_text = (
            self._render_subtree_text(
                prompt_tree,
                attachment_image_captions_node,
                include_root=True,
                escape_text=True,
            )
            if attachment_image_captions_node is not None
            else None
        )
        file_annotations_text = (
            self._render_subtree_text(
                prompt_tree,
                file_annotations_node,
                include_root=True,
                escape_text=True,
            )
            if file_annotations_node is not None
            else None
        )
        file_extracts_text = (
            self._render_subtree_text(
                prompt_tree,
                file_extracts_node,
                include_root=True,
                escape_text=True,
            )
            if file_extracts_node is not None
            else None
        )
        input_extensions_text = self._compile_input_extensions_text(prompt_tree)
        quoted_image_parts = (
            self._compile_image_content_parts(
                prompt_tree,
                quoted_images_node,
            )
            if quoted_images_node is not None
            else []
        )
        attachment_image_parts = (
            self._compile_image_content_parts(
                prompt_tree,
                attachment_images_node,
            )
            if attachment_images_node is not None
            else []
        )
        file_text_parts = (
            self._compile_file_text_parts(
                prompt_tree,
                attachment_files_node,
            )
            if attachment_files_node is not None
            else []
        )

        if (
            current_text
            and not request_context_text
            and not input_extensions_text
            and not quoted_text
            and not current_text_annotation
            and not quoted_text_annotation
            and not quoted_image_annotations_text
            and not quoted_image_captions_text
            and not attachment_image_annotations_text
            and not attachment_image_captions_text
            and not file_annotations_text
            and not file_extracts_text
            and not quoted_image_parts
            and not attachment_image_parts
            and not file_text_parts
        ):
            return {"role": "user", "content": current_text}

        if request_context_text:
            content_parts.append(self._build_text_content_part(request_context_text))
        if input_extensions_text:
            content_parts.append(self._build_text_content_part(input_extensions_text))
        if user_input_text:
            content_parts.append(self._build_text_content_part(user_input_text))
        if quoted_image_annotations_text:
            content_parts.append(
                self._build_text_content_part(quoted_image_annotations_text)
            )
        if quoted_image_captions_text:
            content_parts.append(
                self._build_text_content_part(quoted_image_captions_text)
            )
        if attachment_image_annotations_text:
            content_parts.append(
                self._build_text_content_part(attachment_image_annotations_text)
            )
        if attachment_image_captions_text:
            content_parts.append(
                self._build_text_content_part(attachment_image_captions_text)
            )
        if file_annotations_text:
            content_parts.append(self._build_text_content_part(file_annotations_text))
        if file_extracts_text:
            content_parts.append(self._build_text_content_part(file_extracts_text))
        content_parts.extend(quoted_image_parts)
        content_parts.extend(attachment_image_parts)
        content_parts.extend(file_text_parts)

        if not content_parts:
            fallback_content = self._render_subtree_text(
                prompt_tree,
                user_input_node,
                include_root=True,
            )
            if not fallback_content:
                return None
            return {"role": "user", "content": fallback_content}

        return {"role": "user", "content": content_parts}

    def _compile_tool_schema(
        self, prompt_tree: PromptBuilder
    ) -> list[dict[str, Any]] | None:
        schemas: list[dict[str, Any]] = []

        function_tools_node = self._find_tag_path(prompt_tree, "tools/function_tools")
        if function_tools_node is not None:
            schemas.extend(
                self._compile_tool_nodes(
                    prompt_tree,
                    function_tools_node,
                    prefer_existing_schema=True,
                )
            )

        handoff_tools_node = self._find_tag_path(prompt_tree, "tools/subagent_handoff")
        if handoff_tools_node is not None:
            schemas.extend(
                self._compile_tool_nodes(
                    prompt_tree,
                    handoff_tools_node,
                    prefer_existing_schema=False,
                )
            )

        return schemas or None

    def _render_slots_as_children(
        self,
        group: str,
        target: NodeRef,
        slots: list[ContextSlot],
    ) -> list[str]:
        """Render a logical group into child tags under the target node."""
        rendered_slot_names: list[str] = []
        for serialized in self.serialize_group_slots(group, slots):
            content = self.render_serialized_value(serialized)
            if content is None:
                continue

            target.tag(
                serialized.tag,
                meta={
                    "slot_name": serialized.slot_name,
                    "group": serialized.group,
                    "value_kind": serialized.kind,
                    **serialized.meta,
                },
            ).add(content)
            rendered_slot_names.append(serialized.slot_name)
        return rendered_slot_names

    def serialize_group_slots(
        self,
        group: str,
        slots: list[ContextSlot],
    ) -> list[SerializedRenderValue]:
        """Serialize all slots in a logical group into structured values."""
        serialized_values: list[SerializedRenderValue] = []
        for slot in sorted(slots, key=lambda item: item.name):
            serialized = self.serialize_slot_value(slot, group=group)
            if serialized is not None:
                serialized_values.append(serialized)
        return serialized_values

    def serialize_slot_value(
        self,
        slot: ContextSlot,
        *,
        group: str,
    ) -> SerializedRenderValue | None:
        """Serialize a single slot into a structured intermediate value."""
        tag = self._slot_to_child_tag(slot.name, group)
        value = slot.value

        if group == "knowledge" and isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str):
                resolved = text.strip()
                if resolved:
                    return SerializedRenderValue(
                        slot_name=slot.name,
                        group=group,
                        tag=tag,
                        kind="text",
                        value=resolved,
                    )

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            return SerializedRenderValue(
                slot_name=slot.name,
                group=group,
                tag=tag,
                kind="text",
                value=text,
            )

        if value is None:
            return None

        if isinstance(value, dict):
            return SerializedRenderValue(
                slot_name=slot.name,
                group=group,
                tag=tag,
                kind="mapping",
                value=value,
            )

        if isinstance(value, list):
            return SerializedRenderValue(
                slot_name=slot.name,
                group=group,
                tag=tag,
                kind="sequence",
                value=value,
            )

        if isinstance(value, (bool, int, float)):
            return SerializedRenderValue(
                slot_name=slot.name,
                group=group,
                tag=tag,
                kind="scalar",
                value=value,
            )

        return SerializedRenderValue(
            slot_name=slot.name,
            group=group,
            tag=tag,
            kind="scalar",
            value=str(value),
        )

    def render_serialized_value(self, serialized: SerializedRenderValue) -> str | None:
        """Render a structured intermediate value into node text."""
        if serialized.kind == "text":
            value = serialized.value
            if not isinstance(value, str):
                return None
            return value

        return json.dumps(
            serialized.value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    def _find_tag_path(
        self,
        prompt_tree: PromptBuilder,
        node_path: str,
    ):
        parts = [part for part in node_path.split("/") if part]
        if not parts:
            return prompt_tree._root_node

        root_tag = prompt_tree._root_node.meta.get("tag")
        if parts and parts[0] == root_tag:
            parts = parts[1:]

        current = prompt_tree._root_node
        for part in parts:
            next_node = None
            for child in self._iter_structured_children(prompt_tree, current):
                if (
                    child.meta.get("kind") == "tag_start"
                    and child.meta.get("tag") == part
                ):
                    next_node = child
                    break
            if next_node is None:
                return None
            current = next_node
        return current

    def _render_subtree_text(
        self,
        prompt_tree: PromptBuilder,
        node,
        *,
        include_root: bool,
        escape_text: bool = False,
    ) -> str | None:
        lines: list[str] = []
        base_depth = node.depth if include_root else node.depth + 1

        if include_root:
            self._render_node_relative(
                prompt_tree,
                node,
                lines,
                base_depth=base_depth,
                escape_text=escape_text,
            )
        else:
            for child in self._iter_structured_children(prompt_tree, node):
                self._render_node_relative(
                    prompt_tree,
                    child,
                    lines,
                    base_depth=base_depth,
                    escape_text=escape_text,
                )

        while lines and lines[-1] == "":
            lines.pop()

        if not lines:
            return None
        return prompt_tree.newline.join(lines)

    def _render_system_prompt_text(
        self,
        prompt_tree: PromptBuilder,
        system_node,
    ) -> str | None:
        lines: list[str] = []
        base_depth = system_node.depth
        indent = " " * (0 * prompt_tree.indent_size)
        include_session = self.include_session_in_system_prompt()

        lines.append(f"{indent}<{system_node.meta.get('tag', 'system')}>")
        for child in self._iter_structured_children(prompt_tree, system_node):
            if (
                not include_session
                and child.meta.get("kind") == "tag_start"
                and child.meta.get("tag") == "session"
            ):
                continue
            self._render_node_relative(
                prompt_tree,
                child,
                lines,
                base_depth=base_depth,
                escape_text=True,
            )
        lines.append(f"{indent}</{system_node.meta.get('tag', 'system')}>")

        while lines and lines[-1] == "":
            lines.pop()

        if len(lines) <= 2:
            return None
        return prompt_tree.newline.join(lines)

    def _system_prompt_has_visible_content(
        self,
        prompt_tree: PromptBuilder,
        system_node,
    ) -> bool:
        include_session = self.include_session_in_system_prompt()
        for child in self._iter_structured_children(prompt_tree, system_node):
            if (
                not include_session
                and child.meta.get("kind") == "tag_start"
                and child.meta.get("tag") == "session"
            ):
                continue
            rendered = self._render_subtree_text(
                prompt_tree,
                child,
                include_root=True,
            )
            if rendered:
                return True
        return False

    def _render_node_relative(
        self,
        prompt_tree: PromptBuilder,
        node,
        lines: list[str],
        *,
        base_depth: int,
        escape_text: bool = False,
    ) -> None:
        if not node.enabled:
            return

        kind = node.meta.get("kind")
        is_container = kind in {"container", "container_root"}
        indent_depth = max(node.depth - base_depth, 0)

        if kind == "tag_end":
            parent_depth = node.parent.depth if node.parent is not None else 0
            indent_depth = max(parent_depth - base_depth, 0)

        indent = " " * (indent_depth * prompt_tree.indent_size)
        text = node.text
        if text and escape_text and kind not in {"tag_start", "tag_end"}:
            text = self.escape_render_text(text)

        if text:
            for line in text.splitlines():
                lines.append(indent + line)
        elif not is_container:
            lines.append("")

        for child in sorted(node.children, key=prompt_tree._sort_key):
            self._render_node_relative(
                prompt_tree,
                child,
                lines,
                base_depth=base_depth,
                escape_text=escape_text,
            )

    def _compile_turn_messages(
        self,
        prompt_tree: PromptBuilder,
        parent_node,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for turn_node in self._iter_descendant_tags(
            prompt_tree, parent_node, tag="turn"
        ):
            for role in ("user", "assistant"):
                role_node = self._find_direct_child_tag(
                    turn_node, role, prompt_tree=prompt_tree
                )
                if role_node is None:
                    continue
                content = self._render_subtree_text(
                    prompt_tree,
                    role_node,
                    include_root=False,
                )
                if content:
                    messages.append({"role": role, "content": content})
        return messages

    def _compile_tool_nodes(
        self,
        prompt_tree: PromptBuilder,
        parent_node,
        *,
        prefer_existing_schema: bool,
    ) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tool_node in self._iter_descendant_tags(
            prompt_tree, parent_node, tag="tool"
        ):
            name = self._extract_child_tag_text(prompt_tree, tool_node, "name")
            if not name:
                continue
            description = self._extract_child_tag_text(
                prompt_tree, tool_node, "description"
            )
            parameters = tool_node.meta.get("parameters")
            schema = tool_node.meta.get("schema")

            if prefer_existing_schema and isinstance(schema, dict):
                compiled_schema = deepcopy(schema)
                function_block = compiled_schema.setdefault("function", {})
                function_block["name"] = name
                if description:
                    function_block["description"] = description
                elif (
                    "description" in function_block
                    and not function_block["description"]
                ):
                    function_block.pop("description", None)
                if parameters is not None:
                    function_block["parameters"] = deepcopy(parameters)
                schemas.append(compiled_schema)
                continue

            function_block: dict[str, Any] = {"name": name}
            if description:
                function_block["description"] = description
            if parameters is not None:
                function_block["parameters"] = deepcopy(parameters)
            schemas.append({"type": "function", "function": function_block})

        return schemas

    def _compile_image_content_parts(
        self,
        prompt_tree: PromptBuilder,
        parent_node,
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for image_node in self._iter_descendant_tags(
            prompt_tree, parent_node, tag="image"
        ):
            ref = self._render_subtree_text(prompt_tree, image_node, include_root=False)
            if not ref:
                continue

            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": ref},
                }
            )
        return parts

    def _compile_file_text_parts(
        self,
        prompt_tree: PromptBuilder,
        parent_node,
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for file_node in self._iter_descendant_tags(
            prompt_tree, parent_node, tag="file"
        ):
            name = self._extract_child_tag_text(prompt_tree, file_node, "name")
            ref = self._extract_child_tag_text(prompt_tree, file_node, "ref")
            if not name and not ref:
                continue

            source = file_node.meta.get("source")
            if source == "quoted":
                prefix = "[File Attachment in quoted message:"
            else:
                prefix = "[File Attachment:"

            details: list[str] = []
            if name:
                details.append(f"name {name}")
            if ref:
                details.append(f"path {ref}")

            file_text = prefix
            if details:
                file_text += f" {', '.join(details)}"
            file_text += "]"
            parts.append(self._build_text_content_part(file_text))
        return parts

    def _compile_request_context_text(
        self,
        prompt_tree: PromptBuilder,
    ) -> str | None:
        session_node = self._find_tag_path(prompt_tree, "system/session")
        if session_node is None:
            return None

        datetime_node = self._find_direct_child_tag(
            session_node,
            "datetime",
            prompt_tree=prompt_tree,
        )
        user_info_node = self._find_direct_child_tag(
            session_node,
            "user_info",
            prompt_tree=prompt_tree,
        )

        datetime_lines = self._compile_datetime_lines(prompt_tree, datetime_node)
        user_info_lines = self._compile_user_info_lines(prompt_tree, user_info_node)
        if not datetime_lines and not user_info_lines:
            return None

        lines = ["<request_context>", "  <session>"]
        lines.extend(datetime_lines)
        lines.extend(user_info_lines)
        lines.append("  </session>")
        lines.append("</request_context>")
        return "\n".join(lines)

    def _compile_input_extensions_text(
        self,
        prompt_tree: PromptBuilder,
    ) -> str | None:
        extensions_node = self._find_tag_path(prompt_tree, "user_input/extensions")
        if extensions_node is None:
            return None

        rendered = self._render_subtree_text(
            prompt_tree,
            extensions_node,
            include_root=True,
            escape_text=True,
        )
        return rendered or None

    def _compile_datetime_lines(
        self,
        prompt_tree: PromptBuilder,
        datetime_node,
    ) -> list[str]:
        if datetime_node is None:
            return []

        text = self._render_subtree_text(prompt_tree, datetime_node, include_root=False)
        values = {
            "text": text,
            "iso": datetime_node.meta.get("iso"),
            "timezone": datetime_node.meta.get("timezone"),
            "source": datetime_node.meta.get("source"),
        }
        if not any(values.values()):
            return []

        lines = ["    <datetime>"]
        for key in ("text", "iso", "timezone", "source"):
            value = self._clean_text(values.get(key))
            if value:
                lines.append(f"      <{key}>{self.escape_render_text(value)}</{key}>")
        lines.append("    </datetime>")
        return lines

    def _compile_user_info_lines(
        self,
        prompt_tree: PromptBuilder,
        user_info_node,
    ) -> list[str]:
        if user_info_node is None:
            return []

        values = {
            "user_id": user_info_node.meta.get("user_id"),
            "nickname": self._extract_child_tag_text(
                prompt_tree, user_info_node, "nickname"
            ),
            "platform_name": self._extract_child_tag_text(
                prompt_tree,
                user_info_node,
                "platform_name",
            ),
            "umo": user_info_node.meta.get("umo"),
            "group_id": user_info_node.meta.get("group_id"),
            "group_name": self._extract_child_tag_text(
                prompt_tree,
                user_info_node,
                "group_name",
            ),
            "is_group": self._extract_child_tag_text(
                prompt_tree, user_info_node, "is_group"
            ),
        }
        if not any(values.values()):
            return []

        lines = ["    <user_info>"]
        for key in (
            "user_id",
            "nickname",
            "platform_name",
            "umo",
            "group_id",
            "group_name",
            "is_group",
        ):
            value = self._clean_text(values.get(key))
            if value:
                lines.append(f"      <{key}>{self.escape_render_text(value)}</{key}>")
        lines.append("    </user_info>")
        return lines

    def _compile_user_input_text(
        self,
        *,
        current_text: str | None,
        quoted_text: str | None,
        current_text_annotation: dict[str, str] | None = None,
        quoted_text_annotation: dict[str, str] | None = None,
    ) -> str | None:
        normalized_current = self._clean_text(current_text)
        normalized_quoted = self._clean_text(quoted_text)
        current_annotation = self._clean_annotation_mapping(current_text_annotation)
        quoted_annotation = self._clean_annotation_mapping(quoted_text_annotation)
        if (
            not normalized_current
            and not normalized_quoted
            and not current_annotation
            and not quoted_annotation
        ):
            return None

        lines = ["<user_input>"]
        if normalized_current:
            lines.append(
                f"  <text>{self.escape_render_text(normalized_current)}</text>"
            )
        lines.extend(
            self._compile_annotation_lines(
                tag="text_annotation",
                annotation=current_annotation,
                indent="  ",
            )
        )
        if normalized_quoted:
            lines.append(
                f"  <quoted_text>{self.escape_render_text(normalized_quoted)}</quoted_text>"
            )
        lines.extend(
            self._compile_annotation_lines(
                tag="quoted_text_annotation",
                annotation=quoted_annotation,
                indent="  ",
            )
        )
        lines.append("</user_input>")
        return "\n".join(lines)

    @staticmethod
    def _build_text_content_part(text: str) -> dict[str, Any]:
        return {
            "type": "text",
            "text": text,
        }

    def _extract_child_tag_text(
        self,
        prompt_tree: PromptBuilder,
        node,
        tag: str,
    ) -> str | None:
        child = self._find_direct_child_tag(node, tag, prompt_tree=prompt_tree)
        if child is None:
            return None
        return self._render_subtree_text(prompt_tree, child, include_root=False)

    def _find_direct_child_tag(
        self,
        node,
        tag: str,
        *,
        prompt_tree: PromptBuilder,
    ):
        for child in self._iter_structured_children(prompt_tree, node):
            if child.meta.get("kind") == "tag_start" and child.meta.get("tag") == tag:
                return child
        return None

    def _iter_descendant_tags(
        self,
        prompt_tree: PromptBuilder,
        node,
        *,
        tag: str,
    ):
        for child in self._iter_structured_children(prompt_tree, node):
            if child.meta.get("kind") == "tag_start" and child.meta.get("tag") == tag:
                yield child
            yield from self._iter_descendant_tags(prompt_tree, child, tag=tag)

    def _iter_structured_children(self, prompt_tree: PromptBuilder, node):
        for child in sorted(node.children, key=prompt_tree._sort_key):
            if child.meta.get("kind") == "tag_end":
                continue
            yield child

    def _prune_empty_prompt_tree(self, prompt_tree: PromptBuilder) -> None:
        self._prune_empty_node(prompt_tree._root_node, keep_node=True)
        if prompt_tree.roots:
            prompt_tree.roots = [
                root for root in prompt_tree.roots if self._prune_empty_node(root)
            ]

    def _prune_empty_node(self, node, *, keep_node: bool = False) -> bool:
        retained_children = []
        for child in node.children:
            if self._prune_empty_node(child):
                retained_children.append(child)
        node.children = retained_children

        kind = node.meta.get("kind")
        if kind == "tag_end":
            return True

        if kind in {"container", "container_root"}:
            return keep_node or bool(node.children)

        if kind == "tag_start":
            has_body = any(
                child.meta.get("kind") != "tag_end" for child in node.children
            )
            return keep_node or has_body

        if node.text:
            return bool(node.text.strip())

        return keep_node

    def _find_slot(
        self,
        slots: list[ContextSlot],
        slot_name: str,
    ) -> ContextSlot | None:
        for slot in slots:
            if slot.name == slot_name:
                return slot
        return None

    def _render_persona_segments(
        self,
        target: NodeRef,
        slot: ContextSlot,
    ) -> bool:
        segments = slot.value
        if not isinstance(segments, dict):
            return False

        persona_root = target.container(meta=self._slot_meta(slot))
        rendered_any = False
        for key, value in segments.items():
            rendered_any |= self._render_generic_value(persona_root, key, value)
        return rendered_any

    def _render_message_history(
        self,
        target: NodeRef,
        messages: list[object],
        *,
        meta: dict[str, Any],
    ) -> bool:
        normalized_messages = [
            item for item in messages if isinstance(item, dict) and item.get("content")
        ]
        if not normalized_messages:
            return False

        container = target.container(meta=meta)
        current_turn: NodeRef | None = None
        rendered_any = False
        for message in normalized_messages:
            role = self._clean_text(message.get("role"))
            content = self._clean_text(message.get("content"))
            if role not in {"user", "assistant"} or not content:
                continue

            if role == "user" or current_turn is None:
                current_turn = container.tag("turn")
            current_turn.tag(role).add(content)
            rendered_any = True

        return rendered_any

    def _render_turn_pairs(
        self,
        target: NodeRef,
        turns: list[object],
        *,
        meta: dict[str, Any],
    ) -> bool:
        container = target.container(meta=meta)
        rendered_any = False
        for turn in turns:
            if not isinstance(turn, dict):
                continue

            user_payload = turn.get("user_message")
            assistant_payload = turn.get("assistant_message")
            user_content = self._extract_message_content(user_payload)
            assistant_content = self._extract_message_content(assistant_payload)

            if not user_content and not assistant_content:
                continue

            turn_ref = container.tag("turn")
            if user_content:
                turn_ref.tag("user").add(user_content)
            if assistant_content:
                turn_ref.tag("assistant").add(assistant_content)
            rendered_any = True

        return rendered_any

    def _render_image_records(
        self,
        target: NodeRef,
        slot: ContextSlot,
    ) -> bool:
        records = self._coerce_list(slot.value)
        if not records:
            return False

        container = target.container(meta=self._slot_meta(slot))
        rendered_any = False
        for record in records:
            ref = self._clean_text(record.get("ref"))
            if not ref:
                continue
            container.tag(
                "image",
                meta={
                    "transport": record.get("transport"),
                    "resolution": record.get("resolution"),
                    "reply_id": record.get("reply_id"),
                    "source": record.get("source"),
                },
            ).add(ref)
            rendered_any = True
        return rendered_any

    def _render_input_annotation_records(
        self,
        target: NodeRef,
        *,
        records: list[dict[str, Any]],
        item_tag: str,
        body_keys: tuple[str, ...],
        ref_resolver: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> bool:
        annotated_records: list[dict[str, Any]] = []
        for record in records:
            annotation = self._extract_input_annotation_fields(record)
            if not annotation:
                continue

            normalized_record = dict(annotation)
            for key in body_keys:
                if key == "ref" and callable(ref_resolver):
                    value = ref_resolver(record)
                else:
                    value = record.get(key)
                if value:
                    normalized_record[key] = value

            for meta_key in ("source", "reply_id", "resolution", "transport"):
                value = record.get(meta_key)
                if value is not None:
                    normalized_record[meta_key] = value
            annotated_records.append(normalized_record)

        if not annotated_records:
            return False

        container = target.container()
        rendered_any = False
        for record in annotated_records:
            item_ref = container.tag(item_tag)
            item_rendered = False
            for key in body_keys:
                item_rendered |= self._render_generic_value(
                    item_ref, key, record.get(key)
                )
            for annotation_key in SUPPORTED_INPUT_ANNOTATION_FIELDS:
                item_rendered |= self._render_generic_value(
                    item_ref,
                    annotation_key,
                    record.get(annotation_key),
                )
            for meta_key in ("source", "reply_id", "resolution", "transport"):
                item_rendered |= self._render_generic_value(
                    item_ref, meta_key, record.get(meta_key)
                )
            rendered_any |= item_rendered
        return rendered_any

    def _render_file_records(
        self,
        target: NodeRef,
        slot: ContextSlot,
    ) -> bool:
        records = self._coerce_list(slot.value)
        if not records:
            return False

        container = target.container(meta=self._slot_meta(slot))
        rendered_any = False
        for record in records:
            ref = self._resolve_file_ref(record)
            name = self._clean_text(record.get("name"))
            if not name and not ref:
                continue
            file_ref = container.tag(
                "file",
                meta={
                    "source": record.get("source"),
                    "reply_id": record.get("reply_id"),
                    "file": record.get("file"),
                    "url": record.get("url"),
                },
            )
            if name:
                file_ref.tag("name").add(name)
            if ref:
                file_ref.tag("ref").add(ref)
            rendered_any = True
        return rendered_any

    def _render_record_items(
        self,
        target: NodeRef,
        slot: ContextSlot,
        *,
        item_tag: str,
        body_keys: tuple[str, ...],
    ) -> bool:
        records = self._coerce_list(slot.value)
        if not records:
            return False

        container = target.container(meta=self._slot_meta(slot))
        rendered_any = False
        for record in records:
            item_meta = {
                key: value for key, value in record.items() if key not in body_keys
            }
            item_ref = container.tag(item_tag, meta=self._clean_meta(item_meta))
            item_rendered = False
            for key in body_keys:
                item_rendered |= self._render_generic_value(
                    item_ref, key, record.get(key)
                )
            for key, value in item_meta.items():
                item_rendered |= self._render_generic_value(item_ref, key, value)
            rendered_any |= item_rendered
        return rendered_any

    def _render_mapping_slot(
        self,
        target: NodeRef,
        tag: str,
        slot: ContextSlot | None,
        *,
        body_keys: tuple[str, ...],
    ) -> bool:
        if slot is None or not isinstance(slot.value, dict):
            return False

        payload = slot.value
        extra_meta = {
            key: value for key, value in payload.items() if key not in body_keys
        }
        node_ref = self._add_parent_tag(
            target,
            tag,
            meta=self._slot_meta(slot, extra_meta),
        )
        rendered_any = False
        for key in body_keys:
            rendered_any |= self._render_generic_value(node_ref, key, payload.get(key))
        return rendered_any

    def _render_record_list(
        self,
        target: NodeRef,
        *,
        parent_tag: str,
        item_tag: str,
        items: list[object],
        body_keys: tuple[str, ...],
        meta: dict[str, Any],
    ) -> bool:
        if not items:
            return False

        parent_ref = self._add_parent_tag(target, parent_tag, meta=meta)
        rendered_any = False
        for item in items:
            item_meta = {
                key: value for key, value in item.items() if key not in body_keys
            }
            item_ref = parent_ref.tag(item_tag, meta=item_meta)
            item_rendered = False
            for key in body_keys:
                item_rendered |= self._render_generic_value(
                    item_ref, key, item.get(key)
                )
            rendered_any |= item_rendered
        return rendered_any

    def _render_prompt_extensions(
        self,
        target: NodeRef,
        slot: ContextSlot,
    ) -> bool:
        if not isinstance(slot.value, dict):
            return False

        items = self._coerce_list(slot.value.get("items"))
        if not items:
            return False

        container = target.container(
            meta=self._slot_meta(
                slot,
                {
                    "format": slot.value.get("format"),
                    "mount": slot.value.get("mount"),
                    "item_count": len(items),
                },
            )
        )
        grouped_items: dict[str, list[dict[str, Any]]] = {}
        plugin_order: list[str] = []
        for item in items:
            plugin_id = self._clean_text(item.get("plugin_id"))
            if not plugin_id:
                continue
            if plugin_id not in grouped_items:
                grouped_items[plugin_id] = []
                plugin_order.append(plugin_id)
            grouped_items[plugin_id].append(item)

        if not grouped_items:
            return False

        rendered_any = False
        used_tags: dict[str, str] = {}
        for plugin_id in plugin_order:
            plugin_tag = self._normalize_extension_plugin_tag(plugin_id)
            existing_owner = used_tags.get(plugin_tag)
            if existing_owner is not None and existing_owner != plugin_id:
                plugin_tag = (
                    f"{plugin_tag}_{sha1(plugin_id.encode('utf-8')).hexdigest()[:8]}"
                )
            used_tags[plugin_tag] = plugin_id

            plugin_ref = container.tag(plugin_tag, meta={"plugin_id": plugin_id})
            plugin_ref.tag("plugin_id").add(plugin_id)
            rendered_plugin = True
            for item in sorted(
                grouped_items[plugin_id],
                key=lambda payload: self._coerce_extension_order(payload.get("order")),
            ):
                entry_ref = plugin_ref.tag(
                    "entry",
                    meta=self._clean_meta(
                        {
                            "value_kind": self._clean_text(item.get("value_kind")),
                            "order": self._coerce_extension_order(item.get("order")),
                            "meta": item.get("meta"),
                        }
                    ),
                )
                entry_rendered = False
                entry_rendered |= self._add_text_tag(
                    entry_ref,
                    "title",
                    self._clean_text(item.get("title")),
                )
                entry_rendered |= self._render_generic_value(
                    entry_ref,
                    "value",
                    item.get("value"),
                )
                rendered_plugin |= entry_rendered
            rendered_any |= rendered_plugin

        return rendered_any

    def _render_tool_inventory(
        self,
        target: NodeRef,
        tools: list[dict[str, Any]],
        *,
        meta: dict[str, Any],
    ) -> bool:
        if not tools:
            return False

        container = target.container(meta=meta)
        rendered_any = False
        for tool in tools:
            name = self._clean_text(tool.get("name"))
            description = self._clean_text(tool.get("description"))
            if not name:
                continue
            tool_ref = container.tag(
                "tool",
                meta={
                    "parameters": tool.get("parameters"),
                    "schema": tool.get("schema"),
                    "active": tool.get("active"),
                    "handler_module_path": tool.get("handler_module_path"),
                },
            )
            tool_ref.tag("name").add(name)
            if description:
                tool_ref.tag("description").add(description)
            rendered_any = True
        return rendered_any

    def _render_handoff_inventory(
        self,
        target: NodeRef,
        tools: list[dict[str, Any]],
        *,
        meta: dict[str, Any],
    ) -> bool:
        if not tools:
            return False

        container = target.container(meta=meta)
        rendered_any = False
        for tool in tools:
            name = self._clean_text(tool.get("name"))
            description = self._clean_text(tool.get("description"))
            if not name:
                continue
            tool_ref = container.tag(
                "tool",
                meta={
                    "parameters": tool.get("parameters"),
                },
            )
            tool_ref.tag("name").add(name)
            if description:
                tool_ref.tag("description").add(description)
            rendered_any = True
        return rendered_any

    def _render_generic_value(
        self,
        target: NodeRef,
        tag: str,
        value: Any,
    ) -> bool:
        clean_tag = self._normalize_tag(tag)
        if value is None:
            return False

        if isinstance(value, str):
            text = self._clean_text(value)
            if not text:
                return False
            target.tag(clean_tag).add(text)
            return True

        if isinstance(value, bool):
            target.tag(clean_tag).add("true" if value else "false")
            return True

        if isinstance(value, (int, float)):
            target.tag(clean_tag).add(str(value))
            return True

        if isinstance(value, dict):
            tag_ref = target.tag(clean_tag)
            rendered_any = False
            for child_key, child_value in value.items():
                rendered_any |= self._render_generic_value(
                    tag_ref, child_key, child_value
                )
            return rendered_any

        if isinstance(value, list):
            tag_ref = target.tag(clean_tag)
            rendered_any = False
            for item in value:
                rendered_any |= self._render_generic_value(tag_ref, "item", item)
            return rendered_any

        target.tag(clean_tag).add(str(value))
        return True

    def _extract_name_whitelist(
        self,
        slot: ContextSlot | None,
    ) -> set[str] | None:
        if slot is None:
            return None
        value = slot.value
        if value is None:
            return None
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    def _filter_named_items(
        self,
        items: list[dict[str, Any]],
        whitelist: set[str] | None,
    ) -> list[dict[str, Any]]:
        if whitelist is None:
            return list(items)
        if not whitelist:
            return []
        return [
            item
            for item in items
            if isinstance(item.get("name"), str) and item["name"] in whitelist
        ]

    def _add_parent_tag(
        self,
        target: NodeRef,
        tag: str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> NodeRef:
        return target.tag(self._normalize_tag(tag), meta=self._clean_meta(meta))

    def _add_text_tag(
        self,
        target: NodeRef,
        tag: str,
        text: str | None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        clean_text = self._clean_text(text)
        if not clean_text:
            return False
        target.tag(self._normalize_tag(tag), meta=self._clean_meta(meta)).add(
            clean_text
        )
        return True

    def _slot_meta(
        self,
        slot: ContextSlot,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = {
            "slot_name": slot.name,
            "slot_category": slot.category,
            "slot_source": slot.source,
            "llm_exposure": slot.llm_exposure,
            "placement": slot.placement,
            "render_mode": slot.render_mode,
            "priority": slot.priority,
            **slot.meta,
        }
        if extra:
            base.update(extra)
        return self._clean_meta(base)

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _clean_meta(self, meta: dict[str, Any] | None) -> dict[str, Any]:
        if not meta:
            return {}
        return {key: value for key, value in meta.items() if value is not None}

    @staticmethod
    def _coerce_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _resolve_file_ref(self, record: dict[str, Any]) -> str | None:
        url = self._clean_text(record.get("url"))
        if url:
            return url
        return self._clean_text(record.get("file"))

    def _extract_message_content(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        return self._clean_text(payload.get("content"))

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        return tag.replace(".", "_").replace(" ", "_")

    @staticmethod
    def _normalize_extension_plugin_tag(plugin_id: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", plugin_id).strip("._-")
        normalized = normalized.replace(".", "_").replace("-", "_")
        if not normalized:
            normalized = "plugin"
        if normalized[0].isdigit():
            normalized = f"plugin_{normalized}"
        return normalized

    @staticmethod
    def _coerce_extension_order(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 100

    @staticmethod
    def _slot_to_child_tag(slot_name: str, group: str) -> str:
        """Convert a slot name into a child tag name for a logical group."""
        child_tag = (
            slot_name[len(group) + 1 :]
            if slot_name.startswith(f"{group}.")
            else slot_name
        )
        return child_tag.replace(".", "_") or group

    @staticmethod
    def _stringify_slot_value(slot: ContextSlot) -> str | None:
        """Convert a slot value into a minimal text form."""
        value = slot.value
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if value is None:
            return None
        return str(value)

    def _render_annotation_mapping(
        self,
        target: NodeRef,
        annotation: dict[str, str],
    ) -> bool:
        clean_annotation = self._clean_annotation_mapping(annotation)
        if not clean_annotation:
            return False

        annotation_ref = target.tag("annotation")
        rendered_any = False
        for annotation_key in SUPPORTED_INPUT_ANNOTATION_FIELDS:
            rendered_any |= self._render_generic_value(
                annotation_ref,
                annotation_key,
                clean_annotation.get(annotation_key),
            )
        return rendered_any

    def _extract_annotation_from_node(
        self,
        prompt_tree: PromptBuilder,
        node,
    ) -> dict[str, str]:
        if node is None:
            return {}

        annotation_node = self._find_direct_child_tag(
            node,
            "annotation",
            prompt_tree=prompt_tree,
        )
        if annotation_node is None:
            return {}

        return self._clean_annotation_mapping(
            {
                annotation_key: self._extract_child_tag_text(
                    prompt_tree,
                    annotation_node,
                    annotation_key,
                )
                for annotation_key in SUPPORTED_INPUT_ANNOTATION_FIELDS
            }
        )

    def _extract_input_annotation_fields(
        self,
        payload: dict[str, Any] | None,
    ) -> dict[str, str]:
        if not isinstance(payload, dict):
            return {}

        return self._clean_annotation_mapping(
            {
                annotation_key: payload.get(annotation_key)
                for annotation_key in SUPPORTED_INPUT_ANNOTATION_FIELDS
            }
        )

    def _clean_annotation_mapping(
        self,
        annotation: dict[str, Any] | None,
    ) -> dict[str, str]:
        if not annotation:
            return {}

        clean_annotation: dict[str, str] = {}
        for annotation_key in SUPPORTED_INPUT_ANNOTATION_FIELDS:
            value = self._clean_text(annotation.get(annotation_key))
            if value:
                clean_annotation[annotation_key] = value
        return clean_annotation

    def _compile_annotation_lines(
        self,
        *,
        tag: str,
        annotation: dict[str, str],
        indent: str,
    ) -> list[str]:
        if not annotation:
            return []

        lines = [f"{indent}<{tag}>"]
        for annotation_key in SUPPORTED_INPUT_ANNOTATION_FIELDS:
            value = self._clean_text(annotation.get(annotation_key))
            if value:
                lines.append(
                    f"{indent}  <{annotation_key}>{self.escape_render_text(value)}</{annotation_key}>"
                )
        lines.append(f"{indent}</{tag}>")
        return lines

    def _build_render_metadata(
        self,
        *,
        prompt_tree: PromptBuilder,
        rendered_groups: list[str],
        rendered_slots: list[str],
        compiled_message_count: int = 0,
        compiled_tool_count: int = 0,
    ) -> dict[str, Any]:
        """Build shared renderer metadata for render results."""
        return {
            "renderer": self.get_name(),
            "rendered_groups": list(rendered_groups),
            "rendered_slots": list(rendered_slots),
            "tree_root": prompt_tree._root_node.meta.get("tag", self.get_root_tag()),
            "compiled_message_count": compiled_message_count,
            "compiled_tool_count": compiled_tool_count,
            "debug_prompt_tree": prompt_tree.build(),
        }
