---
outline: deep
---

# Persona Effects

Persona Effects are a Yakumo-fork extension for structured persona output. A plugin can let Persona Runtime produce presentation intent, such as a Live2D motion, light state, or client expression, alongside `spoken_reply`.

A Persona Effect is not an Agent Tool. It never enters the Core Tool Loop and is never exposed to Router. Router only returns `silent`, `persona`, or `hybrid`; it does not register tools, request JSON, or generate `effect_calls`.

## Register an Effect

```python
from astrbot.api.star import Context, Star
from astrbot.core.interaction import PersonaEffectSpec


def supports_current_event(event) -> bool:
    return event.get_platform_name() == "my_platform"


class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        context.register_persona_effect(
            PersonaEffectSpec(
                plugin_id="my_plugin",
                name="my_plugin.expression",
                description="Select a client expression for the visible reply.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string"},
                    },
                    "required": ["label"],
                },
            ),
            event_filter=supports_current_event,
        )
```

`event_filter` must be synchronous and side-effect free. Core passes the current event whenever it builds a Persona output contract:

- `True`: the effect schema is included in this turn's `persona_expression` contract.
- `False`: the effect is hidden from the model and consumes no schema tokens for this turn.
- Exception: Core logs a warning and treats the result as `False`.

A platform-specific plugin should check the current platform, adapter, device capability, and required runtime instead of exposing its schema globally. Synthetic environment events can also be excluded by this filter.

## List and Unregister

```python
active = context.list_persona_effects(event=event)
all_enabled = context.list_persona_effects()
context.unregister_persona_effects(plugin_id="my_plugin")
```

The event-scoped query builds the current Persona contract. The unscoped query is for registry management and diagnostics; it lists all enabled registrations and does not imply that every effect applies to every event.

## Output and Consumption

Persona Runtime always uses this shape:

```json
{
  "spoken_reply": "User-visible reply",
  "effect_calls": [
    {
      "name": "my_plugin.expression",
      "arguments": {"label": "happy"}
    }
  ]
}
```

When no effect applies, `effect_calls` is still present as an empty array. Core validates calls against the registered schema and restores plugin ownership. The plugin consumes its own calls from the current `InteractionResultView.effect_calls`, then executes them through `client_objects`, `platform_extras`, or its own transport.

The plugin owns device constraints, resource mapping, and fallback behavior. Core does not understand motion semantics, Live2D parameters, or client protocols.
