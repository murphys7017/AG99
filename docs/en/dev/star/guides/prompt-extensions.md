---
outline: deep
---

# Prompt Extensions

Prompt Extensions are the Yakumo-fork API for contributing model-visible facts to the unified Prompt pipeline. Use them for business state, concise platform capabilities, or event-scoped context. Do not use them to register executable tools, alter routing decisions, or send messages.

## Pipeline Position

```text
Plugin Collector
  -> PromptExtension
  -> ContextSlot / ContextPack
  -> target projection
  -> layout / renderer
  -> ProviderRequest
```

Collectors run before target projection. One fact can therefore be explicitly exposed to Router, Core Planner, Persona, or Core through `meta.targets`. Collectors never receive those models' decisions and cannot mutate the canonical pack.

## Register a Collector

```python
from astrbot.api.star import Context, Star
from astrbot.core.prompt import (
    PromptExtension,
    PromptExtensionCollectorInterface,
)


class RuntimeStatusCollector(PromptExtensionCollectorInterface):
    @property
    def plugin_id(self) -> str:
        return "my_plugin"

    @property
    def lifecycle(self) -> str:
        return "dynamic"

    async def collect(
        self,
        event,
        plugin_context,
        config,
        provider_request=None,
    ) -> list[PromptExtension]:
        return [
            PromptExtension(
                plugin_id=self.plugin_id,
                mount="context",
                title="Runtime status",
                value={"service_available": True},
                value_kind="mapping",
                meta={"targets": ["persona", "core"]},
            )
        ]


class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        context.register_prompt_extension_collector(RuntimeStatusCollector())
```

AstrBot removes owned registrations when the plugin is unloaded or hot-reloaded.

## Field Boundaries

The main `PromptExtension` fields are:

- `plugin_id`: a stable, non-empty ownership identifier.
- `mount`: `system`, `context`, `input`, `conversation`, `memory`, or `capability`.
- `title`: an optional human-readable title.
- `value`: the contributed fact.
- `value_kind`: `text`, `mapping`, or `sequence`.
- `order`: stable ordering within a mount; lower values come first.
- `meta.targets`: the model roles allowed to read this fact.

Valid targets are `router`, `core_planner`, `persona`, and `core`. A regular extension without targets defaults to Core only. Do not assume global visibility.

## Router and Planner Plugin Directory

When Router or Core Planner only needs to know which plugins exist and what they do, contribute a minimal `capability` directory:

```python
PromptExtension(
    plugin_id="my_plugin",
    mount="capability",
    value={
        "plugins": [
            {
                "name": "calendar",
                "description": "Reads and updates calendar events.",
            }
        ]
    },
    value_kind="mapping",
    meta={"targets": ["router", "core_planner"]},
)
```

Projection keeps only `name` and `description`. Do not include example conversations, low-level schemas, plugin IDs, diagnostics, or execution results.

## Lifecycle and Failures

- `dynamic`: collected on every Prompt build.
- `static`: a successful result may be reused only for the same event, config object, and `ProviderRequest` object.

`static` is not a cross-message, session, or global cache. Group context, user state, and device availability must remain dynamic.

A failing plugin collector is logged and skipped so one plugin cannot break core Prompt collection. Do not return tracebacks, stale failures, or diagnostics as model facts.

## Related APIs

| API | Purpose |
|---|---|
| Prompt Extension Collector | Contribute model-visible facts through the unified pipeline |
| Persona Effect | Add structured presentation capabilities to Persona output |
| LLM Tool | Register executable capability; plugin tools default to Persona and resolved `core` plugins enter Core |
| `on_llm_request` | Modify the final routed Persona or Core low-level request, based on plugin target |

The same runtime target applies to a plugin's full LLM lifecycle and its owned LLM Tools. In an Interaction turn, resolution is configuration override, class or legacy decorator declaration, then the Persona default; only a resolved `core` plugin enters Core. Non-Interaction flows retain the official Core behavior. `on_llm_request` does not run for Router, Core Planner, or internal Persona tool calls; actual Persona tool execution still emits `on_using_llm_tool` and `on_llm_tool_respond`. Facts needed by those targets must use Prompt Extensions with explicit targets; do not make per-turn dynamic facts depend on a low-level request hook.

## Safety Rules

- Do not return secrets, tokens, internal paths, or unnecessary user identifiers.
- Do not feed Router/Planner decisions or model output back into the same turn's facts.
- Do not send messages, write memory, or run side-effecting tools from a collector.
- Do not imitate executable tools with Prompt text; register real tools through the Tool API.
- Do not require generic Router patches for one plugin; describe only the plugin name and capability.

The Prompt system does not yet enforce every Catalog redaction declaration. Plugins must minimize and sanitize `value` before returning it.
