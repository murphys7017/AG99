# Post Process System Design

本文件定义 AstrBot 的 `Post Process System`。

它与以下文档平级配套：

- `docs/Yakumo/dev/persona-memory-system-design.md`
- `docs/Yakumo/dev/memory-system-design-spec.md`

三者关系应理解为：

- `Prompt System` 负责请求前上下文组织与 prompt 构建
- `Memory System` 负责记忆更新、存储、检索与状态沉淀
- `Post Process System` 负责请求后任务的统一编排与调度

本文件不讨论 memory 细节，也不直接讨论 prompt renderer，而是专注于：

- AstrBot 为什么需要独立 post process 系统
- 它与现有 hook / pipeline 的关系
- 它的抽象模型与调度职责
- 它如何为 memory、trace、stats 等后处理任务提供统一入口

## 一句话定位

`Post Process System` 是 AstrBot 在“请求完成之后”的统一编排层。

它不是新的底层事件总线，而是：

> 基于现有 AstrBot hook / event 时机，对回合后任务进行标准化调度的 orchestration layer。

## 一、为什么需要单独的 Post Process System

AstrBot 当前已经存在若干“请求后时机”：

- `EventType.OnLLMResponseEvent`
- `EventType.OnAfterMessageSentEvent`
- `MainAgentHooks.on_agent_done()`

这些时机本身是有价值的，但当前还存在几个问题：

### 1. 时机存在，但缺少统一编排

目前系统更像：

- 某个 hook 被触发
- 某段逻辑直接挂上去执行

缺少一层统一管理：

- 哪些后处理逻辑应该在什么 trigger 上执行
- 执行顺序是什么
- 失败如何隔离
- 是否允许并发
- 如何记录调试日志

### 2. Memory 不应直接拥有 post-turn 生命周期

如果没有独立 `Post Process System`，很容易变成：

- memory 自己监听 hook
- memory 自己控制回合后调度

这会导致 memory 过度膨胀，并且让未来其他回合后能力难以接入。

### 3. Prompt 不应承担请求后任务

Prompt System 的职责是：

- collect
- select
- render
- build

而不是：

- 在响应完成后写 memory
- 记录 trace
- 做统计上报

### 4. 回合后能力未来一定会增多

后续除了 `MemoryPostProcessor`，很可能还会出现：

- `TracePostProcessor`
- `StatsPostProcessor`
- `SummaryPostProcessor`
- `KnowledgeSyncPostProcessor`

因此，从一开始就抽出统一系统，比让每个模块各自挂 hook 更稳。

## 二、系统边界

## Post Process System 负责

- 接收来自现有 hook / event 的后处理触发
- 将触发点标准化为统一 trigger
- 构造统一的 `PostProcessContext`
- 找到应该执行的 post processors
- 管理顺序、失败隔离、日志记录

## Post Process System 不负责

- 不负责 memory schema 设计
- 不负责 prompt 渲染
- 不负责静态 persona 解析
- 不负责替换 AstrBot 原生 hook/event 机制
- 不负责直接持有 memory store

interaction middleware 接入后还需要额外明确：

- middleware 负责产出 finalized turn material
- postprocess 负责作为 completion consumer boundary
- memory service 负责实际记忆写入
- postprocess 不应为 interaction turn 自行从 visible outputs、provider request 或历史 extra 中反推完整 turn material

## 三、与现有 AstrBot 机制的关系

`Post Process System` 必须明确建立在 AstrBot 现有机制之上，而不是另起炉灶。

当前优先复用的现有时机：

### 1. `OnLLMResponseEvent`

含义：

- LLM / Agent 运行完成后触发

适合：

- trace 记录
- stats 记录
- 某些不依赖“消息真正已发送”的后处理任务

### 2. `OnAfterMessageSentEvent`

含义：

- 平台消息发送完成后触发
- 语义上覆盖非流式发送完成与流式发送完成
- 若发送后 hook 显式 stop，则 post process 不继续执行

适合：

- 依赖“消息已经成功送出”的后处理任务
- 更保守的 memory update

### 3. `MainAgentHooks.on_agent_done()`

含义：

- agent runner 完成后的统一 hook

作用：

- 是当前 internal / third-party agent 共同能走到的收口点之一
- 可作为接入 `OnLLMResponseEvent` 的上游实现基础

## 设计原则

- 底层继续使用 AstrBot 已有 hook / event
- 上层由 `PostProcessManager` 统一编排
- 不重复造底层事件机制

## 四、推荐抽象

## 1. `PostProcessTrigger`

表示“后处理在什么时机被触发”。

第一版建议只定义少量稳定 trigger：

- `on_llm_response`
- `after_message_sent`

未来如有需要，再扩展：

- `on_tool_end`
- `on_agent_done`

但第一版不建议把 trigger 设计得过多。

## 2. `PostProcessContext`

表示一次后处理调用中，所有 processor 可见的统一上下文。

建议至少包含：

- `event`
- `trigger`
- `provider_request`
- `llm_response`
- `conversation`
- `agent_stats`
- `timestamp`
- `debug_meta`

设计原则：

- 统一输入模型
- 尽量减少 processor 自己去各处取隐式依赖
- 允许部分字段为空，但字段语义要稳定

## 3. `PostProcessor`

表示一个独立的回合后处理单元。

建议它具备：

- 稳定 `name`
- 声明支持的 `trigger`
- 接收统一 `PostProcessContext`
- 独立异常处理

概念上可理解为：

- `MemoryPostProcessor`
- `TracePostProcessor`
- `StatsPostProcessor`

这类处理器的共同接口。

## 4. `PostProcessManager`

`Post Process System` 的核心对象。

职责：

- 注册 processors
- 按 trigger 建立映射
- 在 hook 触发时创建或补齐 `PostProcessContext`
- 顺序执行 processors
- 记录日志
- 隔离异常

它不负责：

- memory 业务逻辑
- prompt 业务逻辑
- 具体数据库写入逻辑

## 五、推荐调度模型

第一版建议采用保守模型：

### 1. trigger 映射

每个 processor 声明自己关心哪些 trigger。

例如：

- `MemoryPostProcessor` -> `after_message_sent`
- `TracePostProcessor` -> `on_llm_response`
- `StatsPostProcessor` -> `on_llm_response`

### 2. 顺序执行优先

第一版建议默认顺序执行 processors，而不是并发执行。

原因：

- 更容易调试
- 更容易保证日志顺序
- 更容易看清隐式依赖

并发可以留到后续在无依赖 processor 中逐步引入。

### 3. 失败隔离

每个 processor 的失败都不应阻断其他 processor。

要求：

- 单 processor 异常被捕获
- 记录日志
- 继续执行后续 processor

### 4. 可配置禁用

后续建议支持：

- 全局启停某个 processor
- 按 trigger 启停某个 processor

第一版可以先只做代码级注册控制。

## 六、与 Memory System 的关系

`Post Process System` 不等于 `Memory System`。

两者关系是：

- `Post Process System` 负责“什么时候调 memory”
- `Memory System` 负责“memory 怎么更新”

对应关系：

- `PostProcessManager` 调度 `MemoryPostProcessor`
- `MemoryPostProcessor` 负责桥接
- `MemoryService` 负责真正 update / consolidate / store

因此应避免：

- `MemoryService` 直接监听 AstrBot hook
- `MemorySystem` 直接拥有 post-turn orchestration

## 七、与 Prompt System 的关系

`Post Process System` 与 `Prompt System` 也是平级关系。

两者关系是：

- Prompt 负责请求前
- Post Process 负责请求后

对应链路：

请求前：

- `Prompt System <- MemorySnapshot / PersonaState / TopicState`

请求后：

- `Execution Result -> Post Process System -> MemoryPostProcessor -> Memory System`

这意味着：

- Prompt 不负责任何 post-turn update
- Post Process 不负责 prompt 组装

## 八、第一版推荐的处理器

第一版建议先聚焦最少量 processor。

### 1. `MemoryPostProcessor`

职责：

- 从 `PostProcessContext` 中提取 memory update 所需材料
- 组装 `MemoryUpdateRequest`
- 调用 `MemoryService.update(...)`

推荐 trigger：

- 首选 `after_message_sent`

原因：

- 语义更保守
- 能保证用户侧消息已送出

### 2. `TracePostProcessor`

职责：

- 记录请求后 trace / debug 信息

推荐 trigger：

- `on_llm_response`

### 3. `StatsPostProcessor`

职责：

- 记录请求后统计信息

推荐 trigger：

- `on_llm_response`

第一版即便暂时不实现 2、3，也建议在文档中保留其位置，避免 `Post Process System` 从一开始就被理解成 memory 专属层。

## 九、建议的最小实现顺序

### 第一步：定义抽象

先定义：

- `PostProcessTrigger`
- `PostProcessContext`
- `PostProcessor`
- `PostProcessManager`

### 第二步：接 AstrBot 现有 hook

优先打通：

- `OnLLMResponseEvent`
- `OnAfterMessageSentEvent`

让它们统一流向 `PostProcessManager.dispatch(...)`。

### 第三步：接入第一个 processor

优先接：

- `MemoryPostProcessor`

### 第四步：补日志与异常隔离

至少保证：

- processor 开始/结束日志
- trigger 日志
- 单 processor 异常隔离

## 十、最终结论

AstrBot 的 `Post Process System` 应被定义为：

> 一个建立在现有 hook / event 时机之上的统一后处理编排层，用于在请求完成后，以标准 trigger 和统一上下文模型，调度 memory、trace、stats 等独立 post processors。

它的意义不是替换现有 hook，而是把现有 hook 从“零散扩展点”提升为“稳定可演化的回合后运行阶段”。
