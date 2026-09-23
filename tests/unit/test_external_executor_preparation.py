import pytest

from astrbot.core.execution import CoreExecutionSpec
from astrbot.core.executors.external import (
    ExternalExecutorConfigurationError,
    prepare_external_executor_request,
)
from astrbot.core.prompt.context_types import ContextPack, ContextSlot


def _spec() -> CoreExecutionSpec:
    pack = ContextPack(
        slots={
            "system.core_execution_context": ContextSlot(
                name="system.core_execution_context",
                value={
                    "execution_prompt": "Inspect the repository and update one file.",
                },
                category="system",
                source="test",
                meta={"targets": ["core"]},
            ),
            "conversation.history": ContextSlot(
                name="conversation.history",
                value={"turns": [{"role": "user", "content": "Please fix it."}]},
                category="conversation",
                source="test",
            ),
            "persona.prompt": ContextSlot(
                name="persona.prompt",
                value="This must not reach an external executor.",
                category="persona",
                source="test",
            ),
            "capability.tools_schema": ContextSlot(
                name="capability.tools_schema",
                value={"tools": [{"name": "astrbot_only_tool"}]},
                category="capability",
                source="test",
            ),
        }
    )
    return CoreExecutionSpec.from_context_pack(
        context_pack=pack,
        turn_id="turn-1",
        task_spec={
            "task_intent": "coding",
            "task_summary": "Update one file.",
            "execution_prompt": "Inspect the repository and update one file.",
            "suggested_capabilities": ["workspace_io"],
        },
    )


def test_prepare_external_request_uses_core_projection_and_scoped_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    child = workspace / "project"
    child.mkdir()

    request = prepare_external_executor_request(
        execution_spec=_spec(),
        deadline_view=None,
        executor_id="codex_cli",
        runtime_config_id="bot-a",
        session_id="qq:10001",
        workspace_config={
            "workspace_root": str(workspace),
            "workspace": "project",
        },
    )

    assert request.workspace == child.resolve()
    assert request.session_key.workspace_root == workspace.resolve()
    assert request.session_key.workspace == child.resolve()
    assert request.session_key.runtime_config_id == "bot-a"
    assert request.capabilities == ("workspace_io",)
    assert "Inspect the repository and update one file." in request.prompt
    assert "conversation.history" in request.prompt
    assert "persona.prompt" not in request.prompt
    assert "astrbot_only_tool" not in request.prompt


def test_prepare_external_request_rejects_workspace_outside_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(
        ExternalExecutorConfigurationError,
        match="workspace must be inside workspace_root",
    ):
        prepare_external_executor_request(
            execution_spec=_spec(),
            deadline_view=None,
            executor_id="codex_cli",
            runtime_config_id="bot-a",
            session_id="qq:10001",
            workspace_config={
                "workspace_root": str(workspace),
                "workspace": str(outside),
            },
        )


def test_prepare_external_request_rejects_relative_workspace_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(
        ExternalExecutorConfigurationError,
        match="workspace_root must be absolute",
    ):
        prepare_external_executor_request(
            execution_spec=_spec(),
            deadline_view=None,
            executor_id="codex_cli",
            runtime_config_id="bot-a",
            session_id="qq:10001",
            workspace_config={"workspace_root": "workspace"},
        )
