from pathlib import Path

import pytest

from astrbot.core.execution import CoreExecutionSpec
from astrbot.core.executors.assembly import CodexExecutorAssembly
from astrbot.core.executors.external import prepare_external_executor_request
from astrbot.core.prompt.context_types import ContextPack, ContextSlot


class _Session:
    async def iter_turn(self, prompt):
        if False:
            yield prompt

    async def interrupt(self, turn_id=None):
        return None


def _request(tmp_path: Path, executor_id="codex_cli"):
    pack = ContextPack(
        slots={
            "system.core_execution_context": ContextSlot(
                name="system.core_execution_context",
                value={"execution_prompt": "Inspect the workspace."},
                category="system",
                source="test",
                meta={"targets": ["core"]},
            )
        }
    )
    spec = CoreExecutionSpec.from_context_pack(
        context_pack=pack,
        turn_id="turn-1",
        task_spec={"execution_prompt": "Inspect the workspace."},
    )
    root = tmp_path / "root"
    root.mkdir()
    return prepare_external_executor_request(
        execution_spec=spec,
        deadline_view=None,
        executor_id=executor_id,
        runtime_config_id="bot-a",
        session_id="session-a",
        workspace_config={"workspace_root": str(root)},
    )


def test_codex_assembly_builds_external_body(tmp_path):
    request = _request(tmp_path)
    run = CodexExecutorAssembly(session=_Session()).build_run(request)
    assert run.executor_id == "codex_cli"


def test_codex_assembly_rejects_other_executor(tmp_path):
    request = _request(tmp_path, executor_id="native")
    with pytest.raises(ValueError, match="executor_id=codex_cli"):
        CodexExecutorAssembly(session=_Session()).build_run(request)
