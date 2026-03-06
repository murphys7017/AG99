"""Context builder (Phase 1.1 MVP)."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional, Protocol

from ..types import AgentRequest, RoutingPlan
from .types import ContextPack, ContextSlot, ProviderResult, SlotSpec
from .providers import (
    ContextProvider,
    CurrentInputProvider,
    RecentObsProvider,
    PlanMetaProvider,
    SessionStateProvider,
    PersonaProvider,
    MemoryProvider,
    KnowledgeProvider,
    ToolResultsProvider,
    RuntimePolicyProvider,
)


class ContextBuilder(Protocol):
    """可插拔上下文构建器接口。"""

    async def build(self, req: AgentRequest, plan: RoutingPlan) -> ContextPack:
        """Build execution context for pools/subagents (not pool selector input)."""
        ...


class SlotContextBuilder:
    """Context builder for execution context (not pool selector input)."""

    def __init__(
        self,
        *,
        slot_specs: Optional[Mapping[str, SlotSpec]] = None,
        providers: Optional[Mapping[str, ContextProvider]] = None,
        runtime_priority_overrides: Optional[Mapping[str, int]] = None,
    ) -> None:
        self._slot_specs = dict(slot_specs or _default_slot_specs())
        self._providers = dict(providers or _default_providers())
        self._runtime_priority_overrides = dict(runtime_priority_overrides or {})

    async def build(self, req: AgentRequest, plan: RoutingPlan) -> ContextPack:
        """Build ContextPack based on RoutingPlan.required_context.

        Note: Pool selector input is a separate lightweight view.
        """
        requested_by_plan = list(plan.required_context or ())
        auto_injected = _resolve_auto_injected_slots(requested_by_plan, self._slot_specs)
        requested_effective = _merge_requested_slots(requested_by_plan, auto_injected)
        priorities, priority_sources = _resolve_priorities(
            requested_effective,
            self._slot_specs,
            plan_meta_priorities=plan.meta.get("context_priorities") if isinstance(plan.meta, dict) else None,
            runtime_overrides=self._runtime_priority_overrides,
        )

        slots: Dict[str, ContextSlot] = {}
        errors: list[dict] = []
        build_notes: list[str] = []

        for slot_name in requested_effective:
            spec = self._slot_specs.get(slot_name)
            provider_name = spec.provider if spec else slot_name
            provider = self._providers.get(provider_name)
            priority = priorities.get(slot_name, 0)

            if provider is None:
                slots[slot_name] = ContextSlot(
                    name=slot_name,
                    value=None,
                    priority=priority,
                    source=provider_name,
                    status="missing",
                    meta={"reason": "provider_not_found"},
                )
                build_notes.append(f"missing_provider:{slot_name}")
                continue

            try:
                result = await provider.provide(req, plan)
                slot = _slot_from_result(slot_name, provider_name, priority, result)
                slots[slot_name] = slot
                if slot.status in ("missing", "stub"):
                    build_notes.append(f"slot_{slot.status}:{slot_name}")
                if slot.status == "error":
                    errors.append({"slot": slot_name, "error": slot.meta.get("error")})
            except Exception as exc:
                slots[slot_name] = ContextSlot(
                    name=slot_name,
                    value=None,
                    priority=priority,
                    source=provider_name,
                    status="error",
                    meta={"error": str(exc)},
                )
                errors.append({"slot": slot_name, "error": str(exc)})

        recent_obs = _extract_recent_obs(slots)
        slots_hit = {name: slot.status == "ok" for name, slot in slots.items()}

        provided = [name for name, slot in slots.items() if slot.status == "ok"]
        missing = [name for name, slot in slots.items() if slot.status in ("missing", "stub")]
        meta = {
            "requested_by_plan": list(requested_by_plan),
            "auto_injected": list(auto_injected),
            "requested_effective": list(requested_effective),
            "provided": provided,
            "provided_effective": list(provided),
            "missing": missing,
            "errors": list(errors),
            "priorities": dict(priorities),
            "priority_sources": dict(priority_sources),
            "build_notes": list(build_notes),
        }
        return ContextPack(slots=slots, meta=meta, recent_obs=recent_obs, slots_hit=slots_hit)


class RecentObsContextBuilder(SlotContextBuilder):
    """Backward-compatible alias for the new slot-based builder."""

    pass


def _default_slot_specs() -> Dict[str, SlotSpec]:
    return {
        "current_input": SlotSpec(
            name="current_input",
            provider="current_input",
            default_priority=100,
            required_by_default=True,
        ),
        "recent_obs": SlotSpec(
            name="recent_obs",
            provider="recent_obs",
            default_priority=90,
        ),
        "plan_meta": SlotSpec(
            name="plan_meta",
            provider="plan_meta",
            default_priority=80,
            required_by_default=True,
        ),
        "session_state": SlotSpec(
            name="session_state",
            provider="session_state",
            default_priority=70,
        ),
        "runtime_policy": SlotSpec(
            name="runtime_policy",
            provider="runtime_policy",
            default_priority=60,
        ),
        "persona": SlotSpec(
            name="persona",
            provider="persona",
            default_priority=55,
        ),
        "memory": SlotSpec(
            name="memory",
            provider="memory",
            default_priority=50,
        ),
        "knowledge": SlotSpec(
            name="knowledge",
            provider="knowledge",
            default_priority=40,
        ),
        "tool_results": SlotSpec(
            name="tool_results",
            provider="tool_results",
            default_priority=35,
        ),
        "recent_summary": SlotSpec(
            name="recent_summary",
            provider="recent_summary",
            default_priority=30,
            enabled=False,
        ),
    }


def _default_providers() -> Dict[str, ContextProvider]:
    return {
        "current_input": CurrentInputProvider(),
        "recent_obs": RecentObsProvider(),
        "plan_meta": PlanMetaProvider(),
        "session_state": SessionStateProvider(),
        "persona": PersonaProvider(),
        "memory": MemoryProvider(),
        "knowledge": KnowledgeProvider(),
        "tool_results": ToolResultsProvider(),
        "runtime_policy": RuntimePolicyProvider(),
    }


def _resolve_auto_injected_slots(requested_by_plan: Iterable[str], specs: Mapping[str, SlotSpec]) -> list[str]:
    requested_set = {name for name in requested_by_plan}
    auto_injected: list[str] = []
    for spec in specs.values():
        if spec.required_by_default and spec.enabled and spec.name not in requested_set:
            auto_injected.append(spec.name)
    return auto_injected


def _merge_requested_slots(requested_by_plan: Iterable[str], auto_injected: Iterable[str]) -> list[str]:
    merged: list[str] = []
    for name in requested_by_plan:
        if name not in merged:
            merged.append(name)
    for name in auto_injected:
        if name not in merged:
            merged.append(name)
    return merged


def _resolve_priorities(
    requested: Iterable[str],
    specs: Mapping[str, SlotSpec],
    *,
    plan_meta_priorities: Optional[Mapping[str, int]],
    runtime_overrides: Mapping[str, int],
) -> tuple[Dict[str, int], Dict[str, str]]:
    priorities: Dict[str, int] = {}
    sources: Dict[str, str] = {}
    for name in requested:
        spec = specs.get(name)
        priorities[name] = spec.default_priority if spec else 0
        sources[name] = "default"

    if isinstance(plan_meta_priorities, Mapping):
        for name, value in plan_meta_priorities.items():
            if name in priorities and isinstance(value, int):
                priorities[name] = value
                sources[name] = "plan_override"

    for name, value in runtime_overrides.items():
        if name in priorities and isinstance(value, int):
            priorities[name] = value
            sources[name] = "runtime_override"

    return priorities, sources


def _slot_from_result(
    slot_name: str,
    provider_name: str,
    priority: int,
    result: ProviderResult,
) -> ContextSlot:
    status = result.status if result.status else "ok"
    meta = dict(result.meta or {})
    if result.error:
        meta["error"] = result.error
        status = "error"
    return ContextSlot(
        name=slot_name,
        value=result.value,
        priority=priority,
        source=provider_name,
        status=status,
        meta=meta,
    )


def _extract_recent_obs(slots: Mapping[str, ContextSlot]) -> list:
    slot = slots.get("recent_obs")
    if slot and slot.status == "ok" and isinstance(slot.value, list):
        return list(slot.value)
    return []
