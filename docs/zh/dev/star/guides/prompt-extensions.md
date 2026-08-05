---
outline: deep
---

# Prompt Extension

Prompt Extension 是 Yakumo fork 向统一 Prompt 事实管线贡献模型可见上下文的插件接口。它适合提供业务状态、平台能力摘要或当前事件相关资料，不适合注册可执行工具、修改路由结果或发送消息。

## 它处于哪里

```text
Plugin Collector
  -> PromptExtension
  -> ContextSlot / ContextPack
  -> target projection
  -> layout / renderer
  -> ProviderRequest
```

Collector 在目标投影前运行，因此同一份插件事实可以通过 `meta.targets` 授权给 Persona 或 Core。Router 和 Core Planner 不挂载插件扩展，也不读取插件能力目录；它们只消费核心维护的路由事实。Collector 不会拿到这些模型的决策，也不能修改规范 Pack。

## 注册 Collector

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

插件卸载或热重载时，AstrBot 会按插件模块所有权清理注册项。

## 字段边界

`PromptExtension` 的主要字段：

- `plugin_id`：稳定且非空的插件所有权标识。
- `mount`：`system`、`context`、`input`、`conversation`、`memory` 或 `capability`。
- `title`：可选的人类可读标题。
- `value`：要贡献的事实。
- `value_kind`：`text`、`mapping` 或 `sequence`。
- `order`：同一 mount 内的稳定顺序，数值越小越靠前。
- `meta.targets`：允许读取该事实的目标列表。

目标值：

- `persona`
- `core`

普通 extension 没有声明 `targets` 时默认只提供给 Core。不要依赖“所有目标默认可见”。

Router 和 Core Planner 不接受插件能力目录。需要让它们参与路由或执行判断的事实，必须由 AstrBot 内部明确标记的核心 Collector 以非插件的结构化上下文提供；插件不能通过 Prompt Extension 改变 Router/Planner 的准入或规划，自行设置 `official_context` 也不会获得该权限。

## 生命周期与失败

- `dynamic`：每次 Prompt build 都重新收集。
- `static`：只在同一 event、同一 config、同一 `ProviderRequest` 对象内缓存成功结果。

`static` 不是跨消息、跨会话或全局缓存。群聊上下文、用户状态、设备在线状态等会变化的数据必须使用 `dynamic`。

插件 Collector 的异常会记录告警并跳过，避免一个插件阻断核心 Prompt。插件应自行记录必要诊断，但不得把异常日志、traceback 或过期执行痕迹作为模型事实返回。

## 与其他接口的区别

| 接口 | 用途 |
|---|---|
| Prompt Extension Collector | 在统一管线中贡献模型可见事实 |
| Persona Effect | 给 Persona 输出契约增加结构化表现能力，不是输入事实 |
| LLM Tool | 注册可执行能力；插件工具默认进入 Core，只有工具声明或用户配置明确允许时进入 Persona |
| `on_llm_request` | 修改路由后的最终 Persona 或 Core 低层请求，取决于插件运行目标 |

插件 LLM 生命周期与 LLM Tool 独立解析：生命周期按“`plugin_runtime_targets` 配置覆盖 > 类或旧装饰器声明 > Persona 默认值”，工具按“`plugin_tool_targets` 用户覆盖 > 工具 `tool_targets` 声明 > Core 默认值”。非 Interaction 流程保持官方 Core 行为。`on_llm_request` 不覆盖 Router、Core Planner 或 Persona 内部工具回路；实际执行 Persona 插件工具时仍会触发 `on_using_llm_tool` 和 `on_llm_tool_respond`。需要 Persona/Core 读取的插件事实必须进入 Prompt Extension，并声明 `persona` 或 `core`；不要把每轮动态事实依赖在低层请求钩子上。

## 安全约束

- 不返回 token、密码、内部路径或无必要的用户标识。
- 不把模型输出、Router/Planner 决策重新注入同一轮事实包。
- 不在 Collector 中发送消息、写 memory 或执行有副作用工具。
- 不用 Prompt Extension 伪装可执行工具；实际工具必须通过 Tool API 注册。
- 不为某个插件要求修改通用 Router Prompt；插件只描述自己的名称和能力。

Prompt 系统当前不会自动执行所有 Catalog redaction 声明。插件必须在返回 `value` 前完成自己的最小化和脱敏。
