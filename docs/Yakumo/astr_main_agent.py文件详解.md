# Main Agent 职责

`astr_main_agent.py` 负责准备 Core 执行环境并启动 Agent Runner。它不再自行拼接 Persona、历史、知识库、安全策略或附件 Prompt。

## 构建顺序

```text
选择 Provider
  -> 建立或接收 ProviderRequest
  -> 准备 Persona 工具白名单与 Subagent handoff
  -> 注册知识库、Web Search、Cron、Sandbox 或 Local 工具
  -> PromptContextBuilder 收集事实
  -> project_context_pack(target)
  -> PromptRenderProfile（目标需要时）
  -> PromptLayoutInterface 语义落位
  -> PromptTreeBuilder 构建语义树
  -> Provider Renderer 序列化
  -> ProviderRequestAdapter 应用模型输入
  -> AgentRunner.reset
```

## 边界

主 Agent 可以修改运行时对象，例如 `func_tool`、provider、conversation、runner 配置和 sandbox 环境变量。模型可见的 `system_prompt`、`contexts`、当前输入与媒体只能由 Prompt 管线生成。

`PromptRenderProfile` 只用于目标局部指令和输出契约。Core 主链路通常不需要 Router/Planner/Persona Profile；Interaction Core 通过 Core 目标投影获得执行视图。

知识库非 Agentic 检索由 `KnowledgeCollector` 产生 `knowledge.snippets`；Agentic 模式只在主 Agent 注册查询工具。Persona 的文本、skills、policy、session 信息和 Core 委派意图分别由对应 Collector 提供。

`ProviderRequest` 中由插件显式提供的 contexts、content parts、图片和音频也先进入 ContextPack。应用 RenderResult 时不会在末尾进行补丁式追加。

`RenderResult.tool_schema` 不会自动更新 `ProviderRequest.func_tool`。前者属于模型输入渲染，后者属于 Main Agent 的实际能力装配。

## Interaction Core

Interaction Middleware 委派 Core 时，主 Agent 使用 Core 目标投影。Core 可见官方历史、群聊上下文、当前输入、工具、skills、知识库和结构化执行意图；不可见完整人格、动态 persona state、拟人效果、Motion、TTS 或 Live2D 语义。

Core 执行意图由 `CoreTaskCollector` 读取 turn state，主 Agent 不直接改写 `system_prompt`。

## 非职责

- 不选择 Router、Core Planner、Persona 或 Core 应该读取哪些上下文。
- 不生成 Persona Expression。
- 不解释插件 effect payload。
- 不保留另一套 legacy/shadow Prompt 管线。
