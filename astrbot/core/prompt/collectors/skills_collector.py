"""
Skills context collector for prompt context packing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.db import BaseDatabase
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.skills.skill_manager import SkillInfo, SkillManager
from astrbot.core.star.context import Context
from astrbot.core.star.star import star_registry
from astrbot.core.workspace import (
    default_workspace_root,
    resolve_workspace_root_for_umo,
)

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface

if TYPE_CHECKING:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig


class SkillsCollector(ContextCollectorInterface):
    """Collect active skills as structured inventory for later rendering."""

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
        del provider_request

        runtime = self._resolve_runtime(config)

        try:
            skills = self._filter_skills_for_current_config(
                self._load_active_skills(runtime),
                config.provider_settings,
            )
            workspace_skills = await self._load_workspace_skills(
                event,
                plugin_context,
                runtime,
            )
            if workspace_skills:
                skills_by_name = {skill.name: skill for skill in skills}
                for skill in workspace_skills:
                    skills_by_name[skill.name] = skill
                skills = [skills_by_name[name] for name in sorted(skills_by_name)]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to collect active skills: runtime=%s error=%s",
                runtime,
                exc,
                exc_info=True,
            )
            return []

        if not skills:
            return []

        return [self._build_skills_slot(runtime, skills)]

    def _resolve_runtime(self, config: MainAgentBuildConfig) -> str:
        runtime = getattr(config, "computer_use_runtime", None)
        if isinstance(runtime, str) and runtime.strip():
            return runtime.strip()
        return "local"

    def _load_active_skills(self, runtime: str) -> list[SkillInfo]:
        manager = SkillManager()
        return manager.list_skills(active_only=True, runtime=runtime)

    def _filter_skills_for_current_config(
        self,
        skills: list[SkillInfo],
        provider_settings: object,
    ) -> list[SkillInfo]:
        settings = provider_settings if isinstance(provider_settings, dict) else {}
        plugin_set = settings.get("plugin_set", ["*"])
        allowed_plugins = (
            None
            if not isinstance(plugin_set, list) or "*" in plugin_set
            else {str(name) for name in plugin_set}
        )
        plugin_by_root_dir = {
            metadata.root_dir_name: metadata
            for metadata in star_registry
            if metadata.root_dir_name
        }
        filtered: list[SkillInfo] = []
        for skill in skills:
            if skill.source_type != "plugin":
                filtered.append(skill)
                continue
            plugin = plugin_by_root_dir.get(skill.plugin_name)
            if not plugin or not plugin.activated:
                continue
            if plugin.reserved or allowed_plugins is None:
                filtered.append(skill)
                continue
            if plugin.name is not None and plugin.name in allowed_plugins:
                filtered.append(skill)
        return filtered

    async def _load_workspace_skills(
        self,
        event: AstrMessageEvent,
        plugin_context: Context,
        runtime: str,
    ) -> list[SkillInfo]:
        if runtime != "local" or self._event_has_group_context(event):
            return []
        workspace_root = default_workspace_root(event.unified_msg_origin)
        db = getattr(plugin_context, "_db", None)
        if isinstance(db, BaseDatabase):
            try:
                workspace_root = await resolve_workspace_root_for_umo(
                    event.unified_msg_origin,
                    db,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Failed to resolve workspace skills root for %s: %s",
                    event.unified_msg_origin,
                    exc,
                )
        return SkillManager().list_workspace_skills(workspace_root)

    @staticmethod
    def _event_has_group_context(event: AstrMessageEvent) -> bool:
        get_group_id = getattr(event, "get_group_id", None)
        if not callable(get_group_id):
            return False
        try:
            return bool(get_group_id())
        except Exception:
            return False

    def _build_skills_slot(
        self,
        runtime: str,
        skills: list[SkillInfo],
    ) -> ContextSlot:
        serialized_skills = [self._serialize_skill(skill) for skill in skills]
        return ContextSlot(
            name="capability.skills_prompt",
            value={
                "format": "skills_inventory_v1",
                "runtime": runtime,
                "skill_count": len(serialized_skills),
                "skills": serialized_skills,
            },
            category="tools",
            source="skill_manager",
            meta={
                "format": "skills_inventory_v1",
                "runtime": runtime,
                "skill_count": len(serialized_skills),
            },
        )

    def _serialize_skill(self, skill: SkillInfo) -> dict[str, object]:
        return {
            "name": skill.name,
            "description": skill.description,
            "path": skill.path,
            "source_type": skill.source_type,
            "source_label": skill.source_label,
            "active": skill.active,
            "local_exists": skill.local_exists,
            "sandbox_exists": skill.sandbox_exists,
        }
