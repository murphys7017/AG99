from types import SimpleNamespace

import pytest

from astrbot.core.interaction.core_planner import (
    CorePlannerError,
    build_core_planner_output_contract,
    build_core_planner_system_prompt,
    extract_core_planning_decision,
)
from astrbot.core.interaction.types import CorePlanningAction
from astrbot.core.output_contract import CompiledOutputContract


def _compiled(strategy: str) -> tuple:
    contract = build_core_planner_output_contract()
    return contract, CompiledOutputContract(
        contract=contract,
        strategy=strategy,
        tool_name="core_execution_plan"
        if strategy == "protocol_tool_call"
        else None,
        tool_schema=contract.schema
        if strategy == "protocol_tool_call"
        else None,
    )


def _execute_payload() -> dict:
    return {
        "decision": "execute",
        "core_task_spec": {
            "task_intent": "lookup",
            "task_summary": "查询当前时间",
            "execution_prompt": "查询当前时间并返回时区明确的结果。",
            "suggested_capabilities": ["time"],
        },
    }


def test_core_planner_prompt_is_independent_from_router_decision():
    prompt = build_core_planner_system_prompt()

    assert "hybrid" not in prompt
    assert "silent" not in prompt
    assert "Router" not in prompt
    assert "上游路由" not in prompt


def test_core_planner_prefers_protocol_tool_call():
    contract, compiled = _compiled("protocol_tool_call")
    response = SimpleNamespace(
        tools_call_name=["core_execution_plan"],
        tools_call_args=[_execute_payload()],
    )

    decision = extract_core_planning_decision(
        "ignored",
        llm_response=response,
        output_contract=contract,
        compiled_output_contract=compiled,
    )

    assert decision.action is CorePlanningAction.EXECUTE
    assert decision.task_spec is not None
    assert decision.task_spec.execution_prompt.startswith("查询当前时间")


def test_core_planner_accepts_prompt_only_structured_text():
    contract, compiled = _compiled("prompt_only")
    response = SimpleNamespace(tools_call_name=[], tools_call_args=[])

    decision = extract_core_planning_decision(
        '{"decision":"not_required","core_task_spec":null}',
        llm_response=response,
        output_contract=contract,
        compiled_output_contract=compiled,
    )

    assert decision.action is CorePlanningAction.NOT_REQUIRED
    assert decision.task_spec is None


def test_core_planner_rejects_missing_protocol_tool_call():
    contract, compiled = _compiled("protocol_tool_call")
    response = SimpleNamespace(tools_call_name=[], tools_call_args=[])

    with pytest.raises(CorePlannerError, match="tool call missing"):
        extract_core_planning_decision(
            '{"decision":"not_required","core_task_spec":null}',
            llm_response=response,
            output_contract=contract,
            compiled_output_contract=compiled,
        )


def test_core_planner_rejects_execute_without_task_spec():
    contract, compiled = _compiled("prompt_only")
    response = SimpleNamespace(tools_call_name=[], tools_call_args=[])

    with pytest.raises(CorePlannerError, match="invalid structured result"):
        extract_core_planning_decision(
            '{"decision":"execute","core_task_spec":null}',
            llm_response=response,
            output_contract=contract,
            compiled_output_contract=compiled,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "not_required"},
        {"decision": "not_required", "core_task_spec": {}},
        {
            "decision": "execute",
            "core_task_spec": {
                "task_summary": "查询当前时间",
                "execution_prompt": "查询当前时间。",
                "suggested_capabilities": [],
            },
        },
        {
            "decision": "execute",
            "core_task_spec": {
                "task_intent": "lookup",
                "task_summary": "查询当前时间",
                "execution_prompt": "查询当前时间。",
                "suggested_capabilities": "time",
            },
        },
        {
            "decision": "execute",
            "core_task_spec": {
                "task_intent": "lookup",
                "task_summary": "查询当前时间",
                "execution_prompt": "查询当前时间。",
                "suggested_capabilities": [1],
            },
        },
    ],
)
def test_core_planner_rejects_payloads_that_violate_declared_schema(payload):
    contract, compiled = _compiled("prompt_only")
    response = SimpleNamespace(tools_call_name=[], tools_call_args=[])

    with pytest.raises(CorePlannerError, match="invalid structured result"):
        extract_core_planning_decision(
            str(payload).replace("'", '"'),
            llm_response=response,
            output_contract=contract,
            compiled_output_contract=compiled,
        )


@pytest.mark.parametrize("empty_field", ["task_intent", "task_summary", "execution_prompt"])
def test_core_planner_rejects_execute_with_empty_required_task_field(empty_field):
    contract, compiled = _compiled("prompt_only")
    response = SimpleNamespace(tools_call_name=[], tools_call_args=[])
    payload = _execute_payload()
    payload["core_task_spec"][empty_field] = "  "

    with pytest.raises(CorePlannerError, match="invalid structured result"):
        extract_core_planning_decision(
            str(payload).replace("'", '"'),
            llm_response=response,
            output_contract=contract,
            compiled_output_contract=compiled,
        )


def test_core_planner_contract_requires_nonempty_task_fields():
    task_schema = build_core_planner_output_contract().schema["properties"][
        "core_task_spec"
    ]["anyOf"][0]

    assert task_schema["properties"]["task_intent"]["minLength"] == 1
    assert task_schema["properties"]["task_summary"]["minLength"] == 1
    assert task_schema["properties"]["execution_prompt"]["minLength"] == 1
