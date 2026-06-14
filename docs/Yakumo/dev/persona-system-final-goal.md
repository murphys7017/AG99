# Persona Runtime Final Goal Consensus

这份文档记录 Yakumo / AstrBot 二期目前确认的最终目标和主运行时边界。

它不是当前实现说明，也不是具体的插件接口规范。本文确认主链路如何从任务型对话走向人格型运行，并记录已经确认的插件总体模型；具体 hook、数据结构和调用协议会在下一步单独设计。

## 目标

Yakumo 二期的目标，是把当前偏任务型对话的 AstrBot，逐步改造成更拟人化、可长期运行的人格系统。

这里的拟人化不是简单让回复语气更像人，而是让系统结构从：

```text
收到消息
  -> 调用 middleware 或 core
  -> 生成并发送回复
```

演进为：

```text
输入进入系统
  -> Input Gateway 判断要做什么
  -> Persona Runtime 决定怎么像这个人一样回应
  -> Executor Runtime 在需要时实际执行任务
  -> Output Runtime 把 Persona Runtime 的表达发出去
  -> FinalizedMaterial 交给 Postprocess / Memory / Trigger
```

核心边界是：

```text
Input Gateway 决定“要做什么”。
Persona Runtime 决定“怎么像这个人一样回应”。
Executor Runtime 负责“实际执行”。
Output Runtime 负责“把 Persona Runtime 的表达发出去”。
```

## 目标流程

当前确认的目标流程是：

```text
Input Bus
  -> Input Gateway
      -> 判断 input_kind
      -> 如果是用户输入：
          并发启动：
            A. Persona Runtime 生成 first_response
            B. Input Gateway 做 route / executor decision
  -> first_response 出来后立刻交给 Output Runtime 发给用户
  -> route 决定是否进入 Executor Runtime
  -> Executor Runtime 执行任务
  -> Executor 中间产出 / 最终结果
      -> Persona Runtime 观察、理解、包装
      -> Output Runtime 发送
  -> FinalizedMaterial
  -> Postprocess / Memory / Trigger
```

这个流程里，`first_response`、`stream_interjection`、`finalizer` 不应该被看成彼此独立的系统。它们本质上是 Persona Runtime 在不同阶段被调用。

## Input Bus

Input Bus 是输入事件进入系统后的传递通道。

它负责承载不同来源的输入，例如平台消息、WebUI 输入、内部事件、后续可能存在的定时触发或后台信号。

Input Bus 不负责人格表达，也不负责执行任务。它只负责把输入送到 Input Gateway。

## Input Gateway

Input Gateway 是输入侧判断和调度层。

它负责判断输入是什么、这一轮要做什么、是否需要进入 Executor Runtime。

当前确认的职责：

- 接收 Input Bus 传入的事件。
- 判断 `input_kind`。
- 对用户输入启动 Persona Runtime 的 first response。
- 同时做 route / executor decision。
- 决定是否需要进入 Executor Runtime。
- 将执行请求交给 Executor Runtime。
- 保持输入侧调度和人格表达解耦。

Input Gateway 不负责“怎么像这个人格一样说话”。它的重点是判断和调度。

## Persona Runtime

Persona Runtime 是拟人化表达中心。

它负责所有“怎么像这个人一样回应”的部分。它不只是最后润色结果，也包括第一响应、执行过程中的提示、执行结果包装，以及最终材料整理。

当前确认的阶段性入口：

```text
persona.on_user_input(observation)
  -> 生成第一句响应

persona.on_executor_started(execution_request)
  -> 可选生成“我开始处理了”的表达

persona.on_executor_progress(execution_delta)
  -> 监测中间输出，决定是否包装成过程提示

persona.on_executor_result(execution_result)
  -> 把执行结果转成人格化回复

persona.on_turn_finalize(turn_material)
  -> 形成 FinalizedMaterial，给记忆和后处理使用
```

因此：

```text
first_response
stream_interjection
finalizer
```

都应该逐步收口为 Persona Runtime 的阶段性能力，而不是继续作为散落在不同模块里的独立概念。

Persona Runtime 会使用人格、记忆、状态和上下文，但它不应该成为所有数据的所有者。长期人格数据、记忆数据、provider 和执行能力仍应由各自系统管理。

## Executor Runtime

Executor Runtime 是实际执行层。

它负责完成需要执行能力支持的任务，例如工具、检索、文件、代码、长推理、外部动作或其他复杂任务。

Executor Runtime 的职责是“把事情做完”，不是决定如何以人格方式表达结果。

执行过程中的中间产出和最终结果，应回到 Persona Runtime：

```text
Executor progress / result
  -> Persona Runtime 观察、理解、包装
  -> Output Runtime 发送
```

这样可以避免执行层直接绕过人格表达，也能让长期人格连续性留在 Persona Runtime 中。

## Output Runtime

Output Runtime 是输出投递层。

它负责把 Persona Runtime 已经形成的表达发送到合适目标，而不是自己决定人格化表达内容。

Output Runtime 的职责包括：

- 发送普通聊天回复。
- 发送 streaming 回复。
- 处理 TTS / voice 等输出通道。
- 处理本地表现通道。
- 发送任务状态或本地通知。
- 记录输出结果，供 FinalizedMaterial 使用。

当前只确认方向：输出层应该成为统一出口，逐步减少各处直接 `event.send(...)`。

## FinalizedMaterial

FinalizedMaterial 是一轮交互结束后的稳定材料。

它应该表达：

- 本轮输入是什么。
- Persona Runtime 产生了哪些表达。
- Executor Runtime 是否执行了任务。
- 执行过程和结果是什么。
- 用户实际看到或没有看到哪些输出。
- 哪些内容应该进入记忆、人格状态、后处理或后续触发。

后续 `Postprocess`、`Memory`、`Trigger` 应消费 FinalizedMaterial，而不是各自从 event、history、visible output 里反推。

```text
本轮发生了什么
  -> FinalizedMaterial
  -> Postprocess / Memory / Trigger
```

## AstrMessageEvent

`AstrMessageEvent` 的兼容性不能动。

现有平台适配器、插件、pipeline 和测试都依赖它的属性和函数，例如：

- `message_str`
- `message_obj`
- `session_id`
- `unified_msg_origin`
- `get_messages()`
- `send(...)`
- `send_streaming(...)`
- `complete_visible_turn(...)`
- `set_extra(...)`
- `get_extra(...)`

所以第一阶段不能另起炉灶替代它，也不能改掉这些公开接口。

当前共识是：

```text
AstrMessageEvent 继续作为兼容外壳
外部 runtime 模块由 lifecycle / gateway 创建和持有
AstrMessageEvent 内部只绑定这些模块的引用
旧参数、旧函数名、旧调用方式保持可用
```

推荐方向是先引入轻量的 `EventRuntimeRefs`：

```text
EventRuntimeRefs
  -> InputGateway / InputRuntime
  -> OutputRuntime / OutputGateway
  -> EventContextResolver
  -> EventStateStore
```

这些 refs 的作用只是把 event 接到外部 runtime，不代表所有能力都塞进 event。

## Cost / Context Runtime

长期运行的人格系统不能无限制调用模型。

Reasonix 这类项目在成本控制和上下文稳定性上的经验可以作为提醒：Yakumo 不需要复制它的 agent loop，但需要严肃对待 context lane、stable prefix、budget gate 和 usage ledger。

后续在加入默认小模型、心跳、潜意识、后台反思之前，需要先考虑：

- 不同模型角色是否需要不同 context lane。
- 哪些内容是稳定 prefix，哪些内容是动态上下文。
- 什么时候允许调用模型。
- 后台能力是否经过 budget gate。
- provider / model 的成本信息如何记录。
- 调用结果如何进入 usage / cost ledger。

这些是后续设计约束，不是当前已经完成的实现。

## 插件总体模型

二期不承诺兼容市面上所有插件依赖的任意内部实现，但一期优先兼容 AstrBot 已经公开提供的旧插件钩子及其既有语义。

迁移方式不是让旧插件立即改用一套全新协议，而是保留旧 decorator、handler 参数和控制行为，将旧钩子的内部触发位置逐步桥接到新运行时。只有旧系统无法表达必要能力时，才增加新的扩展点。

目前确认将插件能力分成三个方向：

```text
1. 人格 / 对话增强
2. 执行能力增强
3. 系统能力增强
```

前两个方向已经形成基本思路；系统能力增强暂时保留开放，后续单独设计。

### 人格 / 对话增强

人格增强插件可以沿着一次交互的不同阶段观察和修改材料。

计划提供扩展点的位置包括：

```text
Input Bus
  -> 消息刚进入系统

Input Gateway
  -> 输入完成初步整理
  -> route / executor decision 前后

Persona Runtime
  -> 人格请求发起前
  -> first_response 生成后
  -> Executor 开始时
  -> Executor 中间产出到达时
  -> Executor 最终结果到达时
  -> FinalizedMaterial 形成前
```

插件可以在允许的阶段：

- 读取输入、上下文、决策和生成材料。
- 补充人格、记忆、状态或 Prompt 所需内容。
- 修改阶段性草稿。
- 观察 Executor 的中间产出和最终结果。
- 补充 FinalizedMaterial 所需材料。

具体 hook 名称、参数、可修改字段、执行顺序、超时和失败策略尚未定稿，将在下一步详细设计。

### 并发路径的修改规则

对于用户输入，Persona Runtime 的 first response 与 Input Gateway 的 route decision 会并发执行。

因此插件对两条路径共享输入的修改，必须在并发启动前完成：

```text
raw input
  -> hooks / materialization
  -> stable InputObservation
      -> Persona Runtime first_response
      -> Input Gateway route decision
```

并发开始后，两条路径应读取同一份稳定 observation，不能同时原地修改同一个共享对象。

后续 hook 设计应优先采用可记录的 patch / contribution，再由 runtime 按顺序合并，避免插件之间出现不可诊断的覆盖和竞态。

### 插件统一输出入口

插件不应把平台 `event.send(...)` 作为新系统里的主要主动输出方式。

新系统应提供统一输出函数。插件提交内容后，默认先交给 Persona Runtime 生成符合当前人格的回复，再交给 Output Runtime 发送：

```text
plugin output request
  -> Persona Runtime 拟人化表达
  -> Output Runtime 投递
  -> FinalizedMaterial
```

统一输出请求需要支持两种模式：

```text
persona
  -> 默认模式
  -> 内容先经过 Persona Runtime

direct
  -> 不进行人格化改写
  -> 仍然经过 Output Runtime 和 FinalizedMaterial
```

`direct` 只表示跳过人格化处理，不表示绕过统一输出链路。它仍然需要保留输出目标、可见性、turn identity、投递结果和 finalized material。

统一输出函数的具体名称和参数将在 hook 设计之后继续确认。

### 执行能力增强

执行能力增强插件更接近 `tool`、`skill` 或可注册的 execution capability。

基本流程是：

```text
plugin capability
  -> 注册到 Executor Runtime
  -> Executor Runtime 按任务调用
  -> ExecutionResult
  -> Persona Runtime 理解和包装
  -> Output Runtime 发送
```

其中：

- `tool` 更接近一个有明确输入输出的具体动作。
- `skill` 更接近一套指令、知识或多步执行方法。
- 具体执行后端也可以作为 Executor Runtime 可选的执行能力。

执行能力负责返回结果材料，不负责决定最终怎样以人格方式回复用户。

### 系统能力增强

系统增强类插件的边界暂时不定稿。

目前只确认：它不一定属于某一轮对话或某一次任务执行，更可能为整个 runtime 提供输入来源、输出通道、存储、调度、provider、观测或其他基础服务。

这部分将在人格增强 hooks 和执行能力接口确定后再单独讨论。

## 第一阶段建议

当前最稳的第一阶段路线是：

```text
1. 建立旧插件 hook 行为基线和兼容测试
2. EventRuntimeRefs
3. Input Bus / InputEnvelope / InputKind
4. Input Gateway / InputObservation
5. 逐步迁移人格增强 hooks
6. EventStateStore
7. OutputGateway / OutputRuntime
8. 迁移 Executor Runtime 的 tool / skill / capability hooks
9. 最后讨论系统增强类插件
```

详细迁移步骤见 `legacy-plugin-hook-migration-plan.md`。

也就是说，先以旧插件兼容测试约束改造，再从 Input Bus 接稳输入边界；每迁移一个 hook，都要证明旧插件调用方式和控制语义没有被破坏。

## 非目标

当前阶段不追求：

- 立刻废弃或强迫插件改写 AstrBot 现有插件 API。
- 立刻重写全部平台适配器。
- 立刻实现完整后台人格循环。
- 立刻把 middleware 变成包办所有事情的大对象。
- 立刻定稿全部 hook、插件协议和系统增强接口。
- 让任何扩展能力绕过人格层直接消费所有原始输入或直接发送最终输出。

这份文档的作用只是把主运行时共识放在同一页上，方便后续继续讨论插件到底应该怎么做。
