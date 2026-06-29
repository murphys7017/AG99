"""
Skills context collector for prompt context packing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.skills.skill_manager import SkillInfo, SkillManager
from astrbot.core.star.context import Context

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
        del event, plugin_context, provider_request

        runtime = self._resolve_runtime(config)

        try:
            skills = self._load_active_skills(runtime)
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
