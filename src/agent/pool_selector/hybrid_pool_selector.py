"""HybridPoolSelector：规则预判 + 小模型判定 + 按需调用大模型 + 兜底。"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional

from ..types import AgentRequest, RoutingPlan
from .llm_pool_selector import LLMPoolSelector
from .rule_pool_selector import RulePoolSelector
from .validator import normalize_routing_plan_payload
from .types import PoolSelectorInputView


class HybridPoolSelector:
    """先跑 RulePoolSelector，再让小模型决定是否升级到大模型。"""

    kind = "hybrid"

    def __init__(
        self,
        *,
        config: Optional[Mapping[str, Any]] = None,
        rule_pool_selector: Optional[RulePoolSelector] = None,
        llm_pool_selector: Optional[LLMPoolSelector] = None,
        small_llm_pool_selector: Optional[LLMPoolSelector] = None,
    ) -> None:
        self._cfg = dict(config or {})
        self._rule = rule_pool_selector or RulePoolSelector()
        self._llm = llm_pool_selector or LLMPoolSelector(config=self._cfg)
        self._small_llm = small_llm_pool_selector or self._build_small_llm_pool_selector()
        self._timeout_seconds = _to_positive_float(self._cfg.get("timeout_seconds"), default=8.0)
        esc_cfg = self._cfg.get("escalation", {})
        esc_cfg = dict(esc_cfg) if isinstance(esc_cfg, Mapping) else {}
        self._confidence_threshold = _to_unit_float(esc_cfg.get("confidence_threshold"), default=0.75)
        self._force_big_for_task_types = _to_string_set(
            esc_cfg.get("force_big_model_for_task_types"),
            default={"plan"},
        )
        self._force_big_for_complexities = _to_string_set(
            esc_cfg.get("complexities_need_big"),
            default={"multi_step", "open_ended"},
        )

    async def select(self, req: AgentRequest, view: PoolSelectorInputView | None = None) -> RoutingPlan:
        rule_plan = await self._rule.select(req, view=view)
        rule_guess = {
            "task_type": rule_plan.task_type,
            "pool_id": rule_plan.pool_id,
        }
        recent_obs_count = (
            view.meta.get("recent_obs_count")
            if view is not None and isinstance(view.meta, dict)
            else len(req.session_state.recent_obs or [])
        )

        small_plan: Optional[RoutingPlan] = None
        small_error: Optional[str] = None
        if self._small_llm is not None:
            try:
                small_plan = await asyncio.wait_for(
                    self._small_llm.select(
                        req,
                        rule_plan=rule_plan,
                        recent_obs_count=recent_obs_count,
                        view=view,
                    ),
                    timeout=self._timeout_seconds,
                )
            except Exception as exc:
                small_error = str(exc)

        if small_plan is not None:
            need_big, gate_reason = self._need_big_model(small_plan)
            if not need_big:
                payload = _to_payload(
                    small_plan,
                    meta_override={
                        "rule_guess": rule_guess,
                        "selector_stage": "hybrid",
                        "selector_llm_called": True,
                        "selector_llm_parse_ok": True,
                        "small_llm_called": True,
                        "small_llm_parse_ok": True,
                        "big_llm_called": False,
                        "big_llm_parse_ok": None,
                        "escalated_to_big": False,
                        "small_gate_decision": "use_small",
                        "small_gate_reason": gate_reason,
                        "final_routing_source": "small_llm",
                    },
                )
                return normalize_routing_plan_payload(payload, pool_selector_kind="hybrid_small_llm")

            try:
                big_plan = await asyncio.wait_for(
                    self._llm.select(
                        req,
                        rule_plan=small_plan,
                        recent_obs_count=recent_obs_count,
                        view=view,
                    ),
                    timeout=self._timeout_seconds,
                )
                payload = _to_payload(
                    big_plan,
                    meta_override={
                        "rule_guess": rule_guess,
                        "selector_stage": "hybrid",
                        "selector_llm_called": True,
                        "selector_llm_parse_ok": True,
                        "small_llm_called": True,
                        "small_llm_parse_ok": True,
                        "big_llm_called": True,
                        "big_llm_parse_ok": True,
                        "escalated_to_big": True,
                        "small_gate_decision": "require_big",
                        "small_gate_reason": gate_reason,
                        "final_routing_source": "big_llm",
                    },
                )
                return normalize_routing_plan_payload(payload, pool_selector_kind="hybrid_big_llm")
            except Exception as exc:
                payload = _to_payload(
                    small_plan,
                    meta_override={
                        "rule_guess": rule_guess,
                        "selector_stage": "hybrid",
                        "selector_llm_called": True,
                        "selector_llm_parse_ok": True,
                        "small_llm_called": True,
                        "small_llm_parse_ok": True,
                        "big_llm_called": True,
                        "big_llm_parse_ok": False,
                        "escalated_to_big": True,
                        "small_gate_decision": "require_big",
                        "small_gate_reason": gate_reason,
                        "fallback_reason": str(exc),
                        "llm_error": str(exc),
                        "final_routing_source": "small_llm",
                    },
                )
                return normalize_routing_plan_payload(payload, pool_selector_kind="hybrid_small_fallback")

        try:
            llm_plan = await asyncio.wait_for(
                self._llm.select(
                    req,
                    rule_plan=rule_plan,
                    recent_obs_count=recent_obs_count,
                    view=view,
                ),
                timeout=self._timeout_seconds,
            )
            payload = _to_payload(
                llm_plan,
                meta_override={
                    "rule_guess": rule_guess,
                    "selector_stage": "hybrid",
                    "selector_llm_called": True,
                    "selector_llm_parse_ok": True,
                    "small_llm_called": self._small_llm is not None,
                    "small_llm_parse_ok": False if self._small_llm is not None else None,
                    "small_llm_error": small_error,
                    "big_llm_called": True,
                    "big_llm_parse_ok": True,
                    "escalated_to_big": False,
                    "small_gate_decision": "skipped" if self._small_llm is None else "error",
                    "small_gate_reason": "small_llm_disabled" if self._small_llm is None else "small_llm_error",
                    "final_routing_source": "big_llm",
                },
            )
            return normalize_routing_plan_payload(payload, pool_selector_kind="hybrid_llm")
        except Exception as exc:
            reason = str(exc)
            if small_error:
                reason = f"small_llm_error={small_error}; llm_error={reason}"
            payload = _to_payload(
                rule_plan,
                meta_override={
                    "rule_guess": rule_guess,
                    "selector_stage": "hybrid",
                    "selector_llm_called": True,
                    "selector_llm_parse_ok": False,
                    "small_llm_called": self._small_llm is not None,
                    "small_llm_parse_ok": False if self._small_llm is not None else None,
                    "small_llm_error": small_error,
                    "big_llm_called": True,
                    "big_llm_parse_ok": False,
                    "fallback_reason": reason,
                    "llm_error": reason,
                    "final_routing_source": "rule_fallback",
                },
            )
            return normalize_routing_plan_payload(payload, pool_selector_kind="hybrid_rule_fallback")

    def _build_small_llm_pool_selector(self) -> Optional[LLMPoolSelector]:
        raw = self._cfg.get("small_llm")
        if not isinstance(raw, Mapping):
            return None
        small_cfg = dict(raw)
        enabled = small_cfg.get("enabled", True)
        if isinstance(enabled, bool) and not enabled:
            return None
        small_cfg.pop("enabled", None)
        return LLMPoolSelector(config={"llm": small_cfg})

    def _need_big_model(self, small_plan: RoutingPlan) -> tuple[bool, str]:
        meta = dict(small_plan.meta or {})
        need_big_raw = meta.get("need_big_model")
        if isinstance(need_big_raw, bool):
            return need_big_raw, "small_meta_need_big_model"
        if isinstance(need_big_raw, (int, float)):
            return bool(need_big_raw), "small_meta_need_big_model_numeric"

        task_type = str(small_plan.task_type or "").strip().lower()
        if task_type in self._force_big_for_task_types:
            return True, "force_task_type"

        complexity = str(meta.get("complexity", "")).strip().lower()
        if complexity in self._force_big_for_complexities:
            return True, "force_complexity"

        confidence = _to_unit_float(meta.get("confidence"), default=0.6)
        if confidence < self._confidence_threshold:
            return True, "low_confidence"

        return False, "small_model_confident"


def _to_positive_float(raw: Any, *, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _to_unit_float(raw: Any, *, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _to_string_set(raw: Any, *, default: set[str]) -> set[str]:
    if not isinstance(raw, (list, tuple, set)):
        return set(default)
    out: set[str] = set()
    for item in raw:
        text = str(item).strip().lower()
        if text:
            out.add(text)
    return out or set(default)


def _to_payload(plan: RoutingPlan, *, meta_override: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    meta = dict(plan.meta or {})
    if meta_override:
        for key, value in meta_override.items():
            if value is not None:
                meta[key] = value
    return {
        "task_type": plan.task_type,
        "pool_id": plan.pool_id,
        "required_context": list(plan.required_context),
        "meta": meta,
        # 兼容历史 trace 读取路径
        "rule_guess": meta.get("rule_guess"),
        "selector_llm_called": bool(meta.get("selector_llm_called", True)),
        "selector_llm_parse_ok": bool(meta.get("selector_llm_parse_ok", True)),
        "fallback_reason": meta.get("fallback_reason"),
        "llm_error": meta.get("llm_error"),
    }
