from types import SimpleNamespace

import pytest

from astrbot.core.interaction.personal_policy import (
    PersonalPolicyAction,
    PersonalPolicyError,
    build_personal_policy_output_contract,
    build_personal_policy_system_prompt,
    extract_personal_policy_decision,
)
from astrbot.core.output_contract import CompiledOutputContract


def test_policy_prompt_requires_novel_facts_before_reexpressing():
    prompt = build_personal_policy_system_prompt()

    assert "最近 assistant 已表达相同意图" in prompt
    assert "Heartbeat 只表示到了评估时点" in prompt


def _compiled_contract():
    contract = build_personal_policy_output_contract()
    return contract, CompiledOutputContract(
        contract=contract,
        strategy="protocol_tool_call",
        tool_name=contract.preferred_tool_name,
        tool_schema=contract.schema,
    )


def test_policy_extraction_reports_invalid_matching_tool_call():
    contract, compiled = _compiled_contract()
    response = SimpleNamespace(
        tools_call_name=["personal_policy_decision"],
        tools_call_args=[
            {
                "action": "ignore",
                "reason_code": "insufficient_value",
                "reply_intent": "Only a heartbeat was observed.",
                "importance": 0.0,
                "defer_seconds": 0,
            }
        ],
    )

    with pytest.raises(PersonalPolicyError) as exc_info:
        extract_personal_policy_decision(response, contract, compiled)

    assert exc_info.value.reason == "invalid_policy_tool_call"


def test_policy_extraction_accepts_valid_matching_tool_call():
    contract, compiled = _compiled_contract()
    response = SimpleNamespace(
        tools_call_name=["personal_policy_decision"],
        tools_call_args=[
            {
                "action": "ignore",
                "reason_code": "insufficient_value",
                "reply_intent": "",
                "importance": 0.0,
                "defer_seconds": 0,
            }
        ],
    )

    decision = extract_personal_policy_decision(response, contract, compiled)

    assert decision.action is PersonalPolicyAction.IGNORE
