from types import SimpleNamespace

import pytest

from astrbot.core.workspace import (
    WORKSPACE_TYPE_PROJECT,
    WORKSPACE_TYPE_SESSION,
    default_workspace_root,
    resolve_workspace_root_for_umo,
    workspace_path_to_root,
)
from astrbot.dashboard.services.chatui_project_service import (
    ChatUIProjectService,
    ChatUIProjectServiceError,
)


def test_custom_workspace_accepts_existing_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "astrbot.core.workspace.get_astrbot_workspaces_path",
        lambda: str(tmp_path),
    )

    workspace_type, workspace_path = ChatUIProjectService._normalize_workspace_config(
        {
            "workspace_type": "custom",
            "workspace_path": str(workspace),
        },
    )

    assert workspace_type == "custom"
    assert workspace_path == str(workspace)


def test_custom_workspace_rejects_missing_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "astrbot.core.workspace.get_astrbot_workspaces_path",
        lambda: str(tmp_path),
    )

    with pytest.raises(ChatUIProjectServiceError, match="does not exist"):
        ChatUIProjectService._normalize_workspace_config(
            {
                "workspace_type": "custom",
                "workspace_path": "missing",
            },
        )


def test_custom_workspace_rejects_relative_path_traversal(tmp_path, monkeypatch):
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    monkeypatch.setattr(
        "astrbot.core.workspace.get_astrbot_workspaces_path",
        lambda: str(workspaces_root),
    )

    with pytest.raises(ChatUIProjectServiceError, match="must stay within"):
        ChatUIProjectService._normalize_workspace_config(
            {
                "workspace_type": "custom",
                "workspace_path": "../outside",
            },
        )


def test_custom_workspace_preserves_absolute_path(tmp_path, monkeypatch):
    outside_workspace = tmp_path / "outside"
    workspaces_root = tmp_path / "workspaces"
    outside_workspace.mkdir()
    workspaces_root.mkdir()
    monkeypatch.setattr(
        "astrbot.core.workspace.get_astrbot_workspaces_path",
        lambda: str(workspaces_root),
    )

    workspace_type, workspace_path = ChatUIProjectService._normalize_workspace_config(
        {
            "workspace_type": "custom",
            "workspace_path": str(outside_workspace),
        },
    )

    assert workspace_type == "custom"
    assert workspace_path == str(outside_workspace)
    assert workspace_path_to_root(workspace_path) == outside_workspace.resolve()


@pytest.mark.asyncio
async def test_resolve_workspace_root_for_webchat_project(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "astrbot.core.workspace.get_astrbot_workspaces_path",
        lambda: str(tmp_path),
    )

    class FakeDB:
        async def get_project_by_session(self, session_id: str, creator: str):
            assert session_id == "session-1"
            assert creator == "alice"
            return SimpleNamespace(
                project_id="project-1",
                workspace_type=WORKSPACE_TYPE_PROJECT,
                workspace_path=None,
            )

    root = await resolve_workspace_root_for_umo(
        "webchat:FriendMessage:webchat!alice!session-1",
        FakeDB(),
    )

    assert root == (tmp_path / "project_project-1").resolve(strict=False)


@pytest.mark.asyncio
async def test_resolve_workspace_root_falls_back_to_session(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "astrbot.core.workspace.get_astrbot_workspaces_path",
        lambda: str(tmp_path),
    )

    class FakeDB:
        async def get_project_by_session(self, session_id: str, creator: str):
            return None

    umo = "webchat:FriendMessage:webchat!alice!session-1"
    root = await resolve_workspace_root_for_umo(umo, FakeDB())

    assert root == default_workspace_root(umo)


def test_session_workspace_type_clears_custom_path():
    workspace_type, workspace_path = ChatUIProjectService._normalize_workspace_config(
        {
            "workspace_type": WORKSPACE_TYPE_SESSION,
            "workspace_path": "ignored",
        },
    )

    assert workspace_type == WORKSPACE_TYPE_SESSION
    assert workspace_path is None
