# Legacy Plugin Hook Migration and Input Bus Plan

这份文档记录 Yakumo 一期的插件兼容与 Input Bus 实施计划。

它不是最终插件协议，也不要求现在设计一套全新的插件生态。一期工作的首要目标是保留 AstrBot 现有插件能力，将旧插件依赖的钩子逐步迁移到新的 Input Gateway、Persona Runtime、Executor Runtime 和 Output Runtime。

本文服从 `persona-system-final-goal.md` 已经确认的运行时边界：

```text
Input Gateway 决定“要做什么”。
Persona Runtime 决定“怎么像这个人一样回应”。
Executor Runtime 负责“实际执行”。
Output Runtime 负责“把 Persona Runtime 的表达发出去”。
```

## 一期目标

一期只做两件核心工作：

1. 完整确认 AstrBot 当前提供的插件钩子、参数、触发位置和控制语义。
2. 在不破坏旧插件调用方式的前提下，将这些钩子迁移到新运行时。

一期不以增加大量新钩子为目标，也不以立即完成最终插件协议为目标。

兼容优先级如下：

```text
旧插件装饰器和函数签名
  -> 旧触发条件和执行顺序
  -> 旧可修改对象和修改生效范围
  -> stop_event / result / send 等控制语义
  -> 最后才是新增能力
```

只有当旧系统没有对应能力，并且新运行时确实无法表达必要行为时，才讨论增加新的扩展点。

## 插件的临时分类

一期暂时把插件分成两类。

### 人格增强插件

依赖消息、LLM 请求、LLM 响应、结果包装和消息发送等对话生命周期钩子的插件，暂时归入人格增强。

这类插件可能：

- 读取或修改用户输入。
- 修改 Prompt 或 ProviderRequest。
- 观察或修改模型响应。
- 修改发送前的消息结果。
- 观察消息发送完成。
- 根据对话过程补充人格、记忆、状态或表现能力。

一期的主要迁移对象就是这组钩子。

### 功能增强插件

类似 MiniMax CLI，或者向 Agent / Executor 提供工具、Skill、任务执行能力的插件，暂时归入功能增强。

这类插件主要依赖：

- LLM tool 注册。
- 工具调用前后钩子。
- Agent 开始和完成事件。
- Executor 可调用的外部能力。

功能增强最终应进入 Executor Runtime，但一期先保证旧工具注册和调用链不被 Input Bus 改造破坏。

### 暂缓分类

以下生命周期事件暂时保留旧实现，后续归入系统增强：

- AstrBot 加载完成。
- 平台加载完成。
- 插件加载、卸载和错误事件。

一期不借迁移人格钩子的机会重写插件管理器。

## 当前系统情况

AstrBot 当前已经存在一条事实上的输入传递链：

```text
Platform Adapter
  -> queue-like input object
  -> InteractionMiddleware.handle_inbound(...)
  -> core event_queue
  -> EventBus.dispatch(...)
  -> PipelineScheduler.execute(...)
```

相关实现包括：

- 平台通过 `Platform.commit_event(event)` 或 `_event_queue.put_nowait(event)` 提交 `AstrMessageEvent`。
- 平台入口仍直接写入原有 `event_queue`。
- 未启用 interaction middleware 的事件按官方 pipeline 继续执行。
- `EventBus` 从 `event_queue` 中读取事件，并交给对应的 `PipelineScheduler`。
- interaction middleware 位于 `ProcessStage` 内部，贴在核心 agent 启动前执行快速拟人回复和路由判断。
- 大部分旧插件钩子仍在 pipeline 的各个 stage 内触发。

因此，目前不再保留独立的输入代理类。平台输入先进入官方 EventBus/pipeline，统一输入分类和 route / executor decision 由 `ProcessStage` 内部的 interaction 入口和显式 router/decision 逻辑承担。

一期不能在这条链旁边再建立一套平行输入链。目标是逐步把现有入口正规化：

```text
Platform Adapter / Internal Producer
  -> Input Bus
  -> Input Gateway
      -> Persona Runtime first response
      -> route / executor decision
  -> legacy pipeline or new runtime path
```

## 旧钩子清单与目标归属

### 输入和对话处理

| 旧事件 | 当前语义 | 目标归属 | 一期策略 |
| --- | --- | --- | --- |
| `AdapterMessageEvent` | 适配器消息 handler、command、regex 和各种 filter 共用的事件类型 | Input Bus / Input Gateway 入口附近，但仍需保留旧过滤和唤醒语义 | 先保留原触发点，完成输入包装后再迁移 dispatcher |
| `OnWaitingLLMRequestEvent` | 确定调用 LLM、获取锁之前的通知 | Persona Runtime 请求等待阶段 | 保留名称和 `event` 参数，桥接到 Persona 请求生命周期 |
| `OnLLMRequestEvent` | Provider 请求发起前，可修改 `ProviderRequest` | Persona Runtime 请求前；Executor 自身 LLM 调用也需保留兼容 | 按调用来源标记 lane，但旧插件仍接收原参数 |
| `OnLLMResponseEvent` | LLM 响应后 | Persona Runtime 生成后；Executor LLM 响应也需保留兼容 | 保留一次调用对应一次响应，不重复触发 |
| `OnDecoratingResultEvent` | 最终消息发送前 | Persona Runtime 输出形成后、Output Runtime 投递前 | 保留对 `event.result` 的修改能力 |
| `OnAfterMessageSentEvent` | 消息发送完成后 | Output Runtime 投递完成后 | 保留真实发送完成后的触发时机 |

### Agent 和执行能力

| 旧事件 | 当前语义 | 目标归属 | 一期策略 |
| --- | --- | --- | --- |
| `OnAgentBeginEvent` | Agent 开始运行 | Executor Runtime lifecycle | Executor 发出状态，同时允许 Persona Runtime 观察 |
| `OnAgentDoneEvent` | Agent 运行完成 | Executor Runtime lifecycle | Executor 产出结果，同时允许 Persona Runtime 包装 |
| `OnCallingFuncToolEvent` | 注册和调用旧函数工具 | Executor capability registry | 一期保持原注册和调用方式 |
| `OnUsingLLMToolEvent` | 工具调用前 | Executor Runtime tool lifecycle | 后续桥接，不由 Input Bus 直接处理 |
| `OnLLMToolRespondEvent` | 工具调用后 | Executor Runtime tool lifecycle | 后续桥接，不由 Input Bus 直接处理 |

### 系统生命周期

以下事件一期不迁移，只验证 Input Bus 改造没有破坏它们：

- `OnAstrBotLoadedEvent`
- `OnPlatformLoadedEvent`
- `OnPluginLoadedEvent`
- `OnPluginUnloadedEvent`
- `OnPluginErrorEvent`

## 必须保留的兼容语义

迁移一个钩子不能只做到“还能调用”。至少需要验证以下语义：

### 注册表面

- 旧 decorator 名称继续可用。
- handler 参数数量和参数类型不变。
- `priority` 等现有注册配置继续生效。
- session plugin filtering 继续生效。

### 调用顺序

- 同一事件的 handler 顺序不应无意改变。
- 旧钩子不能因为新旧链路并存而触发两次。
- `OnLLMRequestEvent` 和 `OnLLMResponseEvent` 必须保持请求与响应的对应关系。
- `OnDecoratingResultEvent` 必须发生在实际投递前。
- `OnAfterMessageSentEvent` 必须发生在实际投递后。

### 控制和修改

- 插件对 `ProviderRequest` 的原地修改继续影响实际请求。
- 插件对 event result 的修改继续影响最终输出。
- `event.stop_event()` 的传播终止语义继续有效。
- 插件通过旧 `event.send(...)` 发送消息仍然可用。
- 插件异常继续遵循原有隔离和错误处理方式。

### 消息 handler 的特殊性

`AdapterMessageEvent` 不能被当作普通的“收到消息后立即调用”钩子。

当前 command、regex、permission、platform、message type 和 custom filter 都注册在这个事件类型上；它们还依赖 wake check、权限判断、参数解析、session plugin filtering 和 `activated_handlers`。

所以一期不能简单地把所有 `AdapterMessageEvent` handler 提前到 Input Bus 执行，否则会改变：

- command 和 regex 的触发条件。
- 群聊唤醒行为。
- 权限拒绝行为。
- handler 参数解析。
- 插件停止事件后 pipeline 是否继续运行。

正确方式是先让 Input Bus 承载旧事件，再把旧 dispatcher 作为一个完整兼容单元迁移，而不是把 handler 从 pipeline 中逐个搬走。

## Input Bus 目标

Input Bus 是所有输入进入新运行时的统一入口，但一期首先是兼容层。

它需要同时支持：

- 旧平台适配器提交的 `AstrMessageEvent`。
- 旧插件通过 event queue 注入的 `AstrMessageEvent`。
- 后续系统内部产生的 signal。
- 后续 heartbeat、scheduled、executor progress 等非用户输入。

Input Bus 不做 route decision，不生成人格回复，也不执行插件业务逻辑。

它只负责：

- 接收输入。
- 标记输入种类和来源。
- 建立输入 envelope / runtime context。
- 保持同一输入的 identity 和 trace。
- 将输入交给 Input Gateway。
- 在过渡期把事件送回旧链路。

## 输入数据模型

一期建议引入外部输入包装对象，而不是把所有新字段直接塞进 `AstrMessageEvent`：

```python
class InputKind(str, Enum):
    USER = "user"
    SYSTEM = "system"
    HEARTBEAT = "heartbeat"


@dataclass(slots=True)
class InputEnvelope:
    input_id: str
    kind: InputKind
    payload: AstrMessageEvent | InternalSignal
    source: str
    created_at: float
```

这里的 `payload` 在一期主要是原来的 `AstrMessageEvent`。

为了兼容用户和插件现有代码，可以通过 runtime refs 或兼容属性，让 event 能读取当前输入信息：

```text
event
  -> EventRuntimeRefs
      -> current InputEnvelope / InputContext
```

不建议让每个 `AstrMessageEvent` 自己创建 Input Bus、Input Gateway 或其他共享 runtime。

### 默认分类

旧适配器没有显式提供 `input_kind` 时：

```text
AstrMessageEvent from platform adapter
  -> InputKind.USER
```

新内部生产者必须显式提交 `SYSTEM` 或 `HEARTBEAT`，不能依赖消息文本或平台名称猜测。

一期只需要把分类模型接通，不需要立即实现 system / heartbeat 的完整处理策略。

## Input Bus 兼容接口

为了避免第一步修改所有平台适配器，Input Bus 应暂时表现为 queue-like object：

```python
input_bus.put_nowait(event)
await input_bus.put(event)
```

当收到旧 `AstrMessageEvent` 时，Input Bus 自动包装为 `InputEnvelope(kind=USER, ...)`。

新代码则可以显式发布：

```python
input_bus.publish(envelope)
```

需要保留的原则：

- 旧适配器不需要在一期知道 `InputEnvelope`。
- `Platform.commit_event(event)` 的调用方式不变。
- 旧插件获得的 `Context.get_event_queue()` 在迁移前仍然可用。
- 不要求一次性修改所有 `_event_queue.put_nowait(...)` 调用。

## 实施阶段

### Phase 0: 钩子基线和兼容测试

在改 Input Bus 之前，先为旧钩子建立行为基线。

需要记录并测试：

- 每个 decorator 注册到哪个 `EventType`。
- handler 获得哪些参数。
- handler 顺序。
- filter、priority 和 session plugin filtering。
- `stop_event()`。
- 请求、响应和 result 修改是否生效。
- 发送前后钩子的真实时间顺序。
- handler 异常是否阻断后续 handler。

Phase 0 的产物不是新实现，而是一组兼容测试。后续迁移必须持续通过这些测试。

### Phase 1: 建立 Input Bus 类型和兼容入口

新增最小模块：

```text
InputKind
InputEnvelope
InputBus
```

第一阶段的 Input Bus 只做：

```text
legacy AstrMessageEvent
  -> wrap as USER InputEnvelope
  -> bind input context to event
  -> forward to current inbound path
```

这一阶段不改变：

- interaction middleware 的 first response 行为。
- route decision。
- pipeline stages。
- 插件钩子触发位置。
- 输出发送行为。

### Phase 2: 生命周期接入 Input Bus

在 `CoreLifecycle` 中创建共享 Input Bus，并将平台适配器的 queue-like 入口指向它。

过渡期链路：

```text
Platform Adapter
  -> InputBus.put_nowait(event)
  -> existing InteractionMiddleware inbound path
  -> existing core event_queue
  -> EventBus
  -> legacy pipeline
```

此时 Input Bus 已经成为平台输入的真实入口，但业务行为仍然由旧链路完成。

当前 `ProcessStage` 内部的 interaction 入口是 core agent 前的入口，未来也可以被 Input Bus 包裹；在目标 Input Gateway 实现前，不应把平台入口适配层误认为最终决策层。

### Phase 3: 统一旧插件注入入口

检查所有通过 `Context.get_event_queue()` 或其他方式主动注入事件的旧插件和内置插件。

Input Bus 需要提供它们实际依赖的最小 queue API，并确保注入事件也能获得：

- `InputEnvelope`
- `input_id`
- `InputKind`
- trace
- runtime refs

完成这一步后，平台输入和插件注入输入才真正共享同一入口。

### Phase 4: 建立 Hook Compatibility Dispatcher

从现有 `call_event_hook(...)` 和 `star_handlers_registry` 提取一个兼容调度边界。

它不是新插件协议，而是旧钩子的统一执行器，负责保留：

- EventType 查询。
- plugins_name 过滤。
- handler 顺序。
- 参数传递。
- 异常隔离。
- `stop_event()`。

旧 pipeline 和新 runtime 在过渡期都通过同一个 compatibility dispatcher 调用旧钩子，避免复制调用逻辑和重复触发。

### Phase 5: 迁移人格增强钩子

推荐按以下顺序迁移：

```text
1. OnWaitingLLMRequestEvent
2. OnLLMRequestEvent
3. OnLLMResponseEvent
4. OnDecoratingResultEvent
5. OnAfterMessageSentEvent
6. AdapterMessageEvent compatibility dispatcher
```

前三个先建立 Persona Runtime 请求生命周期兼容，后两个建立 Output Runtime 前后兼容。

`AdapterMessageEvent` 最后迁移，因为它同时承载 command、regex、permission 和 waking semantics，风险最高。

### Phase 6: 迁移执行能力钩子

在 Executor Runtime 边界明确后，再迁移：

- `OnAgentBeginEvent`
- `OnAgentDoneEvent`
- `OnUsingLLMToolEvent`
- `OnLLMToolRespondEvent`
- `OnCallingFuncToolEvent`

这些钩子的迁移不能和 Persona Runtime 混在一起。Persona Runtime 可以观察 Executor 状态，但工具注册和执行归 Executor Runtime 所有。

## 第一个实现切片

从 Input Bus 开始是合适的，但第一个切片必须足够小。

建议首个实现只包含：

1. `InputKind`。
2. `InputEnvelope`。
3. queue-like `InputBus.put_nowait(...)`。
4. 将旧 `AstrMessageEvent` 自动分类为 `USER`。
5. 将 envelope / input context 绑定到 event 的外部 runtime refs。
6. 原样转发到当前 `InteractionMiddleware.handle_inbound(...)`。
7. 单元测试证明启用和未启用 interaction middleware 时，事件仍进入原来的目的地。

首个切片明确不包含：

- 移动任何旧钩子。
- 修改 route decision。
- 修改 first response。
- 修改 middleware 输出拦截。
- 修改平台 event 子类。
- 实现 heartbeat 行为。
- 替换 EventBus 或 PipelineScheduler。

这个切片完成后，系统行为应与现在一致，但每一个平台输入已经拥有稳定的 input identity、kind 和 envelope，后续迁移才有可靠落点。

## 验收标准

### Input Bus 首个切片

- 所有旧平台仍可提交 `AstrMessageEvent`。
- 未启用 interaction middleware 时，事件仍直接进入旧 core queue。
- 启用 interaction middleware 时，first response 和 route 行为不变。
- 同一个事件不会被重复入队。
- 旧 event 对外属性和函数不变。
- 输入可以读取稳定的 `input_id`、`kind` 和 `source`。
- Input Bus 自身不调用 Persona、Executor 或旧插件 handler。

### 钩子迁移

- 旧 decorator 无需修改。
- 旧 handler 参数无需修改。
- 每个旧钩子只触发一次。
- 修改 request / response / result 的旧插件行为仍然生效。
- `stop_event()` 仍能在原来允许的位置终止传播。
- command、regex、permission、session plugin filtering 不发生行为回归。
- 旧插件通过 `event.send(...)` 发送消息仍可工作。

## 暂不解决的问题

一期计划暂不确定：

- 新插件协议最终名称和完整类型系统。
- system / heartbeat 输入应触发哪些人格行为。
- 插件 patch / contribution 的最终合并协议。
- 插件隔离、权限和资源配额。
- 系统增强插件的完整生命周期。
- 旧插件兼容支持的长期截止时间。

这些问题不能阻止 Input Bus 和旧钩子兼容层先落地。

## 下一步

按本计划，下一步不是立即移动钩子，而是：

```text
先补旧钩子兼容测试
  -> 再实现 Input Bus 最小兼容入口
  -> 验证行为完全不变
  -> 然后开始逐个迁移人格增强钩子
```

这样，一期始终以“旧插件还能按原来的方式工作”为判断标准，同时让每一次迁移都逐步进入最终运行时边界。
