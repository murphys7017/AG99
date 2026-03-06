"""LLMPoolSelector：基于 LLM 输出 RoutingPlan。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from loguru import logger

from ...llm import LLMProvider
from ..types import AgentRequest, RoutingPlan
from .types import PoolSelectorInputView
from .validator import normalize_routing_plan_payload

_FENCED_JSON_RE = re.compile(r"^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$", re.IGNORECASE)


class LLMPoolSelector:
    """调用现有 LLMProvider 进行规划分类。"""

    kind = "llm"

    def __init__(
        self,
        *,
        config: Optional[Mapping[str, Any]] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        self._cfg = dict(config or {})
        self._llm_cfg = dict(self._cfg.get("llm", {})) if isinstance(self._cfg.get("llm"), Mapping) else {}
        self._provider = llm_provider
        self._provider_init_error: Optional[str] = None

        self._config_path = str(self._llm_cfg.get("config_path", "config/llm.yaml"))
        self._provider_name = str(self._llm_cfg.get("provider", "bailian"))
        self._model_name = str(self._llm_cfg.get("model", "qwen-max"))
        self._timeout_seconds = _to_positive_float(self._llm_cfg.get("timeout_seconds"), default=6.0)
        self._runtime_params = dict(self._llm_cfg.get("params", {})) if isinstance(self._llm_cfg.get("params"), Mapping) else {}
        self._prompt = self._load_prompt()

    async def select(
        self,
        req: AgentRequest,
        view: PoolSelectorInputView | None = None,
        *,
        rule_plan: Optional[RoutingPlan] = None,
        recent_obs_count: Optional[int] = None,
    ) -> RoutingPlan:
        """调用 LLM 并返回规范化 RoutingPlan。"""
        selector_input = self._build_selector_input(
            req,
            rule_plan=rule_plan,
            recent_obs_count=recent_obs_count,
            view=view,
        )
        messages = self._build_messages(selector_input)

        provider = self._get_or_create_provider()
        llm_text = await asyncio.wait_for(
            asyncio.to_thread(provider.call, messages, **self._runtime_params),
            timeout=self._timeout_seconds,
        )

        payload = _parse_json_payload(llm_text)
        if not isinstance(payload, Mapping):
            raise ValueError("llm pool selector payload is not a JSON object")

        return normalize_routing_plan_payload(payload, pool_selector_kind=self.kind)

    async def plan(
        self,
        req: AgentRequest,
        view: PoolSelectorInputView | None = None,
        *,
        rule_plan: Optional[RoutingPlan] = None,
        recent_obs_count: Optional[int] = None,
    ) -> RoutingPlan:
        """Deprecated alias: plan() -> select()."""
        return await self.select(
            req,
            rule_plan=rule_plan,
            recent_obs_count=recent_obs_count,
            view=view,
        )

    def _get_or_create_provider(self) -> LLMProvider:
        if self._provider is not None:
            return self._provider
        if self._provider_init_error is not None:
            raise RuntimeError(self._provider_init_error)

        try:
            self._provider = LLMProvider.from_config(
                provider=self._provider_name,
                model=self._model_name,
                config_path=self._config_path,
                default_params=self._runtime_params,
            )
            return self._provider
        except Exception as exc:
            self._provider_init_error = f"llm_provider_init_failed:{exc}"
            raise RuntimeError(self._provider_init_error) from exc

    def _build_selector_input(
        self,
        req: AgentRequest,
        *,
        rule_plan: Optional[RoutingPlan],
        recent_obs_count: Optional[int],
        view: PoolSelectorInputView | None,
    ) -> Dict[str, Any]:
        payload = getattr(req.obs, "payload", None)
        text = view.current_input_text if view is not None else getattr(payload, "text", None)
        text = text.strip() if isinstance(text, str) else ""

        hint_summary = view.gate_hint_view if view is not None else {}
        if not hint_summary:
            hint = req.gate_hint or getattr(req.gate_decision, "hint", None)
            if hint is not None:
                hint_summary = {
                    "model_tier": getattr(hint, "model_tier", None),
                    "response_policy": getattr(hint, "response_policy", None),
                    "budget_level": getattr(getattr(hint, "budget", None), "budget_level", None),
                    "max_tokens": getattr(getattr(hint, "budget", None), "max_tokens", None),
                    "max_tool_calls": getattr(getattr(hint, "budget", None), "max_tool_calls", None),
                }

        rule_guess = None
        if rule_plan is not None:
            rule_guess = {
                "task_type": rule_plan.task_type,
                "pool_id": rule_plan.pool_id,
                "required_context": list(rule_plan.required_context),
                "meta": dict(rule_plan.meta or {}),
            }

        return {
            "text": text,
            "rule_guess": rule_guess,
            "recent_obs_count": recent_obs_count if recent_obs_count is not None else len(req.session_state.recent_obs or []),
            "recent_obs_preview": view.recent_obs_view if view is not None else _extract_recent_obs_preview(req, limit=6, max_chars=160),
            "gate_hint": hint_summary,
            "allowed_task_types": ["chat", "code", "plan", "creative"],
            "allowed_required_context": ["recent_obs", "gate_hint", "memory_summary", "retrieved_docs", "tool_results"],
        }

    def _build_messages(self, selector_input: Dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self._prompt},
            {"role": "user", "content": json.dumps(selector_input, ensure_ascii=False)},
        ]

    def _load_prompt(self) -> str:
        configured = self._llm_cfg.get("prompt")
        if isinstance(configured, str) and configured.strip():
            return configured.strip()

        prompt_path_raw = self._llm_cfg.get("prompt_path")
        if isinstance(prompt_path_raw, str) and prompt_path_raw.strip():
            path = Path(prompt_path_raw)
            if path.exists():
                try:
                    return path.read_text(encoding="utf-8").strip()
                except Exception as exc:
                    logger.warning(f"LLMPoolSelector prompt read failed: {exc}")

        return _default_pool_selector_prompt()


def _parse_json_payload(raw_text: str) -> Mapping[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("empty llm pool selector output")

    fence_match = _FENCED_JSON_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        loaded = json.loads(text)
        if isinstance(loaded, Mapping):
            return loaded
    except json.JSONDecodeError:
        pass

    # 容错：提取第一个 JSON object 片段
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidate = text[first : last + 1]
        loaded = json.loads(candidate)
        if isinstance(loaded, Mapping):
            return loaded
    raise ValueError("llm pool selector output is not valid json object")


def _to_positive_float(raw: Any, *, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _extract_recent_obs_preview(
    req: AgentRequest,
    *,
    limit: int,
    max_chars: int,
) -> list[dict[str, str]]:
    recent = list(req.session_state.recent_obs or [])
    if not recent:
        return []

    out: list[dict[str, str]] = []
    for obs in recent[-limit:]:
        payload = getattr(obs, "payload", None)
        text = getattr(payload, "text", None)
        if not isinstance(text, str):
            continue
        normalized = text.strip()
        if not normalized:
            continue
        if len(normalized) > max_chars:
            normalized = normalized[: max_chars - 3] + "..."

        actor = getattr(obs, "actor", None)
        actor_id = getattr(actor, "actor_id", None)
        out.append(
            {
                "actor_id": str(actor_id or ""),
                "source_name": str(getattr(obs, "source_name", "") or ""),
                "text": normalized,
            }
        )
    return out


def _default_pool_selector_prompt() -> str:
    return (
        "你是一个路由选择器，不直接回答用户。\n"
        "你只能输出 JSON 对象，禁止 Markdown、禁止解释文本。\n"
        "合法 task_type/pool_id: chat, code, plan, creative。\n"
        "required_context 仅可从: recent_obs, gate_hint, memory_summary, retrieved_docs, tool_results 里选。\n"
        "meta 必须包含 reason, confidence(0~1), strategy(single_pass|draft_critique), complexity(simple|multi_step|open_ended)。\n"
        "输出格式:\n"
        "{\"task_type\":\"...\",\"pool_id\":\"...\",\"required_context\":[\"...\"],"
        "\"meta\":{\"reason\":\"...\",\"confidence\":0.0,\"strategy\":\"...\",\"complexity\":\"...\"}}\n"
        "示例1 输入: 闲聊问候 -> 输出 task_type=chat。\n"
        "示例2 输入: pytest traceback -> 输出 task_type=code。\n"
        "示例3 输入: 设计一个系统方案 -> 输出 task_type=plan。\n"
        "示例4 输入: 写一个脑暴故事点子 -> 输出 task_type=creative。"
    )


class LLMPlanner(LLMPoolSelector):
    """Deprecated alias: LLMPlanner -> LLMPoolSelector。"""
