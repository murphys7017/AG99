from types import SimpleNamespace

from astrbot.core.plugin_capability_inventory import build_capability_inventory
from astrbot.core.star.context import Context, PluginOwnerScope


def test_capability_inventory_reports_registered_task_and_web_api_ownership():
    context = object.__new__(Context)
    context._prompt_extension_collectors = []
    context._interaction_result_contributors = []
    context._interaction_stream_deciders = []
    context._interaction_lifecycle_observers = []
    context._persona_effects = []
    context._runtime_observation_sensors = []
    owned_task = SimpleNamespace()
    unowned_task = SimpleNamespace()
    context._register_tasks = [owned_task, unowned_task]
    owner = PluginOwnerScope(module_path="data.plugins.demo.main", plugin_name="demo")
    context._registered_task_owners = {id(owned_task): owner}
    context.registered_web_apis = [("/owned", lambda: None, ["GET"], "")]
    context._registered_web_api_owners = {("/owned", ("GET",)): owner}

    inventory = build_capability_inventory(event=None, context=context)
    rows = [entry for plugin in inventory["plugins"] for entry in plugin["capabilities"]]
    tasks = [row for row in rows if row["kind"] == "global_task"]
    assert sorted(row["lifecycle_management"] for row in tasks) == [
        "owner_managed",
        "unowned",
    ]
    assert len({row["registration_id"] for row in tasks}) == 2
    api = next(row for row in rows if row["kind"] == "web_api")
    assert api["lifecycle_management"] == "owner_managed"
    assert api["item_name"] == "/owned"
