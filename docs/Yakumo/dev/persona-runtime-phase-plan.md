# Persona Runtime Phase Plan

这份文档记录 Yakumo 下一阶段的实施计划。它不是当前代码说明，也不是最终目标态说明。

当前共识：

- 第一阶段先完成输入输出解耦。
- interaction middleware 后续扩展为 `Persona Runtime Shell`，作为 Adapter 与 Core 之间的人格运行层。
- runtime 需要重新整理，但第一步不是服务化拆分，而是先把 Input / Persona / Core / Output 的边界接稳。
- `AstrMessageEvent` 的外部 API 必须保持兼容；重构方式不是删除或改名，而是让它逐步成为兼容外壳，内部委托外部 runtime 模块。

## 计划主线

目标流程：

```text
Adapter
  -> Input Runtime / Observation
  -> Persona Runtime Shell
      -> Core Agent / Tools / Capabilities when needed
  -> Output Runtime / Output Gateway
  -> Finalized Material
  -> Postprocess / Memory / Persona State Update
```

这个流程里，复杂任务进入 Core；普通寒暄、轻量反应、presence、状态表达可以由 Persona Runtime Shell 决定是否直接处理或只产出 output intent。

## AstrMessageEvent 兼容外壳

`AstrMessageEvent` 不能直接推倒重写。插件、平台适配器、pipeline、测试和外部生态都依赖它的既有形状。

必须保持兼容的外部接口包括：

- `event.message_str`
- `event.message_obj`
- `event.unified_msg_origin`
- `event.session_id`
- `event.get_messages()`
- `event.get_sender_id()`
- `event.get_sender_name()`
- `event.send(...)`
- `event.send_streaming(...)`
- `event.complete_visible_turn(...)`
- `event.set_extra(...)`
- `event.get_extra(...)`

但 `AstrMessageEvent` 当前混合了承载输入、发送输出、传递上下文、保存运行状态和兼容 extras 等多种职责。
后续不应继续把更多输入、输出、人格和运行状态字段直接塞进它本体。

### 当前情况

当前 `AstrMessageEvent` 不是单纯的输入消息对象。它同时承担：

- 输入消息载体：`message_str`、`message_obj`、sender、group、session、`unified_msg_origin`
- 输出发送接口：`send(...)`、`send_streaming(...)`、`complete_visible_turn(...)`
- 上下文传递：`set_extra(...)` / `get_extra(...)`
- pipeline 运行状态：result、wake 状态、插件启用状态、LLM 调用标志
- trace / diagnostics / temporary files

同时，很多平台适配器都有自己的 `AstrMessageEvent` 子类，并重写发送相关函数。

典型形态是：

```python
class XxxMessageEvent(AstrMessageEvent):
    async def send(...):
        ...
        await super().send(...)

    async def send_streaming(...):
        ...
        await super().send_streaming(...)
```

这些平台发送实现处理了大量平台差异，例如：

- 普通消息发送
- streaming 追加
- draft / edit / finish marker
- 平台 extras
- 文件、图片、语音、卡片等特殊消息类型
- 发送完成后的兼容副作用

此外，interaction middleware 当前还会在运行时动态拦截事件实例方法：

```python
event.send = MethodType(send_wrapper, event)
event.send_streaming = MethodType(send_streaming_wrapper, event)
event.complete_visible_turn = MethodType(complete_visible_turn_wrapper, event)
```

这个拦截是当前 `InteractionOutputController` 接管 interaction turn 输出语义的关键路径。

所以 `AstrMessageEvent` 的输出侧兼容点至少有三层：

```text
平台 event 子类 send / streaming 实现
  -> AstrMessageEvent 基类兼容钩子
  -> interaction middleware 动态拦截
  -> 后续 OutputGateway / OutputRuntime
```

这也是为什么不能直接把 `event.send(...)` 改成全新调用协议，也不能一次性删除 middleware 的 send interception。

目标做法是：

```text
外部 runtime 模块 / 服务
  -> 由 lifecycle / gateway 创建和持有
  -> 通过轻量引用绑定到 AstrMessageEvent
  -> AstrMessageEvent 保持旧 API
  -> 旧 API 内部逐步委托给外部模块
```

也就是说，`AstrMessageEvent` 继续作为兼容外壳和事件桥，不成为新的全局大对象。

推荐形态：

```python
@dataclass(slots=True)
class EventRuntimeRefs:
    input_runtime: InputRuntime | None = None
    output_gateway: OutputGateway | None = None
    context_resolver: EventContextResolver | None = None
    state_store: EventStateStore | None = None
```

`AstrMessageEvent` 内部只保存引用：

```python
event.bind_runtime_refs(refs)
```

旧接口保持原名和原参数，但实现可以逐步委托：

```python
await event.send(message)
# -> refs.output_gateway.send(event, message)

await event.prepare_input()
# -> refs.input_runtime.accept_event(event)
```

短期内，未接入外部模块的接口继续走旧实现。这样可以边接边迁移，不会一次性破坏旧插件。

### 共享模块与事件私有状态

重构时要区分“共享 runtime 模块”和“每个事件自己的状态”。

共享模块由 lifecycle / gateway 创建，不应该每个 event 重复创建：

- `InputRuntime`
- `OutputGateway`
- `EventContextResolver`
- `EventStateStore`
- `PersonaResolver`
- `MemorySnapshotReader`
- `ProviderGateway`
- `CapabilityRegistry`

每个 event 自己只持有或引用本轮状态：

- `InputObservation`
- `TurnState`
- `OutputLedger`
- `CompletionState`
- `Diagnostics`

这能避免 `AstrMessageEvent` 自己创建和拥有所有子系统，也能避免每条消息重复初始化共享服务。

### extras 的定位

`event.set_extra(...)` / `event.get_extra(...)` 必须保留，但目标定位应从“主状态通道”降级为“兼容 bag”。

迁移原则：

- 新代码优先读写结构化 runtime state。
- 旧代码仍可读写 extras。
- 关键状态在过渡期可以双写：结构化 state 为主，extra 为兼容镜像。
- 后续逐步减少 `_interaction_*`、`_input_*` 等临时 key 的直接散落使用。

### 迁移注意事项

1. 不改变 `AstrMessageEvent` 的外部函数名、参数和常用属性语义。

2. 不要求平台适配器第一阶段统一改写。平台子类的 `send(...)` / `send_streaming(...)` 仍是平台差异的合法承载点。

3. OutputGateway 第一阶段只能包裹、委托和记录 ledger，不能直接替代所有平台发送实现。

4. middleware 的动态 send interception 是当前主路径的一部分。后续可以把它替换成正式 OutputGateway hook，但不能在 Input Runtime 阶段删除。

5. 新增 runtime refs 时，要支持未绑定 refs 的事件继续按旧逻辑运行。

6. 对 interaction turn，过渡期允许双写状态：`EventStateStore` / structured state 是新主路径，`event.extra` 是兼容镜像。

7. 对非 interaction 事件，旧 core pipeline 必须继续可用；InputRuntime 接入不能强制所有平台立即启用 interaction middleware。

8. 任何迁移都要优先验证 WebChat、Telegram、QQ/aiocqhttp、Lark、WecomAIBot 这类重写 streaming 或 completion 语义的平台。

## Phase 1: 输入输出解耦

第一阶段先接稳输入和输出，不急着实现完整心跳、潜意识或长期后台人格循环。

Phase 1 的关键不是新建一套绕开 `AstrMessageEvent` 的入口，而是让 `AstrMessageEvent` 通过 runtime refs 连接到外部 Input / Output 模块。

### Input Runtime

Input Runtime 负责把外部和内部输入整理成统一 observation。

输入来源包括：

- 平台适配器消息
- WebUI 输入
- 语音、图片、文件、引用消息
- 主动事件
- 后续心跳、idle tick、任务状态、反思触发等内部信号

Observation 至少应表达：

- source / platform / session / conversation
- sender / audience / visibility
- privacy / permission / importance
- raw input 与 materialized input
- attachments / quoted material
- 初步 route / gate 线索

### Output Runtime

Output Runtime 负责把内部 output intent 落到具体目标。

输出目标至少应区分：

- chat reply
- streaming chat reply
- voice / TTS
- Desktop Body / Presence Client
- task status
- local-only notification
- silent finalized material

聊天窗口回复只是 output target 之一，不再是唯一输出形态。

### Finalized Material

Output Runtime 完成后必须产出 finalized material。

Memory / postprocess / persona state 更新只消费 finalized material，不从临时 visible output 或平台发送结果反推完整回合语义。

## Phase 1 建议实施顺序

### Phase 1A: 绑定 runtime refs

先给 `AstrMessageEvent` 增加轻量绑定能力：

- `bind_runtime_refs(...)`
- `get_runtime_refs(...)`
- 可选的 `prepare_input(...)`

这一阶段不改变任何旧接口行为。

### Phase 1B: 接入 InputRuntime

实现 `InputRuntime.accept_event(event)`，生成 `InputObservation`。

过渡期写入两处：

- 结构化 state / observation 引用
- `event.extra["_input_observation"]` 兼容镜像

现有 middleware 继续使用 `AstrMessageEvent` 驱动，行为不变。

### Phase 1C: 迁移入站 materialization

把 interaction middleware 中的入站 path mapping、Record 规范化、STT 转写等输入整理逻辑迁入 InputRuntime。

middleware 不再自己做输入整理，而是调用：

```python
observation = await event.prepare_input()
```

或：

```python
observation = await input_runtime.accept_event(event)
```

### Phase 1D: 引入 EventStateStore

将 `_turn_id`、interaction decision、completion state、failure ledger 等运行时状态逐步迁入 `EventStateStore`。

`event.extra` 保留兼容镜像。

### Phase 1E: 接入 OutputGateway

在不改 `event.send(...)` 外部调用方式的前提下，让 send / streaming 逐步委托给 OutputGateway。

旧平台 event 子类可以继续保留平台发送细节；OutputGateway 第一阶段只做统一调度和 ledger，不强行抹平所有平台差异。

## Phase 2: Persona Runtime Shell

在输入输出边界稳定后，interaction middleware 扩展为 `Persona Runtime Shell`。

它负责一轮人格运行：

- 接收 observation
- 组合 Effective Persona、memory snapshot、persona state、关系/话题状态和 capability context
- 判断 self reply / delegate to core / hybrid / local presence / silent
- 委托 Core 处理复杂任务
- 向 Output Runtime 提交 output intent
- 交付 finalized material 给 postprocess

它不应拥有长期数据本体：

- base persona 仍由 persona manager / repository 管理
- memory 仍由 memory service 管理
- persona state 后续由 `PersonaStateService` 管理
- provider / tools / skills / subagent 仍通过 gateway 或 capability registry 接入

## Phase 3: Background Mind

完成 Phase 1 和 Phase 2 后，再接默认小模型、心跳、潜意识和主动 presence。

这些能力不应绕过主链路，而应作为内部 observation / intent source 接入：

```text
heartbeat / idle tick / task state / reflection trigger
  -> internal observation
  -> Persona Runtime Shell
  -> output intent / silent material / persona state update
```

这样可以避免后台人格直接发消息、直接写 memory、或绕过隐私/可见性判断。

## 非目标

第一阶段不追求：

- 完整服务化拆分
- 完整人格反思系统
- 默认小模型常驻循环
- 直接把所有 middleware 状态升级成长期人格状态
- 让 AG99live 直接监听所有 session 原文

第一阶段只追求把输入、人格运行、核心执行、输出和 finalized material 的边界接稳。
