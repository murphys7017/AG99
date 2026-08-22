from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

OutputContractMode = Literal["text", "json_object", "tool_call"]
OutputContractStrategy = Literal[
    "prompt_only",
    "protocol_tool_call",
    "protocol_native_json",
]


@dataclass
class OutputContract:
    mode: OutputContractMode = "text"
    strict: bool = False
    schema: dict[str, Any] | None = None
    preferred_tool_name: str | None = None
    allow_text_fallback: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "strict": self.strict,
            "schema": self.schema,
            "preferred_tool_name": self.preferred_tool_name,
            "allow_text_fallback": self.allow_text_fallback,
        }

    @classmethod
    def from_mapping(cls, value: object) -> OutputContract | None:
        if value is None:
            return None
        if isinstance(value, OutputContract):
            return value
        if not isinstance(value, dict):
            return None
        mode = str(value.get("mode", "text") or "text").strip()
        if mode not in {"text", "json_object", "tool_call"}:
            return None
        schema = value.get("schema")
        if schema is not None and not isinstance(schema, dict):
            schema = None
        preferred_tool_name = value.get("preferred_tool_name")
        if preferred_tool_name is not None:
            preferred_tool_name = str(preferred_tool_name).strip() or None
        return cls(
            mode=mode,
            strict=bool(value.get("strict", False)),
            schema=schema,
            preferred_tool_name=preferred_tool_name,
            allow_text_fallback=bool(value.get("allow_text_fallback", True)),
        )


@dataclass
class CompiledOutputContract:
    contract: OutputContract
    strategy: OutputContractStrategy
    degraded: bool = False
    degrade_reason: str | None = None
    tool_name: str | None = None
    tool_schema: dict[str, Any] | None = None
    fallback_prompt_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract.to_dict(),
            "strategy": self.strategy,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
            "tool_name": self.tool_name,
            "tool_schema": self.tool_schema,
            "fallback_prompt_text": self.fallback_prompt_text,
        }


def build_output_contract_fallback_prompt(contract: OutputContract) -> str:
    if contract.mode == "text":
        return ""

    strict_prefix = "必须" if contract.strict else "请"
    schema_text = _dump_schema(contract.schema)

    if contract.mode == "json_object":
        parts = [
            f"{strict_prefix}只输出一个 JSON object，不要输出额外文本、Markdown、XML 或 HTML。",
        ]
        if schema_text:
            parts.append(f"输出必须符合以下 JSON 结构：\n{schema_text}")
        return "\n".join(parts)

    if contract.mode == "tool_call":
        tool_name = (contract.preferred_tool_name or "").strip()
        target_text = (
            f"`{tool_name}` 工具调用参数对应的"
            if tool_name
            else "目标工具调用参数对应的"
        )
        parts = [
            (
                f"{strict_prefix}在无法使用协议级 tool call 时，仅返回一个与"
                f"{target_text}单个 JSON object，"
                "不要输出额外文本、Markdown、XML 或 HTML。"
            ),
        ]
        if schema_text:
            parts.append(f"该 JSON object 必须符合以下结构：\n{schema_text}")
        return "\n".join(parts)

    return ""


def _dump_schema(schema: dict[str, Any] | None) -> str:
    if not isinstance(schema, dict) or not schema:
        return ""
    try:
        return json.dumps(schema, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return ""


__all__ = [
    "CompiledOutputContract",
    "OutputContract",
    "OutputContractMode",
    "OutputContractStrategy",
    "build_output_contract_fallback_prompt",
]
