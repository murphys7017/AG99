from __future__ import annotations

from typing import Any

from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.output_contract import CompiledOutputContract, OutputContract


def build_single_tool_set_from_contract(
    contract: OutputContract,
    *,
    description: str = "",
) -> ToolSet | None:
    tool_name = (contract.preferred_tool_name or "").strip()
    if contract.mode != "tool_call" or not tool_name:
        return None

    tool_set = ToolSet()
    tool_set.add_tool(
        FunctionTool(
            name=tool_name,
            description=description,
            parameters=_build_tool_parameters_from_contract(contract),
            handler=None,
        )
    )
    return tool_set


def build_single_tool_set_from_compiled_contract(
    compiled_contract: CompiledOutputContract | None,
    *,
    description: str = "",
) -> ToolSet | None:
    if compiled_contract is None:
        return None
    tool_name = (compiled_contract.tool_name or "").strip()
    if compiled_contract.strategy != "protocol_tool_call" or not tool_name:
        return None

    tool_set = ToolSet()
    tool_set.add_tool(
        FunctionTool(
            name=tool_name,
            description=description,
            parameters=_normalize_tool_schema(compiled_contract.tool_schema),
            handler=None,
        )
    )
    return tool_set


def merge_output_contract_tool_set(
    func_tool: ToolSet | None,
    contract_tool_set: ToolSet | None,
) -> ToolSet | None:
    """Combine executable tools with a protocol-level output tool.

    The returned ToolSet is detached from the executable inventory. A name
    collision is resolved in favor of the output contract so a plugin cannot
    replace a terminal protocol schema with an executable handler.
    """
    if contract_tool_set is None or contract_tool_set.empty():
        return func_tool

    merged = ToolSet(list(func_tool.tools) if func_tool is not None else [])
    for tool in contract_tool_set:
        merged.add_tool(tool)
    return merged


def _build_tool_parameters_from_contract(contract: OutputContract) -> dict[str, Any]:
    schema = contract.schema if isinstance(contract.schema, dict) else None
    return _normalize_tool_schema(schema)


def _normalize_tool_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    if schema is None:
        return {"type": "object", "properties": {}}

    normalized_schema = dict(schema)
    normalized_schema.setdefault("type", "object")
    properties = normalized_schema.get("properties")
    if not isinstance(properties, dict):
        normalized_schema["properties"] = {}
    required = normalized_schema.get("required")
    if required is not None and not isinstance(required, list):
        normalized_schema.pop("required", None)
    return normalized_schema


__all__ = [
    "build_single_tool_set_from_compiled_contract",
    "build_single_tool_set_from_contract",
    "merge_output_contract_tool_set",
]
