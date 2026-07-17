# Personal Runtime 前置依赖审阅

本文记录 Personal Runtime 前置主链第一轮源码盘点。它描述当前依赖和已经确认的修复
范围，不代表完整执行器接口已经确定。

当前消息时序以 `execution-backend-flow.mmd` 为准；总体准备步骤和进入正式实现的条件
见 `execution-backend-preparation-plan.md`。

## 审阅定位更新

本审阅最初围绕执行器解耦准备展开。后续整体审阅确认，Backend 是主链最后且相对简单
的替换点；当前优先级已经调整为清理它之前的过渡结构。

以下事实继续有效，但不再用于证明应尽快创建 Backend 接口，而用于确定哪些前置 owner
需要先迁移：

- Personal Runtime 当前仍是 event/turn 级协调器，不是稳定 Session Runtime。
- Output 接管依赖 event 方法替换和跨 Pipeline 回调。
- TurnState 与大量 extra 形成可写状态镜像。
- Planner 能力摘要与 Native Core 实际工具注入不是同一能力快照。
- ContextPack 单次合并不可变，但共享 context material 会被后续 Core enrichment 换版。
- Conversation、MemoryService 和 InteractionMemoryStore 仍有重叠。
- Local 与 Third-party Agent SubStage 使用不同准备链，不能作为未来 Backend 架构基础。

这些过渡结构不属于官方兼容面。迁移时保护公开插件、Pipeline、平台、配置和数据边界，
但新 owner 接管后应删除旧内部主路径。

## 当前边界结论

```text
Platform / EventBus / Pipeline
  -> ProcessStage
       -> Interaction 输出接管准备
       -> 官方 Plugin Handler
       -> Personal Runtime 路由与 Core 委派
       -> Native / third-party Core
  -> Personal Expression
  -> Output Runtime
  -> Postprocess / Memory / Conversation
```

- Plugin Handler 的 filter、priority、参数解析和调用发生在 Router 之前。
- Interaction 在 Plugin Handler 前只安装输出拦截和 TurnState，不改写插件输入消息。
- 插件普通结果和 `event.send()` 已进入 Interaction Output；主动
  `Context.send_message()` 仍旁路 Pipeline 和 Interaction。
- Router、Core Planner 和 Personal Expression 使用统一 ContextPack 的独立目标投影，
  但它们直接调用 Provider，不运行官方 Agent Hook。
- Plugin Tool、Skills、Knowledge、MCP、Web Search、Sandbox、Cron 和 Subagent 当前在
  `build_main_agent()` 中注入 Native Core。
- Personal Expression 只注册自身结构化表达契约和适用 effect，不执行普通业务工具。

## 插件依赖矩阵

| 能力 | 当前调用位置 | Native Core 依赖 | 当前影响 | 未来准备结论 |
| --- | --- | --- | --- | --- |
| message/command/filter Handler | WakingCheck + StarRequestSubStage | 无 | 输入和调用顺序保持官方语义 | 保持当前位置，逻辑归属 Personal Runtime 控制范围 |
| `yield MessageEventResult` | Pipeline ResultDecorate/Respond | 无 | Interaction 接管输出 | 修复发送前 Hook 兼容，不移动 Handler |
| `event.send/send_streaming` | Event 方法；Interaction 启用时被 wrapper 接管 | 无 | 默认 `plugin_direct` | 保持官方直接发送语义，不强制 Persona 改写 |
| `Context.send_message` | `Platform.send_by_session` | 无 | 主动消息旁路当前 Turn | 作为独立主动消息边界继续盘点，不纳入本轮修复 |
| `yield ProviderRequest` | Plugin Handler 后进入 AgentRequestSubStage | 有 | Router/Planner 可阻止或委派 Native Core | 保留兼容对象和调用时机 |
| Prompt Extension Collector | Canonical ContextPack collection | 部分 | 未声明 targets 的 extension 当前默认只投影 Core | 记录当前默认值；正式映射前不改变默认 |
| `OnLLMRequest/Response` | Native/third-party Agent path | 有 | Router/Planner/Expression 不触发 | 不映射到内部分类或表达调用；等待 Runtime Action 边界确定 |
| Agent begin/done、Tool Hook | Native/third-party Agent hooks | 有 | 依赖 AgentRunner 生命周期 | 纳入未来统一执行事件要求 |
| Plugin Tool | `build_main_agent()` + FunctionToolExecutor | 有 | 当前只由 Core Tool Loop 调用 | 在 Capability Snapshot 阶段确定唯一能力来源，当前不迁移调用实现 |
| `OnDecoratingResult` | ResultDecorateStage | 无 | Interaction 非流式已恢复；Core 流式仍无法在首块发送前得到完整最终文本 | 保留流式限制并继续审阅统一流事件 |
| `OnAfterMessageSent` | RespondStage | 无 | Interaction Core 可能已经提前完成 Turn | 本轮调整完成顺序 |
| postprocess/lifecycle observer | RespondStage / Interaction Middleware | 部分 | 两条路径 owner 不同 | 保持触发职责，补调用顺序测试 |

## Prompt 与能力依赖

### ContextPack

当前统一收集链负责：

- input、session、conversation、group context；
- persona、memory、policy；
- tools、skills、knowledge、subagent；
- plugin prompt extensions；
- Interaction CoreTaskSpec。

Router、Core Planner、Persona 和 Core 使用独立 Projection。默认 Prompt Extension 未声明
targets 时当前按 Core-only 处理；capability plugin directory 只有显式声明 Router/Planner
targets 才会进入分类视图。

### Native Core

`build_main_agent()` 当前同时承担：

- ProviderRequest 规范化；
- Provider 和 Conversation 选择；
- Persona toolset 筛选；
- Knowledge、Skills、MCP、Web Search、Sandbox 和 Cron 工具注入；
- Subagent Handoff 注入；
- Core ContextPack 构建、Projection、Render 和 Apply；
- AgentRunner、FunctionToolExecutor 和 Agent Hook 组装。

这些职责不能一次性被视为一个可替换接口。前置主链必须先分别确定 Prompt 准备、能力
清单、能力调用、执行事件和会话持久化的 owner，再讨论 Backend 接口。

## Subagent 依赖

当前 Subagent 链路为：

```text
config / @agent
  -> SubAgentOrchestrator
  -> HandoffTool(transfer_to_*)
  -> Native Core toolset
  -> FunctionToolExecutor special case
  -> Context.tool_loop_agent
  -> ToolLoopAgentRunner
```

前台 Handoff 把最终文本作为 Tool Result 返回父 Agent。后台 Handoff 立即返回 task id，
执行完成后创建后续事件重新唤醒主 Agent。

因此 Subagent 当前依赖 Native Tool Loop、Provider 解析、AstrAgentContext、父事件和后台
唤醒。前置清理先确定任务身份、父子关系、完成与唤醒的长期 owner；当前不改写
Handoff，也不提前决定 Subagent Service 或 Backend 选择。

## 已确认的现有问题

### 1. Interaction 跳过发送前兼容阶段

修复前 ResultDecorateStage 在 Interaction Turn 上早退，导致：

- 回复内容安全检查不运行；
- `OnDecoratingResult` 不运行；
- 依赖 Hook 修改、清空或停止结果的插件失效。

Interaction Output 已拥有前缀、TTS、t2i、reasoning 和分段物化，因此不能重新运行
完整普通装饰。当前修复按输出来源区分：

- 插件普通结果在 ResultDecorate 原位置运行内容安全和官方 Hook；
- Core 非流式结果先登记一次性兼容回调，在 Personal Expression 和 Result Contributor
  形成最终文本后、Interaction 物化前运行；
- Core 流式仍保持直接流路径，因为完整最终文本在首块发送前不可用。

流式发送前 Hook 兼容仍是保留风险，不能用发送后的完整文本检查伪装成发送前控制。

### 2. Interaction Turn 可能早于发送后 Hook 完成

Core 最终输出和 Core stream 在 OutputController 内形成 finalized material 后立即请求
Turn 持久化。RespondStage 随后才运行 `OnAfterMessageSent` 和 visible completion。

这使 `AFTER_TURN_COMPLETED` 后台任务可能早于发送后 Hook，并削弱发送后 Hook 的停止
语义。最小修复是仅对 RespondStage 驱动的 Interaction 发送延迟 Turn 持久化，在
发送后 Hook 成功和 visible completion 完成后提交。

### 3. Personal Runtime 插件默认归属尚未落地

当前只有消息 Handler 可以自然视为 Personal Runtime 控制范围。Prompt Extension、
Plugin Tool、LLM Hook、Agent Hook 和 Subagent 仍主要落在 Native Core。

这是 Personal Runtime、Capability 和插件边界收口时必须解决的设计差距，但不是本轮
输出兼容修复的一部分。不能把旧 Hook 直接挂到 Router 或 Personal Expression，因为会
破坏分类 Prompt 和结构化表达契约。

### 4. 术语仍有重叠

当前 `InteractionPersonaRuntime` 实际是 Personal Expression 门面，而总体文档中的
`Persona Runtime Shell` 指控制层。准备阶段使用显式术语映射，不进行大范围重命名；
正式实现前需要确定稳定公开名称。

## 本轮批准的修复范围

1. Interaction 插件普通结果和 Core 非流式最终 Persona 文本恢复内容安全与
   `OnDecoratingResult`，继续跳过重复普通装饰。
2. RespondStage 驱动的 Interaction 输出延迟 Turn 持久化到发送后 Hook 与 visible
   completion 之后。
3. 补充 Hook 调用、结果清空/停止、完成顺序和 postprocess owner 测试。
4. 更新流程图和准备计划中的事实与准备度条件。

## 明确延期

- Personal Runtime Action Loop。
- Tool/Prompt/Hook 的 personal/core 挂载 API。
- ExecutionBackend、Capability Gateway 和远程协议。
- Subagent Service 和后台唤醒迁移。
- 主动 `Context.send_message()` 统一接管。
- 直接依赖特定 AgentRunner 的第三方插件迁移。
- Core 流式完整文本的发送前 `OnDecoratingResult` 兼容。

完成本轮修复后，下一步是形成过渡结构清单，并依次收口 Personal Runtime、类型化状态、
Output、Prompt、Capability、Memory、插件与任务 owner。只有前置主链通过就绪复核后，
才判断是否进入 Backend 接口设计。
