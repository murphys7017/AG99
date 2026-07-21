"""
Policy context collector for prompt context packing.
"""

from __future__ import annotations

import platform
from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.prompt.resources import (
    LLM_SAFETY_MODE_SYSTEM_PROMPT,
    SANDBOX_MODE_PROMPT,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class PolicyCollector(ContextCollectorInterface):
    """Collect active request policy prompts without mutating the request."""

    @property
    def lifecycle(self) -> str:
        return "static"

    async def collect(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        config: MainAgentBuildConfig,
        provider_request: ProviderRequest | None = None,
    ) -> list[ContextSlot]:
        del event, plugin_context, provider_request

        slots: list[ContextSlot] = []

        try:
            safety_slot = self._build_safety_prompt_slot(config)
            if safety_slot is not None:
                slots.append(safety_slot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect policy safety prompt: %s", exc, exc_info=True
            )

        try:
            sandbox_slot = self._build_sandbox_prompt_slot(config)
            if sandbox_slot is not None:
                slots.append(sandbox_slot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect policy sandbox prompt: %s", exc, exc_info=True
            )

        try:
            local_env_slot = self._build_local_env_prompt_slot(config)
            if local_env_slot is not None:
                slots.append(local_env_slot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect policy local-env prompt: %s",
                exc,
                exc_info=True,
            )

        return slots

    def _build_safety_prompt_slot(
        self,
        config: MainAgentBuildConfig,
    ) -> ContextSlot | None:
        if not config.llm_safety_mode:
            return None
        if config.safety_mode_strategy != "system_prompt":
            return None

        return ContextSlot(
            name="policy.safety_prompt",
            value=LLM_SAFETY_MODE_SYSTEM_PROMPT,
            category="system",
            source="main_agent_policy",
            meta={
                "enabled_by_config": True,
                "strategy": config.safety_mode_strategy,
            },
        )

    def _build_sandbox_prompt_slot(
        self,
        config: MainAgentBuildConfig,
    ) -> ContextSlot | None:
        if config.computer_use_runtime != "sandbox":
            return None

        prompt = SANDBOX_MODE_PROMPT
        if config.sandbox_cfg.get("booter", "shipyard_neo") == "shipyard_neo":
            prompt += (
                "\n[Shipyard Neo File Path Rule]\n"
                "When using sandbox filesystem tools (upload/download/read/write/list/delete), "
                "always pass paths relative to the sandbox workspace root. "
                "Example: use `baidu_homepage.png` instead of `/workspace/baidu_homepage.png`.\n"
            )
            prompt += (
                "\n[Neo Skill Lifecycle Workflow]\n"
                "When user asks to create/update a reusable skill in Neo mode, use lifecycle tools instead of directly writing local skill folders.\n"
                "Preferred sequence:\n"
                "1) Use `astrbot_create_skill_payload` to store canonical payload content and get `payload_ref`.\n"
                "2) Use `astrbot_create_skill_candidate` with `skill_key` + `source_execution_ids` (and optional `payload_ref`) to create a candidate.\n"
                "3) Use `astrbot_promote_skill_candidate` to release: `stage=canary` for trial; `stage=stable` for production.\n"
                "For stable release, set `sync_to_local=true` to sync `payload.skill_markdown` into local `SKILL.md`.\n"
                "Do not treat ad-hoc generated files as reusable Neo skills unless they are captured via payload/candidate/release.\n"
                "To update an existing skill, create a new payload/candidate and promote a new release version; avoid patching old local folders directly.\n"
            )
        elif config.sandbox_cfg.get("booter") == "cua":
            prompt += (
                "\n[CUA Desktop Control]\n"
                "Use `astrbot_execute_shell` with `background=true` to launch GUI apps. "
                'Use Firefox for browser tasks, for example `firefox "https://example.com"`. '
                "After each visible step, call `astrbot_cua_screenshot` with "
                "`send_to_user=true` and `return_image_to_llm=true` so the user can "
                "monitor progress. When typing, inspect the screenshot first and confirm "
                "the target field is focused and empty or safe to append to. Use "
                "`astrbot_cua_mouse_click` for coordinates and `astrbot_cua_keyboard_type` "
                "for text input; use text=`\\n` for Enter.\n"
            )

        return ContextSlot(
            name="policy.sandbox_prompt",
            value=prompt,
            category="system",
            source="main_agent_policy",
            meta={
                "enabled_by_config": True,
                "runtime": config.computer_use_runtime,
                "booter": config.sandbox_cfg.get("booter", "shipyard_neo"),
            },
        )

    def _build_local_env_prompt_slot(
        self,
        config: MainAgentBuildConfig,
    ) -> ContextSlot | None:
        if config.computer_use_runtime != "local":
            return None

        return ContextSlot(
            name="policy.local_env_prompt",
            value=self._build_local_mode_prompt(),
            category="system",
            source="main_agent_policy",
            meta={
                "enabled_by_config": True,
                "runtime": config.computer_use_runtime,
            },
        )

    def _build_local_mode_prompt(self) -> str:
        system_name = platform.system() or "Unknown"
        shell_hint = (
            "The runtime shell is Windows Command Prompt (cmd.exe). "
            "Use cmd-compatible commands and do not assume Unix commands like "
            "cat/ls/grep are available."
            if system_name.lower() == "windows"
            else "The runtime shell is Unix-like. Use POSIX-compatible shell commands."
        )
        return (
            "You have access to the host local environment and can execute shell "
            "commands and Python code. "
            f"Current operating system: {system_name}. "
            f"{shell_hint}"
        )
