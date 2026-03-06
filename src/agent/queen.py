"""AgentQueen：Agent Phase 0 编排核心。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, cast

from loguru import logger

from ..schemas.observation import (
    Actor,
    MessagePayload,
    Observation,
    ObservationType,
    SourceKind,
)
from .context import (
    ContextBuilder, 
    RecentObsContextBuilder, 
    SlotContextBuilder,
    load_catalog,
    load_profiles,
    load_presets,
    validate_profiles,
    ContextCatalog,
    ContextPresetsCollection,
)
from .context.types import ContextSlot
from .planner import HybridPoolSelector, LLMPoolSelector, PoolSelector, RulePoolSelector
from .planner.validator import normalize_routing_plan
from .planner.types import build_pool_selector_input_view
from .pools import AgentPoolRouter, Aggregator, ChatPool, DraftAggregator, Pool, PoolRouter
from .registry import AgentConfigRegistry
from .speaker import AgentSpeaker, Speaker
from .types import AgentOutcome, AgentRequest, ContextPack, RoutingPlan

# Deprecated alias: 旧代码可能从 queen 导入 Plan
Plan = RoutingPlan


class AgentQueen:
    """
    Agent 总编排器（Phase 0）。

    固定流程：
    pool_selector -> context_builder -> pool_router.pick -> pool.run -> aggregator -> speaker
    """

    def __init__(
        self,
        *,
        pool_selector: Optional[PoolSelector] = None,
        planner: Optional[PoolSelector] = None,
        context_builder: Optional[ContextBuilder] = None,
        pool_router: Optional[PoolRouter] = None,
        aggregator: Optional[Aggregator] = None,
        speaker: Optional[Speaker] = None,
        registry: Optional[AgentConfigRegistry] = None,
        enable_catalog_loading: bool = True,
    ) -> None:
        self.registry = registry or AgentConfigRegistry()
        self._config = self.registry.load()
        self.pool_selector: PoolSelector = pool_selector or planner or self._build_pool_selector()
        self.planner: PoolSelector = self.pool_selector
        self.context_builder: ContextBuilder = context_builder or SlotContextBuilder()
        self.pool_router: PoolRouter = pool_router or AgentPoolRouter()
        self.aggregator: Aggregator = aggregator or DraftAggregator()
        self.speaker: Speaker = speaker or AgentSpeaker()
        self._builtin_chat_pool = ChatPool()
        
        # Phase 1.1: 加载 catalog & profiles（可选，不影响主流程）
        self._catalog: Optional[ContextCatalog] = None
        self._presets: Optional[ContextPresetsCollection] = None
        self._profiles: Dict[str, Any] = {}
        if enable_catalog_loading:
            self._load_catalog_and_profiles()

    def _load_catalog_and_profiles(self) -> None:
        """加载 catalog 和 profiles（Phase 1.1 最小集成）。"""
        try:
            self._catalog = load_catalog("config/context_catalog.yaml")
            logger.debug(
                f"AgentQueen loaded context catalog: {len(self._catalog.items)} items"
            )
        except Exception as e:
            logger.warning(f"AgentQueen catalog loading failed (non-blocking): {e}")

        try:
            self._presets = load_presets("config/context_presets.yaml")
            logger.debug(
                f"AgentQueen loaded context presets: {len(self._presets.presets)} presets"
            )
        except Exception as e:
            logger.warning(f"AgentQueen presets loading failed (non-blocking): {e}")
        
        try:
            self._profiles = load_profiles("config/agent/prompt_profiles")
            logger.debug(
                f"AgentQueen loaded prompt profiles: {len(self._profiles)} profiles"
            )

            if self._catalog is not None:
                validate_profiles(self._profiles, self._catalog, presets=self._presets)
                logger.debug("AgentQueen prompt profile validation passed")
            
            # 打印 chat.single_pass profile 摘要（验证用）
            if "chat.single_pass" in self._profiles:
                profile = self._profiles["chat.single_pass"]
                logger.debug(
                    f"Profile 'chat.single_pass' loaded: "
                    f"required={len(profile.include.required_items)}, "
                    f"optional={len(profile.include.optional_items)}, "
                    f"max_tokens={profile.budget.max_tokens}"
                )
        except Exception as e:
            logger.warning(f"AgentQueen profiles loading failed (non-blocking): {e}")

    def _build_pool_selector(self) -> PoolSelector:
        planner_cfg = self.registry.get_pool_selector_config()
        kind = str(planner_cfg.get("kind", "rule")).lower()
        if kind in {"hybrid", "hybrid_stub"}:
            return HybridPoolSelector(config=planner_cfg)
        if kind in {"llm", "llm_stub"}:
            return LLMPoolSelector(config=planner_cfg)
        return RulePoolSelector()

    def _build_planner(self) -> PoolSelector:
        """Deprecated alias: _build_planner() -> _build_pool_selector()."""
        return self._build_pool_selector()

    async def handle(self, req: AgentRequest) -> AgentOutcome:
        """执行 Agent 主流程并返回可回灌 Observation。"""
        started = time.perf_counter()
        trace: Dict[str, Any] = {
            "pool_selector_input_summary": {},
            "pool_selector_summary": {},
            "context_build_summary": {},
            "pool": {},
            "aggregation": {},
            "speaker": {},
            "fallback_triggered": False,
        }
        errors: list[str] = []

        plan = await self._safe_select(req, trace, errors)
        ctx = await self._safe_context(req, plan, trace, errors)
        pool = self._safe_pick_pool(req, plan, trace, errors)
        raw = await self._safe_pool_run(req, plan, ctx, pool, trace, errors)
        final_text = await self._safe_aggregate(req, plan, ctx, raw, trace, errors)
        out_obs = self._safe_speak(req, final_text, plan, pool, trace, errors)

        fallback_triggered = bool(errors) or bool(trace.get("pool", {}).get("fallback"))
        trace["fallback_triggered"] = fallback_triggered
        if errors:
            trace["error"] = "; ".join(errors)
        trace["pool_selector_kind"] = str(plan.meta.get("pool_selector_kind", "rule"))
        trace["task_type"] = plan.task_type
        trace["pool_id"] = pool.pool_id
        trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        out_obs.metadata = dict(out_obs.metadata or {})
        out_obs.metadata["fallback"] = fallback_triggered

        return AgentOutcome(
            emit=[out_obs],
            trace=trace,
            error=trace.get("error"),
        )

    async def _safe_select(
        self,
        req: AgentRequest,
        trace: Dict[str, Any],
        errors: list[str],
    ) -> RoutingPlan:
        try:
            selector_input_view = build_pool_selector_input_view(req)
            trace["pool_selector_input_summary"] = {
                "current_input_len": len(selector_input_view.current_input_text or ""),
                "recent_obs_count": selector_input_view.meta.get("recent_obs_count"),
                "recent_obs_preview_count": len(selector_input_view.recent_obs_view or []),
                "gate_hint_present": bool(selector_input_view.gate_hint_view),
            }
            plan = await self.pool_selector.select(req, view=selector_input_view)
            plan = normalize_routing_plan(plan, pool_selector_kind=getattr(self.pool_selector, "kind", "unknown"))
            selector_summary = {
                "pool_selector_kind": plan.meta.get("pool_selector_kind"),
                "selector_stage": plan.meta.get("selector_stage"),
                "final_routing_source": plan.meta.get("final_routing_source"),
                "task_type": plan.task_type,
                "pool_id": plan.pool_id,
                "rule_guess": plan.meta.get("rule_guess"),
                "selector_llm_called": plan.meta.get("selector_llm_called"),
                "selector_llm_parse_ok": plan.meta.get("selector_llm_parse_ok"),
                "small_llm_called": plan.meta.get("small_llm_called"),
                "big_llm_called": plan.meta.get("big_llm_called"),
                "escalated_to_big": plan.meta.get("escalated_to_big"),
                "small_gate_decision": plan.meta.get("small_gate_decision"),
                "small_gate_reason": plan.meta.get("small_gate_reason"),
                "fallback_reason": plan.meta.get("fallback_reason"),
                "confidence": plan.meta.get("confidence"),
                "reason": plan.meta.get("reason"),
            }
            trace["pool_selector_summary"] = selector_summary
            logger.debug(
                "Agent pool selector summary: kind=%s source=%s task=%s pool=%s",
                selector_summary.get("pool_selector_kind"),
                selector_summary.get("final_routing_source"),
                selector_summary.get("task_type"),
                selector_summary.get("pool_id"),
            )
            return plan
        except Exception as exc:
            logger.exception(f"Agent pool selector failed: {exc}")
            errors.append(f"pool_selector:{exc}")
            fallback = normalize_routing_plan(
                RoutingPlan(
                    task_type="chat",
                    pool_id="chat",
                    required_context=("recent_obs",),
                    meta={
                        "strategy": "single_pass",
                        "complexity": "low",
                        "confidence": 0.3,
                        "reason": "pool_selector_exception_fallback",
                    },
                ),
                pool_selector_kind="rule_fallback",
            )
            trace["pool_selector_summary"] = {
                "pool_selector_kind": fallback.meta.get("pool_selector_kind"),
                "selector_stage": fallback.meta.get("selector_stage"),
                "final_routing_source": fallback.meta.get("final_routing_source"),
                "task_type": fallback.task_type,
                "pool_id": fallback.pool_id,
                "fallback": True,
                "error": str(exc),
            }
            return fallback

    async def _safe_context(
        self,
        req: AgentRequest,
        plan: RoutingPlan,
        trace: Dict[str, Any],
        errors: list[str],
    ) -> ContextPack:
        try:
            ctx = await self.context_builder.build(req, plan)
            context_meta = dict(ctx.meta or {})
            context_summary = _build_context_summary(plan, ctx, context_meta)
            trace["context_build_summary"] = context_summary
            logger.debug(
                "Agent context summary: requested=%s auto=%s effective=%s missing=%s errors=%s",
                context_summary.get("requested_by_plan"),
                context_summary.get("auto_injected"),
                context_summary.get("requested_effective"),
                context_summary.get("missing"),
                context_summary.get("errors_count"),
            )
            return ctx
        except Exception as exc:
            logger.exception(f"Agent context builder failed: {exc}")
            errors.append(f"context:{exc}")
            recent_obs = list(req.session_state.recent_obs or [])
            slots = {
                "recent_obs": ContextSlot(
                    name="recent_obs",
                    value=recent_obs,
                    priority=90,
                    source="fallback",
                    status="ok" if recent_obs else "missing",
                    meta={"fallback": True},
                )
            }
            ctx = ContextPack(
                slots=slots,
                recent_obs=recent_obs,
                slots_hit={"recent_obs": len(recent_obs) > 0},
                meta={
                    "fallback": True,
                    "requested_by_plan": list(plan.required_context),
                    "auto_injected": [],
                    "requested_effective": list(plan.required_context),
                    "provided": ["recent_obs"] if recent_obs else [],
                    "provided_effective": ["recent_obs"] if recent_obs else [],
                    "missing": ["recent_obs"] if not recent_obs else [],
                    "errors": [{"slot": "recent_obs", "error": str(exc)}],
                    "priorities": {"recent_obs": 90},
                    "priority_sources": {"recent_obs": "default"},
                },
            )
            context_summary = _build_context_summary(plan, ctx, ctx.meta)
            context_summary["fallback"] = True
            context_summary["error"] = str(exc)
            trace["context_build_summary"] = context_summary
            return ctx


    def _safe_pick_pool(
        self,
        req: AgentRequest,
        plan: RoutingPlan,
        trace: Dict[str, Any],
        errors: list[str],
    ) -> Pool:
        try:
            pool = self.pool_router.pick(req, plan)
            if pool is None:
                raise RuntimeError("pool_router.pick returned None")
        except Exception as exc:
            logger.exception(f"Agent pool router failed: {exc}")
            errors.append(f"pool_router:{exc}")
            pool = self._fallback_pool()
            trace["pool"] = {
                "requested_pool_id": plan.pool_id,
                "pool_name": type(pool).__name__,
                "pool_id": pool.pool_id,
                "fallback": True,
                "error": str(exc),
            }
            return pool

        fallback = pool.pool_id != plan.pool_id
        trace["pool"] = {
            "requested_pool_id": plan.pool_id,
            "pool_name": type(pool).__name__,
            "pool_id": pool.pool_id,
            "fallback": fallback,
        }
        return pool

    async def _safe_pool_run(
        self,
        req: AgentRequest,
        plan: RoutingPlan,
        ctx: ContextPack,
        pool: Pool,
        trace: Dict[str, Any],
        errors: list[str],
    ) -> Dict[str, Any]:
        try:
            raw = await pool.run(req, plan, ctx)
            trace["pool"]["raw_keys"] = list(raw.keys())
            return raw
        except Exception as exc:
            logger.exception(f"Agent pool run failed: {exc}")
            errors.append(f"pool_run:{exc}")

            fallback_pool = self._fallback_pool()
            trace["pool"]["error"] = str(exc)
            trace["pool"]["fallback"] = True
            trace["pool"]["fallback_pool_id"] = fallback_pool.pool_id
            try:
                raw = await fallback_pool.run(req, plan, ctx)
                trace["pool"]["raw_keys"] = list(raw.keys())
                return raw
            except Exception as fallback_exc:
                logger.exception(f"Agent fallback pool run failed: {fallback_exc}")
                errors.append(f"fallback_pool_run:{fallback_exc}")
                return {"draft": "我刚才处理时出现了问题，请重试一次。"}

    def _fallback_pool(self) -> Pool:
        router_fallback = getattr(self.pool_router, "fallback_pool", None)
        if callable(router_fallback):
            return cast(Pool, router_fallback())
        return self._builtin_chat_pool

    async def _safe_aggregate(
        self,
        req: AgentRequest,
        plan: RoutingPlan,
        ctx: ContextPack,
        raw: Dict[str, Any],
        trace: Dict[str, Any],
        errors: list[str],
    ) -> str:
        try:
            final_text = await self.aggregator.aggregate(req, plan, ctx, raw)
            trace["aggregation"] = {"length": len(final_text)}
            return final_text
        except Exception as exc:
            logger.exception(f"Agent aggregation failed: {exc}")
            errors.append(f"aggregation:{exc}")
            fallback_text = raw.get("draft") if isinstance(raw, dict) else ""
            fallback_text = str(fallback_text).strip() if fallback_text else ""
            if not fallback_text:
                fallback_text = "我处理到聚合阶段出现问题，请再试一次。"
            trace["aggregation"] = {
                "fallback": True,
                "error": str(exc),
                "length": len(fallback_text),
            }
            return fallback_text

    def _safe_speak(
        self,
        req: AgentRequest,
        final_text: str,
        plan: RoutingPlan,
        pool: Pool,
        trace: Dict[str, Any],
        errors: list[str],
    ) -> Observation:
        metadata = {
            "task_type": plan.task_type,
            "pool_id": pool.pool_id,
            "pool_selector_kind": plan.meta.get("pool_selector_kind"),
            "fallback": trace.get("fallback_triggered", False),
        }
        try:
            out_obs = self.speaker.speak(req, final_text, extra=metadata)
            out_obs.metadata = dict(out_obs.metadata or {})
            trace["speaker"] = {
                "source_name": out_obs.source_name,
                "actor_id": out_obs.actor.actor_id if out_obs.actor else None,
            }
            return out_obs
        except Exception as exc:
            logger.exception(f"Agent speaker failed: {exc}")
            errors.append(f"speaker:{exc}")
            trace["speaker"] = {"fallback": True, "error": str(exc)}
            return Observation(
                obs_type=ObservationType.MESSAGE,
                source_name="agent:speaker",
                source_kind=SourceKind.INTERNAL,
                session_key=req.obs.session_key,
                actor=Actor(actor_id="agent", actor_type="system", display_name="Agent"),
                payload=MessagePayload(
                    text=final_text or "我刚刚回复失败了，请再发一次。"
                ),
                metadata=dict(metadata),
            )


def _build_context_summary(plan: RoutingPlan, ctx: ContextPack, meta: Dict[str, Any]) -> Dict[str, Any]:
    slots = getattr(ctx, "slots", {}) or {}
    slot_summaries: list[dict[str, Any]] = []
    for name, slot in slots.items():
        slot_meta = dict(slot.meta or {}) if hasattr(slot, "meta") else {}
        summary = {
            "name": name,
            "status": getattr(slot, "status", None),
            "priority": getattr(slot, "priority", None),
        }
        if name == "current_input":
            text = ""
            value = getattr(slot, "value", None)
            if isinstance(value, dict):
                text = value.get("text") or ""
            summary["text_len"] = len(text)
            summary["preview"] = text[:40]
        elif name == "recent_obs":
            count = slot_meta.get("count")
            if count is None and isinstance(getattr(slot, "value", None), list):
                count = len(slot.value)
            summary["count"] = count
        elif name == "plan_meta":
            value = getattr(slot, "value", None)
            if isinstance(value, dict):
                meta_value = value.get("meta", {}) if isinstance(value.get("meta"), dict) else {}
                summary.update(
                    {
                        "task_type": value.get("task_type"),
                        "pool_id": value.get("pool_id"),
                        "strategy": meta_value.get("strategy"),
                        "complexity": meta_value.get("complexity"),
                        "confidence": meta_value.get("confidence"),
                    }
                )
        slot_summaries.append(summary)

    errors = meta.get("errors") or []
    return {
        "requested_by_plan": meta.get("requested_by_plan"),
        "auto_injected": meta.get("auto_injected"),
        "requested_effective": meta.get("requested_effective"),
        "provided": meta.get("provided"),
        "missing": meta.get("missing"),
        "errors": errors,
        "errors_count": len(errors),
        "priorities": meta.get("priorities"),
        "priority_sources": meta.get("priority_sources"),
        "slots": slot_summaries,
        "recent_obs_count": len(ctx.recent_obs),
    }


# 兼容导出：旧测试可能直接引用这些名字
DefaultPoolSelector = RulePoolSelector
DefaultPlanner = RulePoolSelector
DefaultContextBuilder = RecentObsContextBuilder
DefaultPoolRouter = AgentPoolRouter
DefaultAggregator = DraftAggregator
DefaultSpeaker = AgentSpeaker
