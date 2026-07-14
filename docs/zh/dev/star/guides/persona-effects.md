---
outline: deep
---

# Persona Effect

Persona Effect 是 Yakumo fork 的拟人输出扩展协议。插件可以让 Persona Runtime 在生成 `spoken_reply` 的同时生成结构化表现意图，例如 Live2D 动作、灯光或客户端表情。

Persona Effect 不是 Agent Tool：它不会进入 Core Tool Loop，也不会提供给 Router。Router 始终只返回 `silent`、`persona` 或 `hybrid`，不注册工具、不要求 JSON，也不生成 `effect_calls`。

## 注册 Effect

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

`event_filter` 是同步、无副作用的判断函数。Core 在每次构建 Persona 输出契约时传入当前事件：

- 返回 `True`：effect schema 进入本轮 `persona_expression` 契约。
- 返回 `False`：本轮不向模型暴露该 effect，不消耗对应 schema token。
- 抛出异常：Core 记录告警并按 `False` 处理。

平台专用插件不应只在启动时全局注册 schema，而应同时检查当前平台、adapter、设备能力和所需 runtime 是否真实可用。合成环境事件也可以在过滤器中排除。

## 查询与注销

```python
active = context.list_persona_effects(event=event)
all_enabled = context.list_persona_effects()
context.unregister_persona_effects(plugin_id="my_plugin")
```

带 `event` 的查询用于构建当前 Persona 契约。不带 `event` 的查询用于注册表管理和诊断，会返回所有已启用注册项，不代表它们对任意事件都可用。

## 输出与消费

Persona Runtime 的结构固定为：

```json
{
  "spoken_reply": "用户可见回复",
  "effect_calls": [
    {
      "name": "my_plugin.expression",
      "arguments": {"label": "happy"}
    }
  ]
}
```

无可用 effect 时仍返回 `effect_calls: []`。Core 根据注册 schema 校验调用并补充插件所有权；插件从当前阶段的 `InteractionResultView.effect_calls` 消费属于自己的调用，再通过 `client_objects`、`platform_extras` 或自己的传输链路执行。

插件必须自行负责设备约束、资源映射和降级策略。Core 不理解具体动作、Live2D 参数或客户端协议。
