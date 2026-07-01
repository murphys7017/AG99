# Yakumo Current State

当前仓库更接近单体式运行时。`main.py` 负责运行环境准备、WebUI 检查和启动入口，真正的系统装配发生在 `astrbot/core/initial_loader.py` 和 `astrbot/core/core_lifecycle.py`。

## 启动链路

1. `main.py`
2. `astrbot/core/initial_loader.py`
3. `astrbot/core/core_lifecycle.py`
4. 初始化配置、数据库、Persona、Provider、平台适配器、知识库、Cron、SubAgent、PluginManager、Pipeline、Dashboard

## 当前主要模块

### 1. 运行时总装配

- `astrbot/core/core_lifecycle.py`
- `astrbot/core/initial_loader.py`

职责：

- 初始化基础组件
- 组装上下文
- 启动平台适配器
- 启动事件总线和流水线
- 启动 Dashboard

问题：

- 生命周期层掌握过多具体实现
- 运行时边界偏弱，后续拆服务时会牵一发而动全身

### 2. Agent 主体

- `astrbot/core/astr_main_agent.py`
- `astrbot/core/astr_agent_context.py`
- `astrbot/core/astr_agent_tool_exec.py`
- `astrbot/core/astr_agent_hooks.py`
- `astrbot/core/agent/*`
- `astrbot/core/prompt/*`

职责：

- 选择模型提供商
- 构造 `ProviderRequest`
- 注入人格、技能、知识库、工具、子代理委派工具
- 运行 tool loop
- 处理 sandbox/local runtime
- 处理主 Agent 输出
- 新的 prompt collect 层开始承担结构化上下文收集

问题：

- `astr_main_agent.py` 职责过载
- Agent 层直接感知 plugin context、persona、knowledge base、skills、cron、sandbox
- Agent 内核和 AstrBot 业务实现没有明确隔离
- 新的 `prompt` 模块已经完成 collect/select/render/apply 主链路，当前默认 `apply_visible` 会接管模型可见 `ProviderRequest` 字段；shadow/legacy 仍作为显式配置模式存在
- builtin 群聊上下文已接入 prompt pipeline：`GroupChatContext` 作为 prompt extension collector 向 `extension.context` 提供群聊上下文，同时保留 legacy `on_llm_request` 兜底出口；该层只提供群聊上下文材料，不接管 Yakumo memory。
- `PromptRenderEngine` 已支持按 provider metadata 的 `prompt_renderer_family` 自动选择 renderer（`OpenAIPromptRenderer`、`AnthropicPromptRenderer`、`MiniMaxPromptRenderer`、`BasePromptRenderer`），输出对应 API 原生格式
- prompt 输出约束已收口为 `OutputContract -> CompiledOutputContract -> ProviderRequest -> provider` 链路；当前 interaction fast router 不使用结构化输出契约，只返回固定路由词；persona visible-reply 使用统一的 `persona_expression` 虚拟 tool-call 契约，只有 renderer/provider 明确不支持协议工具时才受控降级为 prompt-only JSON
- TODO: 将上下文预算改为显式可配置策略，按 provider/model 支持的 `max_context_tokens` 分配 history/system/tools/memory 的预算，补齐 1M context 模型适配；现阶段 token 统计仍主要依赖估算器，容易保守截断，尚未充分利用大窗口模型
- runner 层 LLM 压缩已改为按对话轮次与 token 比例保留最近上下文，压缩请求会按压缩模型的 modalities 清洗多模态/工具内容；这是最终 request/messages 层优化，不参与 `astrbot/core/memory/*` 的记忆生成或召回。

### 2.5 Interaction Middleware

- `astrbot/core/interaction/*`
- `astrbot/core/voice/*`
- `astrbot/core/memory/postprocessor.py`
- `astrbot/core/postprocess/*`

职责：

- 在 adapter 与 core queue 之间维护 interaction turn state
- 在 core decision 之前处理入站媒体、STT、route decision 与 immediate reply
- 在 interaction turn 中接管 `event.send(...)` / `event.send_streaming(...)` 的语义输出
- 统一 visible-reply persona layer、result contributor、TTS、t2i、stream observation、stream interjection、utterance ledger 与 finalized turn material
- 将 turn completion 收口为：middleware 产出 finalized material，postprocess consumers 再消费 material；当前 memory service 与 interaction conversation history 都在 `AFTER_TURN_COMPLETED` 阶段落地
- 对普通 core 非 interaction 事件保留原 pipeline STT/TTS 兼容路径

当前已完成：

- `InteractionTurnState`、`InteractionUtterance`、`InteractionStreamState` 已成为主状态模型
- prompt / result / stream 插件扩展点已收口到只读阶段视图
- SELF_REPLY / HYBRID / DELEGATE_TO_CORE 主链路已由 middleware 持有 turn owner 语义
- interaction outbound phase 已迁入 `InteractionOutputController`
- core 旧流程与 middleware 新流程共享 voice service
- interaction 内部主链路开发期 fail-fast，不依赖 fallback 证明正确性
- **新增** `output_modes.py`：定义 `PluginOutputMode`、`OutputOrigin`、`temporary_output_origin` 等输出身份模型
- **新增** `persona_runtime.py`：`InteractionPersonaRuntime`，Persona Runtime 种子代码
- 所有用户可见自然语言已经收口到统一的 visible-reply persona 入口：
  `first_response`、插件 persona 输出、core final reply、stream interjection 不再各自维护独立文案生成器
- **新增** `emit_output()` / `send_direct()` / `send_persona()`：`AstrMessageEvent` 上的统一插件输出 helper
- `router_agent` 是轻量固定枚举分类器：只判断 `self_reply` / `hybrid`，不生成用户回复，不注册 tool-call，也不输出 effect；router 自身任务说明直接作为原生 system base 注入，上下文包含裁剪后的聊天记录、interaction memory，以及 router-scoped contributor 提供的本地/拟人层能力说明，但不内置任何具体插件协议
- `expression_agent` 已从 phase 驱动改为“visible reply material”驱动：
  prompt tree 通过 `astrbot/core/prompt` 组装材料，默认注册严格 `tool_call` 的 `persona_expression`，返回 `spoken_reply` / `effect_calls`；persona runtime 说明直接进入原生 `system.base`，`persona.prompt` 直接渲染为 `<persona>` 文本，当前轮待表达材料进入 `input.visible_reply_material`
- persona visible-reply 当前统一基线是协议级虚拟 tool-call；`prompt_only JSON` 仅作为 renderer/provider 不支持 tool-call 时的受控降级路径，自由文本仍不算成功
- 旧 `finalizer.py` 已删除；core final reply 不再走独立 finalizer provider
- stream interjection 不再在 `output_controller` 内独立拼 prompt 调模型生成文案，而是只通过统一 persona visible-reply 入口生成
- **origin 路由**：`send_wrapper` / `send_streaming_wrapper` 通过 `_interaction_output_origin` 区分 core/plugin 输出，
  `respond/stage.py` 中的 event.send / event.send_streaming 调用已加 CORE origin 标记；未标记的插件主动流式输出会走 plugin output path，不再记录为 `core_stream`

当前仍需继续收口：

- output gateway：`capture_plugin_output()` 已建立，但 `event.send` / `event.send_streaming`
  interception 仍为 MethodType 替换形态，后续可演进为正式 Output Gateway
- 插件通过 `return/yield MessageEventResult` 交给 `RespondStage` / 平台适配器发送的官方结果路径，
  仍需接入 interaction Output Runtime，并按 plugin output 归类；当前已覆盖的是插件主动
  `event.send(...)` 与 `event.send_streaming(...)` 路径
- live audio 缺 provider / 文本降级 / completion diagnostics 仍需进一步统一
- 真实平台手动日志断点仍需补齐，尤其是 Record/Image/Text 投递形态与 ledger metadata 的一致性

### 3. 插件与工具整合层

- `astrbot/core/star/context.py`
- `astrbot/core/star/star_manager.py`
- `astrbot/core/star/register/star_handler.py`
- `astrbot/core/provider/register.py`

职责：

- 暴露插件 API
- 维护插件上下文
- 注册命令、事件处理器、工具
- 将插件工具写入全局 `llm_tools`

问题：

- `star.Context` 已经是“大一统上下文”
- 插件系统直接影响 Agent 可见工具集合
- 工具注册中心和插件系统耦合过深

### 4. 基础服务实现

- `astrbot/core/provider/manager.py`
- `astrbot/core/persona_mgr.py`
- `astrbot/core/conversation_mgr.py`
- `astrbot/core/db/*`
- `astrbot/core/platform/*`
- `astrbot/core/voice/*`

职责：

- 提供模型、STT、TTS、会话、数据库、消息平台能力
- `voice` 是共享 STT/TTS service port，core 旧流程与 interaction middleware 都通过它解析 provider、执行转写/合成与记录 diagnostics

问题：

- 这些模块当前是“实现 + 装配目标”混在一起
- 还没有被抽象成稳定的基础接口层

### 5. 能力扩展模块

- `astrbot/core/skills/skill_manager.py`
- `astrbot/core/subagent_orchestrator.py`
- `astrbot/core/tools/*`
- `astrbot/core/computer/*`
- `astrbot/core/knowledge_base/*`
- `astrbot/core/cron/*`

职责：

- 提供 Skills、SubAgent、工具执行、知识库、定时任务等能力

问题：

- 多数能力是直接注入主 Agent，而不是通过独立能力层接入
- 未来拆成多服务时，协议边界尚不清晰

## 当前关键耦合点

### 1. Agent 依赖插件上下文

`astrbot/core/astr_agent_context.py` 中的 `AstrAgentContext` 直接持有 `star.Context`。这意味着 Agent 运行时不是依赖抽象接口，而是依赖完整插件运行时。

### 2. 主 Agent 直接做所有能力注入

`astrbot/core/astr_main_agent.py` 目前统一处理：

- provider 选择
- conversation 获取
- persona 注入
- skills prompt 注入
- knowledge base 注入
- subagent handoff 工具注入
- cron 工具注入
- runtime 工具注入

这使它既是内核，又是平台层，又是能力装配层。

### 3. Tool Registry 不是独立层

全局 `llm_tools` 既被 Provider 层引用，也被 PluginManager、Star 注册器、主 Agent 工具组装逻辑引用。当前没有独立的 Tool Registry/Capability Registry 边界。

### 4. 生命周期层直接掌握所有实现

`astrbot/core/core_lifecycle.py` 负责实例化几乎所有核心组件。这在单体里简单，但会限制未来把 Agent、Plugin、Skill、SubAgent 拆成单独平台或服务。

## 适合拆分的边界

### 1. Agent Kernel

保留纯 Agent 运行能力：

- message model
- tool loop runner
- handoff protocol
- hooks
- response model
- context management

### 2. Agent Platform

主服务器负责：

- API 网关
- 主 Agent 编排
- provider/stt/tts/message/persona/database 的接口访问
- session/conversation 路由
- subagent 调度入口

### 3. Capability Platform

能力平台负责：

- tools
- plugins
- skills
- sandbox/browser/python/shell
- knowledge base
- cron

## 当前拆分判断

当前代码已经具备“可拆”前提，但还不具备“直接服务化”前提。

原因：

- 已经存在主 Agent、SubAgent、Skill、Plugin、Provider、Platform 等天然模块
- 但接口层不足，抽象还没从实现里分离出来
- 更适合先做模块化重构，再做多服务部署
