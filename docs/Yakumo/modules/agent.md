# Agent Modules

## 主 Agent

`astrbot/core/astr_main_agent.py` 是 Core 执行编排入口。它当前负责：

- 选择 Provider 和 Conversation。
- 装配 `func_tool`、知识库查询工具、Web Search、Cron、Sandbox/Local 工具和 SubAgent handoff。
- 建立 Runner 配置和 fallback provider。
- 调用统一 Prompt 管线收集模型事实，并在渲染前形成 `CoreExecutionSpec`。
- 按 Native 目标渲染模型输入，再通过 `NativeExecutionAdapter` 投影到官方 `ProviderRequest`。
- 启动 Agent Runner。

它不再直接拼 Persona、历史、policy、knowledge、附件或 CoreTaskSpec 文本。这些模型可见事实由 Collector 提供，目标范围由 Projection 决定，最终格式由 Layout/Renderer/Adapter 生成。

## Prompt 与能力边界

Main Agent 仍拥有运行时能力装配，Prompt 系统只描述模型输入：

| 对象 | Owner |
|---|---|
| `ProviderRequest.system_prompt/contexts/prompt/media/output_contract` | Prompt Render + Adapter |
| `ProviderRequest.func_tool` | Main Agent / Capability 装配 |
| `CoreExecutionSpec` | Core Execution Preparation facts |
| Native `ProviderRequest` 转换 | `NativeExecutionAdapter` |
| provider、conversation、runner、sandbox 环境 | Main Agent |
| target 可见范围 | Prompt Target Projection |
| Router/Planner/Persona 决策 | Interaction 对应 Agent |

`CoreCapabilitySnapshot` 已记录本轮实际工具对象以及 Prompt 中的 tool schema、skills 和 knowledge；后面三者在形成快照时与 Prompt 构建侧分离，只有 Native `ToolSet` 作为明确的实时执行句柄保留。`RenderResult.tool_schema` 仍不会自动注册到 `func_tool`。两者尚未统一为一个可序列化能力契约，新代码不能把渲染 schema 当作可执行工具注册表。

官方 `on_llm_request` 在 Core 的统一 Prompt Apply 后运行，用于低层请求兼容。它不是 Router、Planner 或 Persona 的事实扩展入口。

## 执行连续性

Native Agent 完成后把有限工具证据、结果、错误和 token usage 写入独立 Core Execution Ledger。后续 Core Prompt 通过专用 Collector 读取最近记录；Router、Persona 和普通 Conversation API 不读取该 ledger。

当前 ledger 记录仍由 `InternalAgentSubStage` 生成，因此这只是 Native 执行准备和连续性边界，不是完整的 `ExecutionBackend` / `ExecutionEvent` 实现。取消、进度、错误翻译和第三方执行器回流仍需后续统一。

## Agent 上下文

`astrbot/core/astr_agent_context.py` 定义 `AstrAgentContext`，当前主要封装插件 `Context` 和 `AstrMessageEvent`。这仍是 Agent 与 AstrBot 业务运行时的主要耦合点，后续可收窄为 `AgentServices` 或 `AgentRuntimeFacade`。

## Tool 执行

`astrbot/core/astr_agent_tool_exec.py` 负责 function tool、handoff、MCP 和主 Agent 专用工具的执行桥接。Prompt 系统只描述模型能看到的能力，不执行这些调用。

## Agent 内核

`astrbot/core/agent/*` 包含 Agent、run context、tool 类型、tool executor、message、response、hooks 和 Tool Loop Runner。这一层最接近可替换内核，但仍引用少量 AstrBot 业务模型。

## SubAgent

`astrbot/core/subagent_orchestrator.py` 从配置构造 HandoffTool 并交给 Main Agent 装配，本身不是独立执行器。`SubagentCollector`、`SubAgentOrchestrator` 和 `HandoffTool` 继续保留官方 Native 行为；`CoreCapabilitySnapshot` 不再设置独立 SubAgent 字段，但 Native ContextPack 和 ToolSet 当前仍携带 handoff 兼容信息。Claude Code、OpenCode 等 Backend 不需要支持它，新的专业能力优先通过插件 Tool 提供。

## 当前判断

Agent 层后续仍建议收口为：

1. Agent Kernel
2. Main Agent Orchestrator
3. Capability Injection Layer

Prompt Pipeline 是三层共享的模型输入边界，不应重新并入 Main Agent 的字符串拼接逻辑。
