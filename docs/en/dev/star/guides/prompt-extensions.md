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

Collectors run before target projection. A plugin fact can therefore be explicitly exposed to Persona or Core through `meta.targets`. Router and Core Planner do not mount plugin extensions or plugin capability directories; they consume only core-owned routing facts. Collectors never receive those models' decisions and cannot mutate the canonical pack.

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

Valid plugin targets are `persona` and `core`. A regular extension without targets defaults to Core only. Router and Core Planner do not accept plugin extensions or plugin capability directories. Facts needed by those control-plane models must be provided by explicitly trusted, core-owned collectors; setting `official_context` in a plugin does not grant that permission.

## Lifecycle and Failures

- `dynamic`: collected on every Prompt build.
- `static`: a successful result may be reused only for the same event, config object, and `ProviderRequest` object.

`static` is not a cross-message, session, or global cache. Group context, user state, and device availability must remain dynamic.

A failing plugin collector is logged and skipped so one plugin cannot break core Prompt collection. Do not return tracebacks, stale failures, or diagnostics as model facts.

## Interaction Latency Boundary

An Interaction turn first builds base facts shared by Router, Planner, Persona, and
Core, then constructs a Persona/Core-only plugin-enrichment pack in the background.
Router and Planner never read ordinary plugin extensions. Persona uses enrichment
only when it is already ready; otherwise it produces the first reply from base facts.
Core waits for and reuses the same enrichment result only when execution is needed.

Prompt Extensions are therefore best-effort enhancements, not hard dependencies for
the current first reply, Router decision, or message takeover. A plugin that must
stop, take over, or alter handling of the current message must use an official
Pipeline Handler. Do not use a collector to send messages, execute tools, or wait on
slow external control work.

## Related APIs

| API | Purpose |
|---|---|
| Prompt Extension Collector | Contribute model-visible facts through the unified pipeline |
| Persona Effect | Add structured presentation capabilities to Persona output |
| LLM Tool | Register executable capability; plugin tools default to Core and enter Persona only through an explicit declaration or user override |
| `on_llm_request` | Modify the pre-tool Persona request once, or the routed Core low-level request, based on plugin target |

Plugin LLM lifecycle and LLM Tool targets resolve independently. Lifecycle order is the `plugin_runtime_targets` override, class or legacy decorator declaration, then the Persona default. Tool order is the user `plugin_tool_targets` override, the tool's `tool_targets` declaration, then the Core default. Non-Interaction flows retain the official Core behavior. Persona `on_llm_request` runs once before its optional tool loop, and its non-contract mutations are retained for the final expression; it does not run for Router, Core Planner, or internal Persona tool calls. Actual Persona tool execution still emits `on_using_llm_tool` and `on_llm_tool_respond`. Facts needed by plugin-enabled targets must use Prompt Extensions with explicit `persona` or `core` targets; do not make per-turn dynamic facts depend on a low-level request hook.

## Safety Rules

- Do not return secrets, tokens, internal paths, or unnecessary user identifiers.
- Do not feed Router/Planner decisions or model output back into the same turn's facts.
- Do not send messages, write memory, or run side-effecting tools from a collector.
- Do not imitate executable tools with Prompt text; register real tools through the Tool API.
- Do not require generic Router patches for one plugin; describe only the plugin name and capability.

The Prompt system does not yet enforce every Catalog redaction declaration. Plugins must minimize and sanitize `value` before returning it.
