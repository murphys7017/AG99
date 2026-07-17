# 执行器解耦前置准备计划

本文记录 Yakumo 下一阶段的总体工作计划。当前阶段只为未来解耦 Core
执行器建立可靠依据，不直接实现完整的 `ExecutionBackend`、外部能力网关或新的
Subagent Runtime。

本文是计划文档，不描述已经完成的运行时能力。当前消息流程以
`execution-backend-flow.mmd` 所记录的源码事实为准。

## 已确认的设计边界

后续工作以以下边界为前提：

- `Personal Runtime` 是控制层。它接收官方 Pipeline 处理后的事件，管理本轮状态、
  插件协作、路由、Core 委派和多轮任务。
- `Personal Expression` 位于 Output 之前。它只负责把已经确定的事实和结果转换成
  统一人格表达，不拥有业务插件执行和 Core 任务状态。
- 官方插件和新插件默认逻辑归属 `Personal Runtime`。插件作者和用户可以在未来
  显式允许某项能力挂载 Core，但 Core 不默认继承全部插件。
- 官方插件 Handler 当前在 Router 之前执行。准备阶段不移动 Handler，不改变
  filter、priority、`yield`、`stop_event`、`ProviderRequest` 或发送语义。
- Prompt 仍遵循统一管线：Collector 收集事实，Builder 形成规范 ContextPack，
  Projection 按目标裁剪，Renderer 生成模型请求。执行器不重新查询或拼接上下文。
- Subagent 的定义、任务和完成事件长期应由 Personal Runtime 管理；Core 可以请求
  委派，但不拥有 Subagent 生命周期。准备阶段只盘点现有 Handoff 依赖，不迁移实现。
- Native AstrBot Agent 是所有后续改造的行为基线。任何抽象都必须先证明能够完整
  表达当前路径，而不是要求当前路径迁就一个尚未实现的外部执行器。

现有文档和代码中仍广泛使用 `Persona Runtime`。准备阶段会建立术语映射，但不为了
统一命名进行大范围重命名。

### 当前术语映射

| 设计术语 | 当前主要实现 | 当前边界 |
| --- | --- | --- |
| Personal Runtime | `InteractionMiddleware`、`ProcessStage` 中的 Interaction 接缝及其 turn 编排 | 控制本轮路由、Core 委派、完成权和输出协作；尚未成为官方插件工具的默认 Action Runtime |
| Personal Expression | `InteractionPersonaRuntime`、`InteractionExpressionAgent` | 把即时回复、Core 结果、插件 persona 输出和流式插话转换成统一人格表达 |
| Native Core | `AgentRequestSubStage`、`InternalAgentSubStage`、`build_main_agent`、原生 AgentRunner | 执行当前 Provider/Tool Loop，并继续承载现有插件工具、Skills、Knowledge、MCP 和 Subagent |

这里的“官方插件默认逻辑归属 Personal Runtime”是目标所有权，不表示当前插件 Tool、
Prompt Hook 和 Agent Hook 已经从 Native Core 迁移。准备阶段必须先记录现有行为，再决定
未来映射。

## 本阶段目标

本阶段只完成三类工作：

1. 确定当前执行链的真实依赖关系。
2. 从整体架构、插件兼容和运行时语义三个角度完成审阅。
3. 修复审阅中确认的现有问题，并建立后续改造所需的验证基线。

本阶段结束时，应能准确回答：

- 替换 Native AgentRunner 时，哪些能力天然不受影响？
- 哪些能力依赖 `ProviderRequest`、Tool Loop 或 Runner 内部状态？
- 旧插件的每一种 Hook 和输出路径应由哪个运行时继续承载？
- Prompt、工具、知识库、Skills、MCP 和 Subagent 如何被当前 Core 使用？
- 哪些依赖可以直接保留，哪些需要适配，哪些确实需要未来的新协议？
- 在不改变现有行为的前提下，最小可行的执行器边界应位于哪里？

## 非目标

准备阶段明确不做以下工作：

- 不实现 Codex、OpenCode 或其他新执行器。
- 不创建只有接口、没有真实调用方的 `ExecutionBackend` 抽象。
- 不把现有插件统一转换成 MCP。
- 不移动官方插件 Handler 的调用位置。
- 不重写官方 `EventType`、装饰器和 `Context` 公共接口。
- 不让 Router 接收工具 Schema 或承担插件规划。
- 不把业务插件注册到 Personal Expression。
- 不重写 `HandoffTool`、`FunctionToolExecutor` 或后台 Subagent 唤醒链。
- 不以兼容未来架构为理由改变当前 Native Core 的可见行为。

## 第一阶段：建立源码基线

以代码而不是旧文档为依据，固定当前主路径：

```text
Platform / EventBus / Pipeline
  -> ProcessStage / Personal Runtime control boundary
       -> Plugin Handler
       -> Personal Runtime route / planning
       -> Native or third-party Core path
  -> Personal Expression
  -> Output Runtime
  -> Postprocess / Memory / Conversation
```

需要完成：

- 校正当前消息流程图，标明同步、异步、旁路和完成权。
- 为普通消息、插件直接回复、插件 `ProviderRequest`、Core 非流式、Core 流式、
  Core 失败、Subagent 前台和 Subagent 后台建立基线场景。
- 记录每个场景中的 Hook 调用顺序、Prompt 构建次数、工具来源、输出次数、
  finalized material 和 postprocess 触发情况。
- 将已有测试映射到这些场景，标出没有覆盖的关键路径。

这一阶段不修复行为；发现的问题进入审阅清单。

## 第二阶段：依赖关系盘点

### 1. 执行主链依赖

盘点以下对象之间的实际调用和状态传递：

- `ProcessStage` 与本地、第三方 Agent SubStage。
- `build_main_agent`、Provider 选择、`ProviderRequest` 和 Prompt Apply。
- AgentRunner、AgentContext、FunctionToolExecutor 和工具结果。
- Conversation、session lock、active runner、follow-up 和取消机制。
- streaming、thinking、tool-running、错误和最终结果。

### 2. 插件依赖

按行为而不是插件名称分类：

- 消息 Handler、command、filter。
- `event.send()`、streaming、`yield MessageEventResult` 和主动发送。
- `yield ProviderRequest` 与插件自发 LLM 请求。
- Prompt Collector、`OnLLMRequest`、`OnLLMResponse`。
- LLM Tool、工具调用前后 Hook、Agent begin/done Hook。
- 结果装饰、发送后、postprocess 和 lifecycle observer。
- 直接导入 `astrbot.core.agent.*` 或特定 Runner 客户端的实现。

每项依赖都记录：

```text
当前调用位置
公开 API 或内部 API
输入与输出类型
是否修改共享状态
是否具有外部副作用
是否依赖 Native Runner
Personal Runtime 默认归属
Core 挂载的未来必要条件
当前测试覆盖
```

### 3. Prompt 与能力依赖

确认当前 Core 获得以下能力的完整路径：

- system、persona、history、memory、group context 和 explicit context。
- Plugin Tools、Skills、Knowledge Base、MCP、Web Search、Sandbox 和 Cron。
- CoreTaskSpec 与 Personal Runtime/Core 协作提示。
- Provider Renderer、结构化输出能力和多模态输入。

重点确认哪些内容属于 ContextPack 事实，哪些属于 Core Profile，哪些仍通过
`ProviderRequest` Hook 在 Apply 之后修改。

### 4. Subagent 依赖

单独记录：

- 配置和 `@agent` 如何形成 `HandoffTool`。
- Handoff Tool Schema 如何进入主 Agent。
- `FunctionToolExecutor` 如何识别并执行 Handoff。
- Subagent 如何选择 Provider、Prompt、工具和 begin dialogs。
- 前台结果如何返回父 Agent。
- 后台任务如何生成 task id、保存结果并重新唤醒主 Agent。
- Subagent 对 `Context.tool_loop_agent()`、Native Runner 和 Cron 事件的直接依赖。

本阶段只形成依赖图和兼容矩阵，不设计替代实现细节。

## 第三阶段：整体审阅

审阅按以下顺序进行：

### 1. 边界审阅

- Personal Runtime 是否只承担控制职责，是否正在吸收执行器内部职责？
- Personal Expression 是否只消费待表达材料，是否存在业务调用或事实改写？
- Core 是否仍隐式拥有人格、插件状态、会话完成权或输出发送权？
- Prompt Pipeline 是否保持“收集一次、按目标投影”的唯一入口？

### 2. 插件兼容审阅

- 插件输入、Handler 顺序和控制语义是否仍与官方一致？
- Interaction Output 接管对 `OnDecoratingResult` 等官方 Hook 有何实际影响？
- 旧 `OnLLMRequest` 应对应哪一种模型调用，是否可能被重复调用？
- 默认 Personal Runtime 与显式 Core 挂载是否能够区分，而不复制插件状态？
- 内置插件是否依赖公开 API，还是直接依赖特定 Runner 状态？

### 3. 执行语义审阅

- Core 正常、流式、取消、超时、工具失败和 Provider 失败是否形成统一结果？
- Core 提前完成与推测式 Personal Expression 是否存在竞态？
- 一次逻辑回复是否可能被插件、Core 和 RespondStage 重复发送？
- 后台任务完成后是否能够恢复正确的父任务和人格主体？

### 4. 可替换性审阅

审阅只输出未来边界的必要能力，不立即创建接口。至少确认未来执行器需要表达：

- 任务输入和已渲染 Prompt。
- 可调用能力清单及调用返回。
- 流式文本、思考、工具调用、进度、完成、失败和取消事件。
- 会话、任务和子任务身份。
- 执行器能力声明，例如 tools、multimodal、streaming、subagent 和 cancellation。

## 第四阶段：必要修复

只有满足以下条件的问题才进入准备阶段修复：

- 已由源码和测试确认，而不是为未来接口做猜测。
- 当前 Native 路径已经存在错误、重复、遗漏或兼容退化。
- 修复范围局部，不要求先建立完整执行器抽象。
- 可以通过自动化测试证明修复前后的语义。

允许的工作包括：

- 补齐缺失的插件 Hook 兼容或明确记录无法兼容的原因。
- 修复重复发送、错误完成权、状态泄漏和错误恢复问题。
- 为 Runner、工具、Subagent 和 Prompt 边界补充诊断信息。
- 将无意泄漏到业务层的 Runner 私有读取收口到现有门面。
- 补充契约测试、调用顺序测试和端到端基线测试。

不允许借此阶段提前实现 Capability Gateway、远程协议或新的 Backend 层。

## 第五阶段：准备度复核

前置准备完成后形成一份准备度报告，至少包含：

- 当前依赖关系图。
- 插件兼容矩阵。
- Hook 调用时序表。
- Prompt 与能力注入路径表。
- Subagent 前台/后台生命周期图。
- 已确认问题、已修复问题和保留风险。
- Native 行为基线测试清单。
- 未来最小执行器边界建议及其证据。

只有以下条件同时满足，才进入正式执行器解耦：

- 所有 Core 依赖都有明确所有者和调用方向。
- 旧插件当前行为有基线测试，未来默认映射到 Personal Runtime 的范围已经明确。
- 显式 Core 能力所需的桥接范围已经确定。
- Subagent、后台任务和主动唤醒的生命周期已经画清楚。
- Native Runner 的正常、流式、工具、失败和取消路径均有基线。
- 关键 `event.extra` 依赖已经盘点，完成权与任务身份均有明确 owner 和测试保护。
- 审阅结论能够证明抽象边界来自现有需求，而不是预设外部执行器形状。

## 计划产物

准备阶段预计产出：

1. 源码依赖图和调用时序。
2. 插件、Hook、Prompt、Tool、Subagent 兼容矩阵。
3. 分严重度排列的架构审阅结论。
4. 小步修复提交及对应回归测试。
5. 执行器解耦准备度报告。
6. 经复核后的正式实现计划。

正式实现计划必须在准备度复核后单独确认，不能把本文直接当作实施授权。

## 当前进度快照

第一轮准备工作已经完成以下内容：

- 根据源码重画当前消息流程，移除未实现目标态。
- 建立 Personal Runtime、Personal Expression 和 Native Core 的当前术语映射。
- 完成插件、Prompt/Tool、Native Core 和 Subagent 的第一轮依赖盘点。
- 恢复 Interaction 插件普通结果与 Core 非流式最终 Persona 文本的回复安全和
  `OnDecoratingResult` 兼容，同时继续避免重复普通装饰。
- 将 RespondStage 驱动的 Interaction 最终提交延迟到
  `OnAfterMessageSent -> visible completion -> AFTER_MESSAGE_SENT 调度` 之后。
- 为 Hook 最终文本、内容安全抑制、发送后停止和 Turn 完成顺序补充回归测试。

当前仍不满足正式执行器解耦条件，保留事项包括：

- 官方 Prompt Extension、Plugin Tool、LLM/Agent Hook 默认归属 Personal Runtime 的
  具体映射尚未设计和实现。
- Core 流式输出仍无法在首块发送前向 `OnDecoratingResult` 提供完整最终文本。
- Subagent 前台、后台、父任务恢复和主动唤醒仍需要更完整的调用时序与基线测试。
- Native Core 的取消、超时、Provider 错误和 Tool 错误还需要统一准备度矩阵。
- `Context.send_message()` 主动消息继续旁路当前 Turn，是否统一接管尚未决定。

第一轮详细结论见
[执行器解耦依赖审阅](./execution-backend-dependency-review.md)。
