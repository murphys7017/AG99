from types import SimpleNamespace

import pytest

from astrbot.core.interaction.personal_policy import (
    PersonalPolicyAction,
    PersonalPolicyError,
    build_personal_policy_output_contract,
    extract_personal_policy_decision,
)
from astrbot.core.output_contract import CompiledOutputContract


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
