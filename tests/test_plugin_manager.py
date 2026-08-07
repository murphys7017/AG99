import asyncio
import functools
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.provider.register import llm_tools
from astrbot.core.star import star_manager as star_manager_module
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata
from astrbot.core.star.star_manager import PluginDependencyInstallError, PluginManager
from astrbot.core.utils.pip_installer import PipInstallError
from astrbot.core.utils.requirements_utils import MissingRequirementsPlan

# --- Test Data & Helpers ---

TEST_PLUGIN_NAME = "helloworld"
TEST_PLUGIN_REPO = "https://github.com/AstrBotDevs/astrbot_plugin_helloworld"
TEST_PLUGIN_DIR = "helloworld"


def test_load_plugin_config_schema_accepts_utf8_bom(tmp_path: Path):
    schema_path = tmp_path / "_conf_schema.json"
    schema_path.write_bytes(b'\xef\xbb\xbf{"type": "object"}')

    assert PluginManager._load_plugin_config_schema(str(schema_path)) == {
        "type": "object"
    }


def test_load_plugin_config_schema_accepts_utf8_without_bom(tmp_path: Path):
    schema_path = tmp_path / "_conf_schema.json"
    schema_path.write_text('{"type": "object"}', encoding="utf-8")

    assert PluginManager._load_plugin_config_schema(str(schema_path)) == {
        "type": "object"
    }


def test_load_plugin_config_schema_reports_invalid_json(tmp_path: Path):
    schema_path = tmp_path / "_conf_schema.json"
    schema_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="不是有效的 JSON"):
        PluginManager._load_plugin_config_schema(str(schema_path))


class MockStar:
    def __init__(self):
        self.root_dir_name = TEST_PLUGIN_DIR
        self.name = TEST_PLUGIN_NAME
        self.repo = TEST_PLUGIN_REPO
        self.reserved = False
        self.info = {"repo": TEST_PLUGIN_REPO, "readme": ""}


def test_remove_plugin_runtime_extensions_clears_all_plugin_registries():
    manager = PluginManager.__new__(PluginManager)
    manager.context = context = SimpleNamespace(
        remove_prompt_extension_collectors_by_module_prefix=lambda prefix: 1,
        remove_interaction_prompt_contributors_by_module_prefix=lambda prefix: 1,
        remove_interaction_result_contributors_by_module_prefix=lambda prefix: 1,
        remove_interaction_stream_deciders_by_module_prefix=lambda prefix: 1,
        remove_interaction_lifecycle_observers_by_module_prefix=lambda prefix: 1,
        remove_runtime_observation_sensors_by_module_prefix=lambda prefix: 1,
        unregister_persona_effects=lambda **kwargs: 1,
    )
    prefix = "data.plugins.demo"

    calls: list[tuple[str, str]] = []
    for name in (
        "remove_prompt_extension_collectors_by_module_prefix",
        "remove_interaction_prompt_contributors_by_module_prefix",
        "remove_interaction_result_contributors_by_module_prefix",
        "remove_interaction_stream_deciders_by_module_prefix",
        "remove_interaction_lifecycle_observers_by_module_prefix",
        "remove_runtime_observation_sensors_by_module_prefix",
    ):
        setattr(
            context,
            name,
            lambda value, method=name: calls.append((method, value)),
        )
    context.unregister_persona_effects = lambda **kwargs: calls.append(
        ("unregister_persona_effects", kwargs["module_prefix"])
    )

    manager._remove_plugin_runtime_extensions(prefix)

    assert calls == [
        ("remove_prompt_extension_collectors_by_module_prefix", prefix),
        ("remove_interaction_prompt_contributors_by_module_prefix", prefix),
        ("remove_interaction_result_contributors_by_module_prefix", prefix),
        ("remove_interaction_stream_deciders_by_module_prefix", prefix),
        ("remove_interaction_lifecycle_observers_by_module_prefix", prefix),
        ("unregister_persona_effects", prefix),
        ("remove_runtime_observation_sensors_by_module_prefix", prefix),
    ]


def _write_local_test_plugin(plugin_path: Path, repo_url: str):
    """Creates a minimal valid plugin structure."""
    plugin_path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "name": TEST_PLUGIN_NAME,
        "repo": repo_url,
        "version": "1.0.0",
        "author": "AstrBot Team",
        "desc": "Local test plugin",
        "short_desc": "Local test short description",
    }
    with open(plugin_path / "metadata.yaml", "w", encoding="utf-8") as f:
        yaml.dump(metadata, f)
    with open(plugin_path / "main.py", "w", encoding="utf-8") as f:
        f.write("from astrbot.api.star import Star, Context, StarManager\n")
        f.write("@StarManager.register\n")
        f.write("class HelloWorld(Star):\n")
        f.write("    def __init__(self, context: Context): ...\n")


def _write_requirements(plugin_path: Path):
    """Creates a requirements.txt file."""
    with open(plugin_path / "requirements.txt", "w", encoding="utf-8") as f:
        f.write("networkx\n")


def test_load_plugin_i18n_reads_locale_files(tmp_path: Path):
    plugin_path = tmp_path / "plugin"
    i18n_path = plugin_path / ".astrbot-plugin" / "i18n"
    i18n_path.mkdir(parents=True)
    (i18n_path / "zh-CN.json").write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps({"metadata": {"desc": "中文描述"}}, ensure_ascii=False).encode(
            "utf-8"
        ),
    )
    (i18n_path / "en-US.json").write_text(
        json.dumps({"metadata": {"desc": "English description"}}),
        encoding="utf-8",
    )
    (i18n_path / "README.md").write_text("ignored", encoding="utf-8")

    assert PluginManager._load_plugin_i18n(str(plugin_path)) == {
        "zh-CN": {"metadata": {"desc": "中文描述"}},
        "en-US": {"metadata": {"desc": "English description"}},
    }


def test_load_plugin_i18n_ignores_legacy_directories(tmp_path: Path):
    plugin_path = tmp_path / "plugin"
    hidden_legacy_i18n_path = plugin_path / ".i18n"
    legacy_i18n_path = plugin_path / "i18n"
    hidden_legacy_i18n_path.mkdir(parents=True)
    legacy_i18n_path.mkdir()
    (hidden_legacy_i18n_path / "zh-CN.json").write_text(
        json.dumps({"metadata": {"desc": "隐藏旧目录"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (legacy_i18n_path / "zh-CN.json").write_text(
        json.dumps({"metadata": {"desc": "中文描述"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert PluginManager._load_plugin_i18n(str(plugin_path)) == {}


def test_load_plugin_metadata_includes_i18n(tmp_path: Path):
    plugin_path = tmp_path / "helloworld"
    _write_local_test_plugin(plugin_path, TEST_PLUGIN_REPO)
    i18n_path = plugin_path / ".astrbot-plugin" / "i18n"
    i18n_path.mkdir(parents=True)
    (i18n_path / "zh-CN.json").write_text(
        json.dumps({"metadata": {"display_name": "你好世界"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    metadata = PluginManager._load_plugin_metadata(str(plugin_path))

    assert metadata is not None
    assert metadata.short_desc == "Local test short description"
    assert metadata.pages == []
    assert metadata.i18n == {"zh-CN": {"metadata": {"display_name": "你好世界"}}}


def test_load_plugin_metadata_includes_pages(tmp_path: Path):
    plugin_path = tmp_path / "helloworld"
    _write_local_test_plugin(plugin_path, TEST_PLUGIN_REPO)
    metadata_path = plugin_path / "metadata.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    metadata["icon"] = "mdi-view-dashboard"
    metadata["pages"] = [{"name": "dashboard", "title": "Dashboard"}]
    metadata_path.write_text(yaml.dump(metadata), encoding="utf-8")

    loaded_metadata = PluginManager._load_plugin_metadata(str(plugin_path))

    assert loaded_metadata is not None
    assert loaded_metadata.icon == "mdi-view-dashboard"
    assert loaded_metadata.pages == [{"name": "dashboard", "title": "Dashboard"}]


@pytest.mark.asyncio
async def test_turn_off_plugin_deactivates_subdirectory_tools(monkeypatch):
    plugin = SimpleNamespace(
        name="demo",
        module_path="data.plugins.astrbot_plugin_demo.main",
        activated=True,
    )
    tool = FunctionTool(
        name="demo_tool",
        description="demo",
        parameters={"type": "object", "properties": {}},
        handler=None,
        handler_module_path="data.plugins.astrbot_plugin_demo.main.tools.extra",
    )

    manager = object.__new__(PluginManager)
    manager._pm_lock = asyncio.Lock()
    manager.context = SimpleNamespace(get_registered_star=lambda name: plugin)

    original_func_list = llm_tools.func_list[:]
    llm_tools.func_list = [tool]
    try:
        monkeypatch.setattr(
            PluginManager,
            "_terminate_plugin",
            staticmethod(lambda star_metadata: asyncio.sleep(0)),
        )

        await PluginManager.turn_off_plugin(manager, "demo")

        assert tool.active is False
        assert plugin.activated is False
    finally:
        from astrbot.core import sp

        sp.put("inactivated_plugins", [], scope="global", scope_id="global")
        sp.put("inactivated_llm_tools", [], scope="global", scope_id="global")
        llm_tools.func_list = original_func_list


@pytest.mark.asyncio
async def test_turn_on_plugin_reactivates_subdirectory_tools(monkeypatch):
    plugin = SimpleNamespace(
        name="demo",
        module_path="data.plugins.astrbot_plugin_demo.main",
        activated=False,
    )
    tool = FunctionTool(
        name="demo_tool",
        description="demo",
        parameters={"type": "object", "properties": {}},
        handler=None,
        handler_module_path="data.plugins.astrbot_plugin_demo.main.tools.extra",
        active=False,
    )

    manager = object.__new__(PluginManager)
    manager.context = SimpleNamespace(get_registered_star=lambda name: plugin)

    original_func_list = llm_tools.func_list[:]
    llm_tools.func_list = [tool]
    try:
        monkeypatch.setattr(
            manager,
            "reload",
            lambda plugin_name: asyncio.sleep(0),
            raising=False,
        )

        from astrbot.core import sp

        sp.put(
            "inactivated_plugins",
            [plugin.module_path],
            scope="global",
            scope_id="global",
        )
        sp.put(
            "inactivated_llm_tools",
            [tool.name],
            scope="global",
            scope_id="global",
        )

        await PluginManager.turn_on_plugin(manager, "demo")

        assert tool.active is True
        assert tool.name not in sp.get(
            "inactivated_llm_tools",
            [],
            scope="global",
            scope_id="global",
        )
    finally:
        from astrbot.core import sp

        sp.put("inactivated_plugins", [], scope="global", scope_id="global")
        sp.put("inactivated_llm_tools", [], scope="global", scope_id="global")
        llm_tools.func_list = original_func_list


def test_loaded_metadata_can_copy_i18n_into_existing_star_metadata(tmp_path: Path):
    plugin_path = tmp_path / "helloworld"
    _write_local_test_plugin(plugin_path, TEST_PLUGIN_REPO)
    i18n_path = plugin_path / ".astrbot-plugin" / "i18n"
    i18n_path.mkdir(parents=True)
    (i18n_path / "zh-CN.json").write_text(
        json.dumps({"metadata": {"desc": "中文描述"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    existing_metadata = star_manager_module.StarMetadata(name="old")
    loaded_metadata = PluginManager._load_plugin_metadata(str(plugin_path))

    assert loaded_metadata is not None
    existing_metadata.i18n = loaded_metadata.i18n
    assert existing_metadata.i18n == {"zh-CN": {"metadata": {"desc": "中文描述"}}}


def _clear_module_cache():
    """Clear test-specific modules from sys.modules to allow reloading."""
    import sys

    to_del = [m for m in sys.modules if m.startswith("data.plugins.helloworld")]
    for m in to_del:
        del sys.modules[m]


def _build_load_mock(events):
    async def mock_load(specified_dir_name=None, ignore_version_check=False):
        del ignore_version_check
        events.append(("load", specified_dir_name or TEST_PLUGIN_DIR))
        return True, ""

    return mock_load


def _build_reload_mock(events):
    async def mock_reload(specified_dir_name=None, **_kwargs):
        events.append(("reload", specified_dir_name or TEST_PLUGIN_DIR))
        return True, ""

    return mock_reload


def _build_dependency_install_mock(
    events,
    fail: bool,
    *,
    capture_content: bool = False,
):
    async def mock_install_requirements(
        *,
        requirements_path: str | None = None,
        package_name: str | None = None,
        **kwargs,
    ):
        del kwargs
        if requirements_path:
            path = Path(requirements_path)
            event = ("deps", str(path))
            if capture_content:
                event = (*event, path.read_text(encoding="utf-8"))
            events.append(event)
        if package_name:
            events.append(("deps_pkg", package_name))
        if fail:
            raise Exception("pip failed")

    return mock_install_requirements


def _mock_missing_requirements(monkeypatch, missing: set[str]):
    _mock_missing_requirements_plan(monkeypatch, missing, sorted(missing))


def _mock_missing_requirements_plan(
    monkeypatch,
    missing_names,
    install_lines,
    *,
    version_mismatch_names=(),
    fallback_reason: str | None = None,
):
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: MissingRequirementsPlan(
            missing_names=frozenset(missing_names),
            version_mismatch_names=frozenset(version_mismatch_names),
            install_lines=tuple(install_lines),
            fallback_reason=fallback_reason,
        ),
    )


def _mock_precheck_fails(monkeypatch):
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: None,
    )


def _assert_dependency_install_event_matches(
    event,
    *,
    expected_original_path: Path,
    expected_content: str | None = None,
    expect_filtered_tempfile: bool | None = None,
):
    assert event[0] == "deps"
    used_path = Path(event[1])
    should_be_filtered = expected_content is not None
    if expect_filtered_tempfile is not None:
        should_be_filtered = expect_filtered_tempfile

    if not should_be_filtered:
        assert used_path == expected_original_path
    else:
        assert used_path != expected_original_path
        assert used_path.name.endswith("_plugin_requirements.txt")
    if expected_content is not None:
        if len(event) >= 3:
            assert event[2] == expected_content


# --- Fixtures ---


@pytest.fixture
def plugin_manager_pm(tmp_path, monkeypatch):
    """Provides a fully isolated PluginManager instance for testing."""
    # Clear module cache before setup to ensure isolation
    _clear_module_cache()

    plugin_dir = tmp_path / "astrbot_root" / "data" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    class MockContext:
        def __init__(self):
            self.stars = []

        def get_all_stars(self):
            return self.stars

        def get_registered_star(self, name):
            for s in self.stars:
                if s.root_dir_name == name or s.name == name:
                    return s
            return None

    mock_context = MockContext()
    mock_config = {}
    pm = PluginManager(cast(Any, mock_context), cast(Any, mock_config))

    # Patch paths to use tmp_path
    monkeypatch.setattr(pm, "plugin_store_path", str(plugin_dir))
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.get_astrbot_plugin_path",
        lambda: str(plugin_dir),
    )

    return pm


@pytest.fixture
def local_updator(plugin_manager_pm):
    """Helper to setup a local plugin directory simulating a download."""
    path = Path(plugin_manager_pm.plugin_store_path) / TEST_PLUGIN_DIR
    _write_local_test_plugin(path, TEST_PLUGIN_REPO)
    return path


# --- Tests ---


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency_install_fails", [False, True])
async def test_install_plugin_dependency_install_flow(
    plugin_manager_pm: PluginManager, monkeypatch, dependency_install_fails: bool
):
    plugin_path = Path(plugin_manager_pm.plugin_store_path) / TEST_PLUGIN_DIR
    events = []
    _mock_missing_requirements(monkeypatch, {"networkx"})

    async def mock_install(repo_url: str, proxy=""):
        assert repo_url == TEST_PLUGIN_REPO
        _write_local_test_plugin(plugin_path, repo_url)
        _write_requirements(plugin_path)
        return str(plugin_path)

    monkeypatch.setattr(plugin_manager_pm.updator, "install", mock_install)
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, dependency_install_fails),
    )

    def mock_load_and_register(*args, **kwargs):
        cast(Any, plugin_manager_pm.context).stars.append(MockStar())
        return _build_load_mock(events)(*args, **kwargs)

    monkeypatch.setattr(plugin_manager_pm, "load", mock_load_and_register)

    if dependency_install_fails:
        with pytest.raises(PluginDependencyInstallError, match="pip failed"):
            await plugin_manager_pm.install_plugin(TEST_PLUGIN_REPO)
        assert len(events) == 1
        _assert_dependency_install_event_matches(
            events[0],
            expected_original_path=plugin_path / "requirements.txt",
            expected_content="networkx\n",
        )
    else:
        await plugin_manager_pm.install_plugin(TEST_PLUGIN_REPO)
        assert len(events) == 2
        _assert_dependency_install_event_matches(
            events[0],
            expected_original_path=plugin_path / "requirements.txt",
            expected_content="networkx\n",
        )
        assert events[1] == ("load", TEST_PLUGIN_DIR)


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency_install_fails", [False, True])
async def test_install_plugin_from_file_dependency_install_flow(
    plugin_manager_pm: PluginManager,
    monkeypatch,
    tmp_path,
    dependency_install_fails: bool,
):
    zip_file_path = tmp_path / f"{TEST_PLUGIN_DIR}.zip"
    zip_file_path.write_text("placeholder", encoding="utf-8")
    events = []
    _mock_missing_requirements(monkeypatch, {"networkx"})

    def mock_unzip_file(zip_path: str, target_dir: str) -> None:
        assert zip_path == str(zip_file_path)
        plugin_path = Path(target_dir)
        _write_local_test_plugin(plugin_path, TEST_PLUGIN_REPO)
        _write_requirements(plugin_path)

    monkeypatch.setattr(plugin_manager_pm.updator, "unzip_file", mock_unzip_file)
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, dependency_install_fails),
    )

    def mock_load_and_register(*args, **kwargs):
        cast(Any, plugin_manager_pm.context).stars.append(MockStar())
        return _build_load_mock(events)(*args, **kwargs)

    monkeypatch.setattr(plugin_manager_pm, "load", mock_load_and_register)

    if dependency_install_fails:
        with pytest.raises(PluginDependencyInstallError, match="pip failed"):
            await plugin_manager_pm.install_plugin_from_file(str(zip_file_path))
        assert any(e[0] == "deps" for e in events)
    else:
        await plugin_manager_pm.install_plugin_from_file(str(zip_file_path))
        assert any(e[0] == "deps" for e in events)
        assert ("load", TEST_PLUGIN_DIR) in events


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency_install_fails", [False, True])
async def test_reload_failed_plugin_dependency_install_flow(
    plugin_manager_pm: PluginManager,
    local_updator: Path,
    monkeypatch,
    dependency_install_fails: bool,
):
    _write_requirements(local_updator)
    plugin_manager_pm.failed_plugin_dict[TEST_PLUGIN_DIR] = {"error": "init fail"}
    events = []
    _mock_missing_requirements(monkeypatch, {"networkx"})

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, dependency_install_fails),
    )

    def mock_load_and_register(*args, **kwargs):
        cast(Any, plugin_manager_pm.context).stars.append(MockStar())
        return _build_load_mock(events)(*args, **kwargs)

    monkeypatch.setattr(plugin_manager_pm, "load", mock_load_and_register)

    if dependency_install_fails:
        with pytest.raises(PluginDependencyInstallError, match="pip failed"):
            await plugin_manager_pm.reload_failed_plugin(TEST_PLUGIN_DIR)
        assert len(events) == 1
        _assert_dependency_install_event_matches(
            events[0],
            expected_original_path=local_updator / "requirements.txt",
            expected_content="networkx\n",
        )
    else:
        await plugin_manager_pm.reload_failed_plugin(TEST_PLUGIN_DIR)
        assert len(events) == 2
        _assert_dependency_install_event_matches(
            events[0],
            expected_original_path=local_updator / "requirements.txt",
            expected_content="networkx\n",
        )
        assert events[1] == ("load", TEST_PLUGIN_DIR)


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_reraises_cancelled_error(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    _write_requirements(local_updator)
    _mock_missing_requirements(monkeypatch, {"networkx"})

    async def mock_install_requirements(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        mock_install_requirements,
    )

    with pytest.raises(asyncio.CancelledError):
        await plugin_manager_pm._ensure_plugin_requirements(
            str(local_updator),
            TEST_PLUGIN_DIR,
        )


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_wraps_generic_dependency_install_failure(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    _write_requirements(local_updator)
    _mock_missing_requirements(monkeypatch, {"networkx"})

    async def mock_install_requirements(*args, **kwargs):
        raise RuntimeError("pip failed")

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        mock_install_requirements,
    )

    with pytest.raises(PluginDependencyInstallError, match="pip failed") as exc_info:
        await plugin_manager_pm._ensure_plugin_requirements(
            str(local_updator),
            TEST_PLUGIN_DIR,
        )

    assert exc_info.value.plugin_label == TEST_PLUGIN_DIR
    assert exc_info.value.requirements_path == str(local_updator / "requirements.txt")
    assert isinstance(exc_info.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_wraps_pip_install_error(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    _write_requirements(local_updator)
    _mock_missing_requirements(monkeypatch, {"networkx"})

    async def mock_install_requirements(*args, **kwargs):
        raise PipInstallError("install failed", code=2)

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        mock_install_requirements,
    )

    with pytest.raises(
        PluginDependencyInstallError, match="install failed"
    ) as exc_info:
        await plugin_manager_pm._ensure_plugin_requirements(
            str(local_updator),
            TEST_PLUGIN_DIR,
        )

    assert isinstance(exc_info.value.__cause__, PipInstallError)


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_logs_requirements_file_install_for_missing_dependencies(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    _write_requirements(local_updator)
    _mock_missing_requirements(monkeypatch, {"networkx"})
    logged_lines = []

    async def mock_install_requirements(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        mock_install_requirements,
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.logger.info",
        lambda line, *args: logged_lines.append(line % args if args else line),
    )

    await plugin_manager_pm._ensure_plugin_requirements(
        str(local_updator),
        TEST_PLUGIN_DIR,
    )

    assert any("按 requirements.txt 安装" in line for line in logged_lines)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version_mismatch_names", "expected_allow_target_upgrade"),
    [
        (set(), False),
        ({"networkx"}, True),
    ],
)
async def test_ensure_plugin_requirements_sets_target_upgrade_based_on_version_mismatch(
    plugin_manager_pm: PluginManager,
    local_updator: Path,
    monkeypatch,
    version_mismatch_names,
    expected_allow_target_upgrade: bool,
):
    _write_requirements(local_updator)
    _mock_missing_requirements_plan(
        monkeypatch,
        {"networkx"},
        ["networkx"],
        version_mismatch_names=version_mismatch_names,
    )
    observed_calls = []

    async def mock_install_requirements(*args, **kwargs):
        observed_calls.append(kwargs)

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        mock_install_requirements,
    )

    await plugin_manager_pm._ensure_plugin_requirements(
        str(local_updator),
        TEST_PLUGIN_DIR,
    )

    assert len(observed_calls) == 1
    assert observed_calls[0]["allow_target_upgrade"] is expected_allow_target_upgrade


@pytest.mark.asyncio
async def test_import_plugin_prefers_installed_dependencies_before_first_import(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("networkx\n", encoding="utf-8")
    events = []
    sentinel_module = object()

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.prefer_installed_dependencies",
        lambda *, requirements_path: events.append(("prefer", requirements_path)),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: MissingRequirementsPlan(
            missing_names=frozenset(),
            install_lines=(),
            version_mismatch_names=frozenset(),
        ),
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        del globals, locals, level
        events.append(("import", name, tuple(fromlist)))
        return sentinel_module

    monkeypatch.setattr(star_manager_module, "__import__", fake_import, raising=False)

    imported_module = await plugin_manager_pm._import_plugin_with_dependency_recovery(
        path="data.plugins.helloworld.main",
        module_str="main",
        root_dir_name=TEST_PLUGIN_DIR,
        requirements_path=str(requirements_path),
    )

    assert imported_module is sentinel_module
    assert events == [
        ("prefer", str(requirements_path)),
        ("import", "data.plugins.helloworld.main", ("main",)),
    ]


@pytest.mark.asyncio
async def test_import_reserved_plugin_skips_preloading_user_site_dependencies(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("networkx\n", encoding="utf-8")
    events = []
    sentinel_module = object()

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.prefer_installed_dependencies",
        lambda *, requirements_path: events.append(("prefer", requirements_path)),
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        del globals, locals, level
        events.append(("import", name, tuple(fromlist)))
        return sentinel_module

    monkeypatch.setattr(star_manager_module, "__import__", fake_import, raising=False)

    imported_module = await plugin_manager_pm._import_plugin_with_dependency_recovery(
        path="astrbot.builtin_stars.web_searcher.main",
        module_str="main",
        root_dir_name="web_searcher",
        requirements_path=str(requirements_path),
        reserved=True,
    )

    assert imported_module is sentinel_module
    assert events == [
        ("import", "astrbot.builtin_stars.web_searcher.main", ("main",)),
    ]


@pytest.mark.asyncio
async def test_import_plugin_skips_preloading_when_requirements_version_mismatch_detected(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("networkx>=3\n", encoding="utf-8")
    events = []
    sentinel_module = object()

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.prefer_installed_dependencies",
        lambda *, requirements_path: events.append(("prefer", requirements_path)),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: MissingRequirementsPlan(
            missing_names=frozenset({"networkx"}),
            install_lines=("networkx>=3",),
            version_mismatch_names=frozenset({"networkx"}),
        ),
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        del globals, locals, level
        events.append(("import", name, tuple(fromlist)))
        return sentinel_module

    monkeypatch.setattr(star_manager_module, "__import__", fake_import, raising=False)

    imported_module = await plugin_manager_pm._import_plugin_with_dependency_recovery(
        path="data.plugins.helloworld.main",
        module_str="main",
        root_dir_name=TEST_PLUGIN_DIR,
        requirements_path=str(requirements_path),
    )

    assert imported_module is sentinel_module
    assert events == [
        ("import", "data.plugins.helloworld.main", ("main",)),
    ]


@pytest.mark.asyncio
async def test_import_plugin_reinstalls_when_version_mismatch_import_fails(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("networkx>=3\n", encoding="utf-8")
    events = []
    sentinel_module = object()
    import_attempts = {"count": 0}

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.prefer_installed_dependencies",
        lambda *, requirements_path: events.append(("prefer", requirements_path)),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: MissingRequirementsPlan(
            missing_names=frozenset({"networkx"}),
            install_lines=("networkx>=3",),
            version_mismatch_names=frozenset({"networkx"}),
        ),
    )

    async def mock_check_plugin_dept_update(*, target_plugin=None):
        events.append(("reinstall", target_plugin))

    monkeypatch.setattr(
        plugin_manager_pm,
        "_check_plugin_dept_update",
        mock_check_plugin_dept_update,
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        del globals, locals, level
        import_attempts["count"] += 1
        events.append(("import", name, tuple(fromlist), import_attempts["count"]))
        if import_attempts["count"] == 1:
            raise ModuleNotFoundError("networkx")
        return sentinel_module

    monkeypatch.setattr(star_manager_module, "__import__", fake_import, raising=False)

    imported_module = await plugin_manager_pm._import_plugin_with_dependency_recovery(
        path="data.plugins.helloworld.main",
        module_str="main",
        root_dir_name=TEST_PLUGIN_DIR,
        requirements_path=str(requirements_path),
    )

    assert imported_module is sentinel_module
    assert events == [
        ("import", "data.plugins.helloworld.main", ("main",), 1),
        ("reinstall", TEST_PLUGIN_DIR),
        ("import", "data.plugins.helloworld.main", ("main",), 2),
    ]


@pytest.mark.asyncio
async def test_import_plugin_skips_preloading_when_requirement_precheck_is_unavailable(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("networkx\n", encoding="utf-8")
    events = []
    sentinel_module = object()

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.prefer_installed_dependencies",
        lambda *, requirements_path: events.append(("prefer", requirements_path)),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: None,
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        del globals, locals, level
        events.append(("import", name, tuple(fromlist)))
        return sentinel_module

    monkeypatch.setattr(star_manager_module, "__import__", fake_import, raising=False)

    imported_module = await plugin_manager_pm._import_plugin_with_dependency_recovery(
        path="data.plugins.helloworld.main",
        module_str="main",
        root_dir_name=TEST_PLUGIN_DIR,
        requirements_path=str(requirements_path),
    )

    assert imported_module is sentinel_module
    assert events == [
        ("import", "data.plugins.helloworld.main", ("main",)),
    ]


@pytest.mark.asyncio
async def test_import_plugin_attempts_dependency_recovery_when_precheck_is_unavailable(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("networkx\n", encoding="utf-8")
    events = []
    sentinel_module = object()
    import_attempts = {"count": 0}

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.prefer_installed_dependencies",
        lambda *, requirements_path: events.append(("prefer", requirements_path)),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: None,
    )

    async def unexpected_check_plugin_dept_update(*args, **kwargs):
        raise AssertionError("dependency install fallback should not run")

    monkeypatch.setattr(
        plugin_manager_pm,
        "_check_plugin_dept_update",
        unexpected_check_plugin_dept_update,
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        del globals, locals, level
        import_attempts["count"] += 1
        events.append(("import", name, tuple(fromlist), import_attempts["count"]))
        if import_attempts["count"] == 1:
            raise ModuleNotFoundError("networkx")
        return sentinel_module

    monkeypatch.setattr(star_manager_module, "__import__", fake_import, raising=False)

    imported_module = await plugin_manager_pm._import_plugin_with_dependency_recovery(
        path="data.plugins.helloworld.main",
        module_str="main",
        root_dir_name=TEST_PLUGIN_DIR,
        requirements_path=str(requirements_path),
    )

    assert imported_module is sentinel_module
    assert events == [
        ("import", "data.plugins.helloworld.main", ("main",), 1),
        ("prefer", str(requirements_path)),
        ("import", "data.plugins.helloworld.main", ("main",), 2),
    ]


@pytest.mark.asyncio
async def test_import_plugin_does_not_recover_from_plain_import_error(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("networkx\n", encoding="utf-8")
    events = []

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.prefer_installed_dependencies",
        lambda *, requirements_path: events.append(("prefer", requirements_path)),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: MissingRequirementsPlan(
            missing_names=frozenset(),
            install_lines=(),
            version_mismatch_names=frozenset(),
        ),
    )

    async def unexpected_check_plugin_dept_update(*args, **kwargs):
        raise AssertionError("dependency install fallback should not run")

    monkeypatch.setattr(
        plugin_manager_pm,
        "_check_plugin_dept_update",
        unexpected_check_plugin_dept_update,
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        del globals, locals, level
        events.append(("import", name, tuple(fromlist)))
        raise ImportError("plugin import error")

    monkeypatch.setattr(star_manager_module, "__import__", fake_import, raising=False)

    with pytest.raises(ImportError, match="plugin import error"):
        await plugin_manager_pm._import_plugin_with_dependency_recovery(
            path="data.plugins.helloworld.main",
            module_str="main",
            root_dir_name=TEST_PLUGIN_DIR,
            requirements_path=str(requirements_path),
        )

    assert events == [
        ("prefer", str(requirements_path)),
        ("import", "data.plugins.helloworld.main", ("main",)),
    ]


@pytest.mark.asyncio
async def test_import_plugin_surfaces_unexpected_recovery_errors(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("networkx\n", encoding="utf-8")
    events = []

    def raising_prefer_installed_dependencies(*, requirements_path):
        events.append(("prefer", requirements_path))
        raise RuntimeError("unexpected recovery failure")

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.prefer_installed_dependencies",
        raising_prefer_installed_dependencies,
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda requirements_path: None,
    )

    async def unexpected_check_plugin_dept_update(*args, **kwargs):
        raise AssertionError("dependency install fallback should not run")

    monkeypatch.setattr(
        plugin_manager_pm,
        "_check_plugin_dept_update",
        unexpected_check_plugin_dept_update,
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        del globals, locals, level
        events.append(("import", name, tuple(fromlist)))
        raise ModuleNotFoundError("networkx")

    monkeypatch.setattr(star_manager_module, "__import__", fake_import, raising=False)

    with pytest.raises(RuntimeError, match="unexpected recovery failure"):
        await plugin_manager_pm._import_plugin_with_dependency_recovery(
            path="data.plugins.helloworld.main",
            module_str="main",
            root_dir_name=TEST_PLUGIN_DIR,
            requirements_path=str(requirements_path),
        )

    assert events == [
        ("import", "data.plugins.helloworld.main", ("main",)),
        ("prefer", str(requirements_path)),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency_install_fails", [False, True])
async def test_update_plugin_dependency_install_flow(
    plugin_manager_pm: PluginManager,
    local_updator: Path,
    monkeypatch,
    dependency_install_fails: bool,
):
    mock_star = MockStar()
    cast(Any, plugin_manager_pm.context).stars.append(mock_star)

    _write_requirements(local_updator)
    events = []
    _mock_missing_requirements(monkeypatch, {"networkx"})

    async def mock_update(plugin, proxy="", download_url=""):
        del proxy, download_url
        events.append(("update", plugin.name))

    monkeypatch.setattr(plugin_manager_pm.updator, "update", mock_update)
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, dependency_install_fails),
    )
    monkeypatch.setattr(
        plugin_manager_pm,
        "_reload_locked",
        _build_reload_mock(events),
    )

    if dependency_install_fails:
        with pytest.raises(PluginDependencyInstallError, match="pip failed"):
            await plugin_manager_pm.update_plugin(TEST_PLUGIN_NAME)
        dep_event = next(event for event in events if event[0] == "deps")
        _assert_dependency_install_event_matches(
            dep_event,
            expected_original_path=local_updator / "requirements.txt",
            expected_content="networkx\n",
        )
    else:
        await plugin_manager_pm.update_plugin(TEST_PLUGIN_NAME)
        dep_event = next(event for event in events if event[0] == "deps")
        _assert_dependency_install_event_matches(
            dep_event,
            expected_original_path=local_updator / "requirements.txt",
            expected_content="networkx\n",
        )
        assert ("reload", TEST_PLUGIN_DIR) in events


@pytest.mark.asyncio
async def test_install_plugin_skips_dependency_install_when_no_requirements_missing(
    plugin_manager_pm: PluginManager, monkeypatch
):
    plugin_path = Path(plugin_manager_pm.plugin_store_path) / TEST_PLUGIN_DIR
    events = []
    _mock_missing_requirements(monkeypatch, set())

    async def mock_install(repo_url: str, proxy=""):
        _write_local_test_plugin(plugin_path, repo_url)
        _write_requirements(plugin_path)
        return str(plugin_path)

    monkeypatch.setattr(plugin_manager_pm.updator, "install", mock_install)
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, False),
    )

    def mock_load_and_register(*args, **kwargs):
        cast(Any, plugin_manager_pm.context).stars.append(MockStar())
        return _build_load_mock(events)(*args, **kwargs)

    monkeypatch.setattr(plugin_manager_pm, "load", mock_load_and_register)

    await plugin_manager_pm.install_plugin(TEST_PLUGIN_REPO)

    assert "deps" not in [e[0] for e in events]
    assert ("load", TEST_PLUGIN_DIR) in events


@pytest.mark.asyncio
async def test_install_plugin_runs_dependency_install_when_precheck_fails(
    plugin_manager_pm: PluginManager, monkeypatch
):
    plugin_path = Path(plugin_manager_pm.plugin_store_path) / TEST_PLUGIN_DIR
    events = []

    async def mock_install(repo_url: str, proxy=""):
        _write_local_test_plugin(plugin_path, repo_url)
        _write_requirements(plugin_path)
        return str(plugin_path)

    _mock_precheck_fails(monkeypatch)
    monkeypatch.setattr(plugin_manager_pm.updator, "install", mock_install)
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, False),
    )

    def mock_load_and_register(*args, **kwargs):
        cast(Any, plugin_manager_pm.context).stars.append(MockStar())
        return _build_load_mock(events)(*args, **kwargs)

    monkeypatch.setattr(plugin_manager_pm, "load", mock_load_and_register)

    await plugin_manager_pm.install_plugin(TEST_PLUGIN_REPO)

    dep_event = next(event for event in events if event[0] == "deps")
    _assert_dependency_install_event_matches(
        dep_event,
        expected_original_path=plugin_path / "requirements.txt",
    )
    assert ("load", TEST_PLUGIN_DIR) in events


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_installs_only_missing_requirement_lines(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text(
        "aiohttp>=3.0\nboto3==1.2\nbotocore\n",
        encoding="utf-8",
    )
    events = []
    _mock_missing_requirements_plan(
        monkeypatch, {"boto3", "botocore"}, ["boto3==1.2", "botocore"]
    )

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, False, capture_content=True),
    )

    await plugin_manager_pm._ensure_plugin_requirements(
        str(local_updator),
        TEST_PLUGIN_DIR,
    )

    assert len(events) == 1
    kind, used_path, content = events[0]
    assert kind == "deps"
    assert used_path != str(requirements_path)
    assert content == "boto3==1.2\nbotocore\n"
    assert not Path(used_path).exists()


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_creates_temp_dir_before_filtered_install(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch, tmp_path
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("boto3\n", encoding="utf-8")
    temp_dir = tmp_path / "missing-temp-dir"
    events = []
    _mock_missing_requirements_plan(monkeypatch, {"boto3"}, ["boto3"])

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.get_astrbot_temp_path",
        lambda: str(temp_dir),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, False, capture_content=True),
    )

    await plugin_manager_pm._ensure_plugin_requirements(
        str(local_updator),
        TEST_PLUGIN_DIR,
    )

    assert temp_dir.is_dir()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_falls_back_when_missing_names_have_no_install_lines(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("boto3\n", encoding="utf-8")
    events = []

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda path: MissingRequirementsPlan(
            missing_names=frozenset({"botocore"}),
            install_lines=(),
            fallback_reason="unmapped missing requirement names",
        ),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        _build_dependency_install_mock(events, False),
    )

    await plugin_manager_pm._ensure_plugin_requirements(
        str(local_updator),
        TEST_PLUGIN_DIR,
    )

    assert events == [("deps", str(requirements_path))]


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_fallback_full_install_keeps_upgrade_for_version_mismatch(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("boto3>=2\n", encoding="utf-8")
    observed_calls = []

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.plan_missing_requirements_install",
        lambda path: MissingRequirementsPlan(
            missing_names=frozenset({"boto3"}),
            install_lines=(),
            version_mismatch_names=frozenset({"boto3"}),
            fallback_reason="unmapped missing requirement names",
        ),
    )

    async def mock_install_requirements(*args, **kwargs):
        observed_calls.append(kwargs)

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        mock_install_requirements,
    )

    await plugin_manager_pm._ensure_plugin_requirements(
        str(local_updator),
        TEST_PLUGIN_DIR,
    )

    assert len(observed_calls) == 1
    assert observed_calls[0]["requirements_path"] == str(requirements_path)
    assert observed_calls[0]["allow_target_upgrade"] is True


@pytest.mark.asyncio
async def test_ensure_plugin_requirements_does_not_mask_install_error_when_cleanup_fails(
    plugin_manager_pm: PluginManager, local_updator: Path, monkeypatch, tmp_path
):
    requirements_path = local_updator / "requirements.txt"
    requirements_path.write_text("boto3\n", encoding="utf-8")
    temp_dir = tmp_path / "cleanup-fails"
    _mock_missing_requirements_plan(monkeypatch, {"boto3"}, ["boto3"])
    warning_logs = []

    async def mock_install_requirements(
        *, requirements_path: str | None = None, **kwargs
    ):
        del kwargs, requirements_path
        raise RuntimeError("pip failed")

    original_remove = os.remove

    def flaky_remove(path):
        if str(path).endswith("_plugin_requirements.txt"):
            raise OSError("cleanup failed")
        return original_remove(path)

    monkeypatch.setattr(
        "astrbot.core.star.star_manager.get_astrbot_temp_path",
        lambda: str(temp_dir),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.pip_installer.install",
        mock_install_requirements,
    )
    monkeypatch.setattr("astrbot.core.star.star_manager.os.remove", flaky_remove)
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.logger.warning",
        lambda line, *args: warning_logs.append(line % args if args else line),
    )

    with pytest.raises(PluginDependencyInstallError, match="pip failed"):
        await plugin_manager_pm._ensure_plugin_requirements(
            str(local_updator),
            TEST_PLUGIN_DIR,
        )

    assert any("删除临时插件依赖文件失败" in log for log in warning_logs)


@pytest.mark.asyncio
async def test_reload_deactivated_plugin_preserves_registered_tools(
    plugin_manager_pm: PluginManager, monkeypatch
):
    plugin_name = "demo_plugin"
    module_path = f"data.plugins.{plugin_name}.main"
    metadata = star_manager_module.StarMetadata(
        name=plugin_name,
        root_dir_name=plugin_name,
        module_path=module_path,
        activated=False,
    )
    plugin_tool = FunctionTool(
        name="plugin_search",
        description="plugin search",
        parameters={"type": "object", "properties": {}},
        handler_module_path=f"{module_path}.tools.search",
    )

    original_star_map = dict(star_manager_module.star_map)
    original_star_registry = list(star_manager_module.star_registry)
    original_func_list = llm_tools.func_list[:]
    star_manager_module.star_map.clear()
    star_manager_module.star_registry.clear()
    star_manager_module.star_map[module_path] = metadata
    star_manager_module.star_registry.append(metadata)
    llm_tools.func_list = [plugin_tool]

    async def mock_terminate(_smd):
        return None

    async def mock_load(specified_module_path=None, **_kwargs):
        assert specified_module_path == module_path
        return True, None

    monkeypatch.setattr(plugin_manager_pm, "_terminate_plugin", mock_terminate)
    monkeypatch.setattr(plugin_manager_pm, "load", mock_load)

    try:
        await plugin_manager_pm.reload(plugin_name)
        assert plugin_tool in llm_tools.func_list
    finally:
        star_manager_module.star_map.clear()
        star_manager_module.star_map.update(original_star_map)
        star_manager_module.star_registry.clear()
        star_manager_module.star_registry.extend(original_star_registry)
        llm_tools.func_list = original_func_list


@pytest.mark.asyncio
async def test_reload_activated_plugin_still_unbinds(
    plugin_manager_pm: PluginManager, monkeypatch
):
    plugin_name = "demo_plugin"
    module_path = f"data.plugins.{plugin_name}.main"
    metadata = star_manager_module.StarMetadata(
        name=plugin_name,
        root_dir_name=plugin_name,
        module_path=module_path,
        activated=True,
    )
    unbound = []

    original_star_map = dict(star_manager_module.star_map)
    original_star_registry = list(star_manager_module.star_registry)
    star_manager_module.star_map.clear()
    star_manager_module.star_registry.clear()
    star_manager_module.star_map[module_path] = metadata
    star_manager_module.star_registry.append(metadata)

    async def mock_terminate(_smd):
        return None

    async def mock_unbind(name, path):
        unbound.append((name, path))

    async def mock_load(specified_module_path=None, **_kwargs):
        assert specified_module_path == module_path
        return True, None

    monkeypatch.setattr(plugin_manager_pm, "_terminate_plugin", mock_terminate)
    monkeypatch.setattr(plugin_manager_pm, "_unbind_plugin_after_drain", mock_unbind)
    monkeypatch.setattr(plugin_manager_pm, "load", mock_load)

    try:
        await plugin_manager_pm.reload(plugin_name)
        assert unbound == [(plugin_name, module_path)]
    finally:
        star_manager_module.star_map.clear()
        star_manager_module.star_map.update(original_star_map)
        star_manager_module.star_registry.clear()
        star_manager_module.star_registry.extend(original_star_registry)


@pytest.mark.asyncio
async def test_repeated_plugin_loads_bind_current_instance_once(
    plugin_manager_pm: PluginManager, monkeypatch
):
    plugin_name = "demo_plugin"
    module_path = f"data.plugins.{plugin_name}.main"

    class DemoPlugin:
        def __init__(self, context):
            self.context = context
            self.initialize_count = 0

        async def initialize(self):
            self.initialize_count += 1

    stale_plugin = DemoPlugin(plugin_manager_pm.context)
    metadata = star_manager_module.StarMetadata(
        name=plugin_name,
        author="AstrBot Team",
        desc="Demo plugin",
        version="1.0.0",
        root_dir_name=plugin_name,
        module_path=module_path,
        star_cls_type=cast(Any, DemoPlugin),
        star_cls=cast(Any, stale_plugin),
        activated=False,
    )

    async def raw_event_handler(plugin, event):
        return plugin, event

    async def raw_tool_handler(plugin, query):
        return plugin, query

    raw_event_handler.__module__ = module_path
    raw_tool_handler.__module__ = module_path
    event_handler = StarHandlerMetadata(
        event_type=EventType.AdapterMessageEvent,
        handler_full_name=f"{module_path}_raw_event_handler",
        handler_name="raw_event_handler",
        handler_module_path=module_path,
        handler=functools.partial(raw_event_handler, stale_plugin),
        event_filters=[],
    )
    plugin_tool = FunctionTool(
        name="plugin_search",
        description="plugin search",
        parameters={"type": "object", "properties": {}},
        handler=functools.partial(raw_tool_handler, stale_plugin),
        handler_module_path=module_path,
    )

    original_star_map = dict(star_manager_module.star_map)
    original_star_registry = list(star_manager_module.star_registry)
    original_handlers = list(star_manager_module.star_handlers_registry)
    original_tools = llm_tools.func_list
    original_context_stars = list(cast(Any, plugin_manager_pm.context).stars)
    preferences = {
        "inactivated_plugins": [module_path],
        "inactivated_llm_tools": [plugin_tool.name],
        "alter_cmd": {},
    }

    async def mock_global_get(key, default=None):
        return preferences.get(key, default)

    async def mock_global_put(key, value):
        preferences[key] = value

    async def mock_import_plugin_with_dependency_recovery(
        path,
        module_str,
        root_dir_name,
        requirements_path,
        *,
        reserved=False,
    ):
        del module_str, root_dir_name, requirements_path, reserved
        assert path == module_path
        return ModuleType(module_path)

    async def mock_sync_command_configs():
        return None

    star_manager_module.star_map.clear()
    star_manager_module.star_registry.clear()
    star_manager_module.star_handlers_registry.clear()
    cast(Any, plugin_manager_pm.context).stars[:] = [metadata]
    star_manager_module.star_map[module_path] = metadata
    star_manager_module.star_registry.append(metadata)
    star_manager_module.star_handlers_registry.append(event_handler)
    llm_tools.func_list = [plugin_tool]

    monkeypatch.setattr(star_manager_module.sp, "global_get", mock_global_get)
    monkeypatch.setattr(star_manager_module.sp, "global_put", mock_global_put)
    monkeypatch.setattr(
        plugin_manager_pm,
        "_get_plugin_modules",
        lambda: [{"pname": plugin_name, "module": "main"}],
    )
    monkeypatch.setattr(
        plugin_manager_pm,
        "_import_plugin_with_dependency_recovery",
        mock_import_plugin_with_dependency_recovery,
    )
    monkeypatch.setattr(plugin_manager_pm, "_load_plugin_metadata", lambda **_: None)
    monkeypatch.setattr(
        star_manager_module,
        "sync_command_configs",
        mock_sync_command_configs,
    )

    try:
        for _ in range(2):
            success, error = await plugin_manager_pm.load(
                specified_module_path=module_path
            )
            assert success is True
            assert error is None
            assert event_handler.handler is raw_event_handler
            assert plugin_tool.handler is raw_tool_handler
            assert plugin_tool.active is False
            assert metadata.star_cls is None
            assert metadata.activated is False

        await plugin_manager_pm.turn_on_plugin(plugin_name)

        assert isinstance(event_handler.handler, functools.partial)
        assert event_handler.handler.func is raw_event_handler
        assert event_handler.handler.args == (metadata.star_cls,)
        assert isinstance(plugin_tool.handler, functools.partial)
        assert plugin_tool.handler.func is raw_tool_handler
        assert plugin_tool.handler.args == (metadata.star_cls,)
        assert plugin_tool.active is True
        assert metadata.activated is True
        assert metadata.star_cls.initialize_count == 1
        assert await event_handler.handler("event") == (metadata.star_cls, "event")
        assert await plugin_tool.handler("query") == (metadata.star_cls, "query")

        success, error = await plugin_manager_pm.load(
            specified_module_path=module_path
        )
        assert success is True
        assert error is None
        assert isinstance(event_handler.handler, functools.partial)
        assert event_handler.handler.func is raw_event_handler
        assert event_handler.handler.args == (metadata.star_cls,)
        assert isinstance(plugin_tool.handler, functools.partial)
        assert plugin_tool.handler.func is raw_tool_handler
        assert plugin_tool.handler.args == (metadata.star_cls,)
        assert metadata.star_cls.initialize_count == 1
    finally:
        llm_tools.func_list = original_tools
        cast(Any, plugin_manager_pm.context).stars[:] = original_context_stars
        star_manager_module.star_map.clear()
        star_manager_module.star_map.update(original_star_map)
        star_manager_module.star_registry.clear()
        star_manager_module.star_registry.extend(original_star_registry)
        star_manager_module.star_handlers_registry.clear()
        for handler in original_handlers:
            star_manager_module.star_handlers_registry.append(handler)
