# Persona Effect Tool Call Implementation Plan

> 状态说明（2026-06-25）：
> 本文档中的 phase-based persona expression 设计已过时。
> 当前实现已经改为“visible reply material”驱动：用户可见自然语言统一走一个 persona visible-reply 入口，
> phase 不再作为 first_response / plugin_output / final_response / stream_interjection 的核心语义分叉。
>
> 补充状态说明（2026-06-26）：
> 当前运行时基线也已经从“单个虚拟 `persona_expression` tool call”进一步收口为严格 `json_object`：
>
> - 默认契约是 `mode="json_object"`、`strict=True`、`allow_text_fallback=False`
> - `tool_call` 仍保留为可选协议路径和测试覆盖，但不代表线上 persona visible-reply 主链路
> - `effect_calls` 现在是固定字段；无 effect 时返回空数组，而不是省略字段
> - effect `arguments` 的约束以注册的 `PersonaEffectSpec.parameters` 为准
> - 若 `arguments.axes` 存在，运行时会统一把 `axes.*` 归一为 `number` schema，减少后端 repair
>
> 因此，本文后续凡是把 `tool_call` 写成统一基线、把 `effect_calls` 写成可省略字段、或把 `axes` 视为松散 object 的段落，都应视为历史方案而不是当前实现。

这份文档记录 Yakumo Persona Runtime 中人格表现插件结构化输出的实施计划。

本文服从 `persona-system-final-goal.md` 已确认的运行时边界：

```text
Input Gateway 决定“要做什么”。
Persona Runtime 决定“怎么像这个人一样回应”。
Executor Runtime 负责“实际执行”。
Output Runtime 负责“把 Persona Runtime 的表达发出去”。
```

本文只处理 Persona Runtime 如何生成并发布插件需要的人格表现数据，不处理 Executor Tool、MCP、Skill、Input Bus 或平台适配器重构。

## 背景

当前 Persona Runtime 需要同时生成：

- 用户可见的人格表达。
- AG99Live 动作、TTS 情绪、客户端表现等结构化插件提示。

现有实现把这两部分放在同一个结果对象中：

```json
{
  "spoken_reply": "……你倒是说句话啊。",
  "plugin_hints": {
    "ag99live_motion": {
      "resource_id": "embarrassed_lookaway"
    }
  }
}
```

当前主实现是严格 `json_object`，由本地解析器解析；当 Provider 或测试场景显式启用协议级 Tool Call 时，这份结果也可以通过虚拟 `persona_expression` 工具返回。

这里存在几个长期问题：

- `plugin_hints` 没有正式的注册、所有权和参数 schema。
- 插件能力只能通过 Prompt 文本描述，Core 无法统一验证。
- 文本 JSON 可能被截断或格式错误，甚至直接显示给用户。
- 人格表现能力容易与 Executor Tool 混淆。
- Router、Persona 和 Executor 可能无差别接收不属于自己的能力描述。

因此需要把人格表现能力正式建模为 `Persona Effect`。

## 核心决策

### Persona Effect 与 Executor Tool 分离

`Persona Effect` 表示 Persona Runtime 生成的人格表现意图，例如：

- Live2D 动作或表情。
- TTS 情绪、语速或声线建议。
- 客户端动画、状态或特效。
- 平台展示相关的结构化表现提示。

`Executor Tool` 表示实际执行任务的能力，例如：

- 搜索和检索。
- 文件或代码操作。
- MCP 和 Skill。
- 外部 API 和有副作用的系统操作。

两者生命周期不同：

```text
Executor Tool
  -> 模型请求工具
  -> Tool Runner 执行
  -> 返回 Tool Result
  -> 模型继续推理

Persona Effect
  -> Persona Runtime 生成 Effect Call
  -> Core 校验和选择
  -> Output Runtime / 插件消费
  -> 不返回 Tool Result
```

Persona Effect 不进入 Agent Tool Loop，不由 Router 决策，也不交给 Executor Runtime 执行。

### 一期使用单个虚拟输出工具

历史方案里，跨 Provider 的可靠基线曾被设计为单个虚拟输出工具：

```text
persona_expression
```

其参数同时承载人格文本和表现调用：

```json
{
  "spoken_reply": "……你倒是说句话啊。",
  "effect_calls": [
    {
      "name": "ag99live.motion",
      "arguments": {
        "resource_id": "embarrassed_lookaway",
        "axes": {
          "head_yaw": 40
        }
      }
    }
  ],
  "metadata": {}
}
```

暂不把以下形式作为统一基线：

```text
completion_text = 人格回复
tool_calls = 多个 Persona Effect Calls
```

原因是不同 Provider 对“正文和 Tool Call 同时出现”、强制 Tool Call、多工具调用和严格 schema 的支持并不一致：

- `tool_choice=required` 不保证同时产生正文。
- `tool_choice=auto` 不保证一定产生 Effect Call。
- 部分 Provider 会把 Tool Call 降级为文本 JSON。
- MiniMax 当前的 Renderer 明确使用 `prompt_only` 降级。

正文与原生多个 Effect Tool Call 的混合输出只能作为后续 Provider 能力优化。

### 一期使用可移植 schema

这部分设计已经过时。当前实现为了固定 `effect_calls` 结构，已经接受在 persona visible-reply contract 中使用 `oneOf + const`，并把 effect 参数 schema 直接编译进输出契约。

Effect Calls 使用扁平 schema：

```json
{
  "effect_calls": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "enum": [
            "ag99live.motion",
            "voice.emotion"
          ]
        },
        "arguments": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "required": [
        "name",
        "arguments"
      ]
    }
  }
}
```

校验分为两层：

```text
Provider 协议层
  -> 保证返回 Tool Call 或 JSON object
  -> 约束 Effect name 为已注册名称

AstrBot 本地校验层
  -> 根据 Effect name 查找 PersonaEffectSpec
  -> 使用对应 parameters 校验 arguments
  -> 丢弃未知或无效调用
```

这部分设计已经过时。当前实现即使没有注册任何 Persona Effect，也会保留 `effect_calls` 字段，并要求模型返回空数组：

```python
effect_calls = []
```

### 保留 JSON 修复降级

Provider 支持协议 Tool Call 时，优先读取 `LLMResponse.tools_call_args`。

Provider 降级为文本输出时，使用以下链路：

```text
completion_text
  -> 严格 json.loads()
  -> 失败时 json-repair
  -> 根类型检查
  -> Persona Expression 字段解析
  -> Effect 本地 schema 校验
```

JSON 修复只是 Provider 协议降级后的容错措施，不能替代 Effect 注册和参数校验。

## 目标数据模型

### `PersonaEffectSpec`

建议新增：

```text
astrbot/core/interaction/effects.py
```

定义：

```python
@dataclass(slots=True)
class PersonaEffectSpec:
    plugin_id: str
    name: str
    description: str
    parameters: dict[str, Any]
    legacy_hint_names: tuple[str, ...] = ()
    priority: int = 100
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
```

字段语义：

- `plugin_id`：注册该 Effect 的插件。
- `name`：全局唯一的正式名称。
- `description`：提供给 Persona 模型的静态能力说明。
- `parameters`：Effect arguments 的 JSON Schema。
- `phases`：允许生成该 Effect 的 Persona 阶段；空元组表示所有阶段。
- `legacy_hint_names`：旧 `plugin_hints` key 的显式兼容别名。
- `priority`：注册表排序和协议构建顺序。
- `enabled`：当前是否启用。
- `metadata`：仅用于框架内部所有权、路由、兼容和诊断。

`metadata` 永远不进入 Prompt 或 Output Contract。插件不得通过它传递 Prompt 指令、角色状态、动作选择规则或其他模型需要读取的内容。

影响模型的静态信息必须放入 `description` 或 `parameters`，动态信息必须通过 Interaction Prompt Contributor 提供。

正式名称建议使用命名空间：

```text
ag99live.motion
voice.emotion
client.expression
```

### `PersonaEffectCall`

定义：

```python
@dataclass(slots=True)
class PersonaEffectCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None
    plugin_id: str | None = None
    source: str = "persona"
    metadata: dict[str, Any] = field(default_factory=dict)
```

模型只能决定 `name` 和 `arguments`。`plugin_id` 必须由 Core 根据注册表解析，不能信任模型提供的插件所有权。

### `PersonaExpressionResult`

目标结构：

```python
@dataclass(slots=True)
class PersonaExpressionResult:
    spoken_reply: str = ""
    effect_calls: list[PersonaEffectCall] = field(default_factory=list)
    plugin_hints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
```

`plugin_hints` 在迁移期保留，用于旧插件兼容；新插件应读取 `effect_calls`。

## 注册表设计

在 `Context` 中增加独立注册表：

```python
def register_persona_effect(self, effect: PersonaEffectSpec) -> None:
    ...

def list_persona_effects(
    self,
    *,
    phase: str | None = None,
) -> list[PersonaEffectSpec]:
    ...

def unregister_persona_effects(
    self,
    *,
    plugin_id: str | None = None,
    module_prefix: str | None = None,
) -> int:
    ...
```

注册表至少维护：

```python
effects_by_name: dict[str, PersonaEffectSpec]
effects_by_legacy_name: dict[str, PersonaEffectSpec]
```

注册时必须检查：

- `plugin_id` 和 `name` 非空。
- 正式名称格式合法。
- 正式名称全局唯一。
- legacy alias 全局唯一。
- 正式名称不能与其他 Effect 的 alias 冲突。
- alias 不能与其他 Effect 的正式名称冲突。
- `parameters` 根节点必须是 `object`。
- `properties` 必须是 mapping。
- `required` 存在时必须是 list。
- `phases` 只能包含合法 Persona phase。
- 插件卸载时正式名称和 alias 必须一起清理。

重复或冲突注册必须明确失败，不能静默覆盖。

## Legacy 兼容规则

旧 `plugin_hints` 名称只通过显式 alias 转换。

例如：

```python
PersonaEffectSpec(
    plugin_id="astrbot_plugin_ag99live_adapter",
    name="ag99live.motion",
    legacy_hint_names=("ag99live_motion",),
    ...
)
```

转换顺序：

1. 按正式 `name` 精确匹配。
2. 按 `legacy_hint_names` 精确匹配。
3. 未匹配的 hint 不转换为 Effect Call。
4. 未匹配数据仍可保留在旧 `plugin_hints` 视图中。

禁止自动执行：

```text
下划线 -> 点号
点号 -> 下划线
大小写归一化
模糊前缀匹配
```

自动转换无法可靠判断命名空间边界，会产生隐式兼容行为和名称冲突。

## Persona Prompt 与协议构建

`build_persona_expression_tool_parameters()` 改为：

```python
def build_persona_expression_tool_parameters(
    effects: Sequence[PersonaEffectSpec] = (),
) -> dict[str, Any]:
    ...
```

行为：

- 始终生成 `spoken_reply`。
- 迁移期继续生成 `plugin_hints`。
- `effects` 非空时生成 portable `effect_calls`。
- `effects` 为空时不生成 `effect_calls`。
- Effect name 进入稳定排序后的 `enum`。
- 不把 `PersonaEffectSpec.metadata` 写入 schema。
- 不原地修改插件提供的 `parameters`。

Persona 系统 Prompt 应明确：

```text
spoken_reply 是用户可见的人格表达。
effect_calls 是可选的人格表现意图。
只能使用协议中声明的 Effect name。
没有合适 Effect 时不生成 Effect Call。
不得把 JSON、Effect 参数或协议字段写入 spoken_reply。
不得把 Persona Effect 当成已经完成的外部任务。
```

完整 schema 只应由 Output Contract 提供。原生 Tool Call 已携带 schema 时，不在普通 Prompt 中重复粘贴；只有 `prompt_only` 降级时，由 Output Contract fallback 生成结构化输出要求。

## Persona 阶段的文本要求

当前 `generate_expression()` 对所有阶段都要求 `spoken_reply` 非空。Effect 接入前需要增加：

```python
def phase_requires_spoken_reply(
) -> bool:
    ...
```

初始规则：

| Phase | 输出要求 |
| --- | --- |
| `first_response` | 必须有文本 |
| `plugin_output` | 必须有文本 |
| `final_response` | 必须有文本 |
| `executor_started` | 文本或 Effect 至少一个 |
| `executor_progress` | 文本或 Effect 至少一个 |
| `executor_result` | 初期要求文本 |

统一有效性判断：

```python
if phase_requires_spoken_reply(req.phase) and not result.spoken_reply:
    raise InteractionExpressionError("empty_output")

if not result.spoken_reply and not result.effect_calls:
    raise InteractionExpressionError("empty_output")
```

## 并行分支约束

Router 和 Persona 并行运行时：

```text
Router
  -> 只读取 Router Prompt View
  -> 不收集 Persona Effect Specs
  -> 不接收动作 capability
  -> 不生成 Effect Call

Persona Runtime
  -> 独立收集当前 phase 可用的 Effect Specs
  -> 独立构建 Output Contract
  -> 在分支局部结果中保存 Effect Calls
```

禁止在模型调用或分支选择前写入：

```python
event.set_extra("_interaction_plugin_hints", ...)
event.set_extra("_interaction_effect_calls", ...)
```

只有 Persona 结果被当前交互采用后，Effect Calls 才能随本次
`PersonaExpressionResult` 进入 `InteractionResultView` 或兼容 event extra；route decision 不承载 effect。

未选中的并行分支不得污染共享事件。

## 结果发布与插件消费

目标是在 `InteractionResultView` 中增加：

```python
effect_calls: tuple[PersonaEffectCall, ...] = ()
```

同步更新：

- `as_read_only_mapping()`。
- `copy_read_only()`。
- Result Contributor 视图构建。
- Interaction Decision 的序列化。

一期之后的新插件读取：

```python
view.effect_calls
```

旧插件继续读取：

```python
view.plugin_hints
```

第一阶段不要求立即实现专用 Dispatcher。可以继续由 Interaction Result Contributor 消费 Effect Calls，并转换为：

- `client_objects`。
- `tts_hints`。
- `platform_extras`。

当至少有两个独立插件需要直接消费 Persona Effect 时，再评估新增：

```text
PersonaEffectDispatcher
PersonaEffectConsumer
```

## Provider 策略

### OpenAI

支持时使用 `protocol_tool_call`，由虚拟 `persona_expression` 工具返回结构化参数。

一期不要求 Provider 完成每个 Effect arguments 的联合严格校验，参数由 AstrBot 本地二次验证。

### Anthropic

支持时使用协议级 Tool Use。Provider Adapter 将 `tool_use.input` 转换为统一的 `LLMResponse.tools_call_args`，Persona Runtime 不感知 Provider 私有格式。

### MiniMax

保持当前 `prompt_only` 策略，不在本计划中贸然启用强制 Tool Call。

降级链为：

```text
Output Contract fallback prompt
  -> completion_text
  -> JSON parse / repair
  -> Persona result parse
  -> Effect 本地校验
```

### 其他 Provider

协议策略由 Prompt Renderer 和 Provider Capability 决定。禁止在 `InteractionExpressionAgent` 中根据 Provider ID 添加特殊分支。

## 错误处理

### 有效文本、无效 Effect

发送人格文本，丢弃无效 Effect，并记录拒绝原因。

### 必须有文本的阶段返回空文本

抛出 `InteractionExpressionError("empty_output")`，沿用当前 fallback。

### 允许仅 Effect 的阶段

只要至少存在一个有效 Effect Call，即可接受结果。

### 原生 Tool Call 不可用

尝试解析文本 JSON，并在严格解析失败时使用 `json-repair`。

### JSON 修复失败

如果存在普通文本，将其作为 `spoken_reply`；不生成 Effect Calls。

### 插件消费失败

不能阻止用户可见文本发送。记录失败后继续 Output Runtime。

## 实施阶段

### Phase 1：协议模型和注册表

范围：

- 新增 `astrbot/core/interaction/effects.py`。
- 实现 `PersonaEffectSpec`。
- 实现 `PersonaEffectCall`。
- 实现 Effect 注册表和注册校验。
- 在 `Context` 增加注册、查询和注销接口。
- 实现显式 legacy alias 索引。
- 改造 `build_persona_expression_tool_parameters(effects=())`。
- 保留现有 `plugin_hints` 字段。
- 增加 schema 和注册表单元测试。

不修改：

- `generate_expression()` 的生产调用链。
- Middleware 的并行选择。
- `InteractionDecision`。
- `InteractionResultView`。
- Output Controller。
- Executor Tool Runner。
- AG99Live 插件。

### Phase 1.5：Persona phase 输出有效性

范围：

- 增加 `phase_requires_spoken_reply()`。
- 按 phase 判断空输出。
- 为 `executor_started`、`executor_progress` 和 `executor_result` 增加测试。

此阶段可以先让 `effect_calls` 为空，目的是提前稳定 Persona 阶段语义。

### Phase 2：Persona Expression 接入

范围：

- `PersonaExpressionResult` 增加 `effect_calls`。
- Persona 分支按 phase 查询 Effect Specs。
- 动态构建 Persona Output Contract。
- 优先解析协议 Tool Call。
- 保留 repaired JSON 和纯文本 fallback。
- 根据注册表校验 Effect name 和 arguments。
- 无效 Effect 不影响有效人格文本。

### Phase 3：选择后发布

范围：

- `InteractionDecision` 增加 `effect_calls`。
- Middleware 只发布被选中的 Persona 结果。
- `InteractionResultView` 增加只读 `effect_calls`。
- 提供 Effect Calls 到旧 `plugin_hints` 的兼容视图。
- 禁止并行分支提前写共享 event extra。

### Phase 4：AG99Live 迁移验证

范围：

- AG99Live 注册 `PersonaEffectSpec`。
- Prompt Contributor 只提供动作选择所需动态上下文。
- Result Contributor 从 `view.effect_calls` 消费动作。
- 不再要求插件解析 Persona JSON。
- 保留旧 `ag99live_motion` alias，验证迁移兼容。

该阶段应在 AG99Live 项目单独实施，不把其私有字段硬编码进 AstrBot Core。

### Phase 5：专用 Dispatcher

当多个插件需要原生 Effect 消费时，再实现：

- `PersonaEffectDispatcher`。
- `PersonaEffectConsumer`。
- 消费超时和失败隔离。
- Effect 级日志和指标。

### Phase 6：Provider 专用严格协议

在有真实兼容性测试后，再评估：

- OpenAI 专用 `anyOf` schema。
- Anthropic 专用严格 Tool Use schema。
- 正文与多个原生 Effect Tool Call 的混合输出。
- Provider Capability 探测和协议缓存。

稳定的单个 `persona_expression` 虚拟工具仍应保留为统一基线。

## Phase 1 文件范围

建议新增：

```text
astrbot/core/interaction/effects.py
tests/unit/test_interaction_effects.py
```

建议修改：

```text
astrbot/core/star/context.py
astrbot/core/interaction/expression_agent.py
tests/unit/test_interaction_expression_agent.py
```

Phase 1 对 `expression_agent.py` 的修改只限于 schema builder 签名和纯函数，不改实际模型调用及结果发布行为。

## Phase 1 测试清单

必须覆盖：

1. 空 Effect 列表不生成 `effect_calls`。
2. 单个 Effect 生成正确的 `name.enum`。
3. 多个 Effect 名称按稳定顺序生成。
4. schema 不使用 `oneOf`、`const` 或 `maxItems`。
5. schema 构建不原地修改插件传入的 `parameters`。
6. 重复正式名称注册失败。
7. 重复 legacy alias 注册失败。
8. 正式名称与其他 alias 冲突时注册失败。
9. alias 与其他正式名称冲突时注册失败。
10. legacy hint 只按显式 alias 转换。
11. 不执行下划线和点号自动转换。
12. `metadata` 不进入 Prompt schema。
13. 按 phase 查询只返回适用的 Effect。
14. disabled Effect 不进入查询和 schema。
15. 插件注销后正式名称和 alias 一起移除。
16. 现有 `plugin_hints` schema 保持兼容。
17. Router Output Contract 不包含 Effect 信息。
18. Context 注册表返回稳定、不可意外修改的结果。

## 后续测试矩阵

Phase 2 和 Phase 3 继续覆盖：

- 原生 `persona_expression` Tool Call 解析。
- repaired JSON 解析。
- 纯文本 fallback。
- 未知 Effect 拒绝。
- arguments schema 验证。
- 无效 Effect 不影响文本。
- Router 与 Persona 使用不同 RenderResult。
- Router 不收集 Effect Specs。
- 未选中分支不能发布 Effect Calls。
- `InteractionResultView.effect_calls` 是只读快照。
- 旧 `plugin_hints` 插件保持兼容。
- OpenAI、Anthropic 和 MiniMax 各自协议策略正确。

## 日志和可观测性

建议逐步增加：

```text
persona_effect_registered
persona_effect_specs_collected
persona_effect_protocol_strategy
persona_effect_call_parsed
persona_effect_call_rejected
persona_effect_legacy_alias_used
persona_effect_dispatched
persona_effect_dispatch_failed
```

日志可以包含：

- `turn_id`。
- `platform_id`。
- `session_id`。
- `phase`。
- `provider_id`。
- `strategy`。
- `effect_name`。
- `plugin_id`。
- `reason`。

不得默认记录完整 Effect arguments，避免把敏感或体积较大的插件数据写入日志。

## 一期完成标准

Phase 1 完成时必须满足：

1. Core 中存在独立的 Persona Effect 数据模型。
2. Persona Effect 与 Executor Tool 没有注册表或执行链耦合。
3. 插件可以注册正式名称、参数 schema、phase 和 legacy alias。
4. 注册冲突会明确失败。
5. Persona Expression schema builder 可以接收动态 Effect Specs。
6. schema 使用跨 Provider 的可移植结构。
7. 空 Effect 集合不会生成无效或不兼容的数组约束。
8. `plugin_hints` 现有行为不变。
9. Router 不接收 Persona Effect。
10. 未修改 Persona 生产调用链、Executor Tool Loop 或平台适配器。
11. 新增单元测试通过。
12. 相关 interaction 回归测试通过。

Phase 1 的目的不是立即让插件消费 Effect Calls，而是先把协议模型、名称所有权、兼容规则和跨 Provider schema 边界确定下来。完成后再进入 Persona Runtime 调用链改造。
