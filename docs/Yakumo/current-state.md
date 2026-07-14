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
- `prompt` 模块已经形成唯一的 collect/build/target projection/prompt tree/provider render/apply 主链路。主 Agent 只准备运行能力和事实，不再另行拼接模型可见 Prompt；目标投影是确定性代码策略，不使用 LLM Selector。
- builtin 群聊上下文只通过动态 prompt extension collector 提供结构化 `conversation.group_recent`；滚动记录不会因一次渲染被消费，该层只提供群聊上下文材料，不接管 Yakumo memory。
- `PromptRenderEngine` 已支持按 provider metadata 的 `prompt_renderer_family` 自动选择 renderer（`OpenAIPromptRenderer`、`AnthropicPromptRenderer`、`MiniMaxPromptRenderer`、`BasePromptRenderer`），输出对应 API 原生格式
- prompt 输出约束已收口为 `OutputContract -> CompiledOutputContract -> ProviderRequest -> provider` 链路；当前 interaction fast router 不使用结构化输出契约，只返回固定路由词；persona visible-reply 使用统一的 `persona_expression` 虚拟 tool-call 契约，只有 renderer/provider 明确不支持协议工具时才受控降级为 prompt-only JSON
- 当前图片输入遵循固定策略：主对话 provider 声明支持 image 时直接传图；不支持时仅使用已配置且可用的图片转述 provider；未配置或不可用时跳过图片输入，不自动切换到图像能力 fallback provider。
- runner 层 LLM 压缩已改为按对话轮次与 token 比例保留最近上下文，压缩请求会按压缩模型的 modalities 清洗多模态/工具内容；这是最终 request/messages 层优化，不参与 `astrbot/core/memory/*` 的记忆生成或召回。
- prompt collector 默认保持 required/fail-fast；只有显式 optional collector 才会局部失败并记录 `collector_failures`。当前 `MemoryCollector` 为 optional，long-term embedding/检索失败只清空长期召回，仍保留本地 Topic、ShortTerm、Experience 与 PersonaState。
- 当前 Prompt 剩余问题集中在 Provider renderer 与输出契约能力、Prompt tool schema 与实际 `func_tool` 双轨、ContextPack 跨阶段派生、DeepSeek 首轮 Marker 和 Context Catalog 契约。处理顺序见 `prompt-development-plan.md`。

### 2.5 Interaction Middleware

- `astrbot/core/interaction/*`
- `astrbot/core/voice/*`
- `astrbot/core/memory/postprocessor.py`
- `astrbot/core/postprocess/*`

职责：

- 在官方 EventBus / Pipeline 完成过滤、权限与插件处理后、核心 Agent 开始前维护 interaction turn state
- 处理入站媒体与 STT，先用共享轻量上下文完成 route decision；只有 `persona` / `hybrid` 才调用统一 Persona Runtime
- 在 interaction turn 中接管 `event.send(...)` / `event.send_streaming(...)` 的语义输出
- 统一 visible-reply persona layer、result contributor、TTS、t2i、stream observation、stream interjection、utterance ledger 与 finalized turn material
- 将 turn completion 收口为：middleware 产出 finalized material，postprocess consumers 再消费 material；当前 memory service 与 interaction conversation history 都在 `AFTER_TURN_COMPLETED` 阶段落地
- 对普通 core 非 interaction 事件保留原 pipeline STT/TTS 兼容路径

当前已完成：

- `InteractionTurnState`、`InteractionUtterance`、`InteractionStreamState` 已成为主状态模型
- prompt / result / stream 插件扩展点已收口到只读阶段视图；通用 lifecycle observer 可读取
  `received` / `routing` / `delegated` / `speaking` / `completed` / `failed` / `cancelled`
  状态，`thinking` / `tool_running` 已作为后续执行器可上报的通用协议状态预留
- turn completion 已具有 `active` / `completed` / `failed` / `cancelled` 显式状态；
  visible output snapshot 复用 utterance 的 `message_id` / `delivered_message_ids`
- SELF_REPLY / HYBRID / DELEGATE_TO_CORE 主链路已由 middleware 持有 turn owner 语义
- interaction outbound phase 已迁入 `InteractionOutputController`
- core 旧流程与 middleware 新流程共享 voice service
- interaction 内部主链路开发期 fail-fast，不依赖 fallback 证明正确性
- **新增** `output_modes.py`：定义 `PluginOutputMode`、`OutputOrigin`、`temporary_output_origin` 等输出身份模型
- **新增** `persona_runtime.py`：`InteractionPersonaRuntime`，Persona Runtime 种子代码
- 所有用户可见自然语言已经收口到统一的 visible-reply persona 入口：
  `first_response`、插件 persona 输出、core final reply、stream interjection 不再各自维护独立文案生成器
- “快速拟人回复”只是统一 Persona Runtime 在 Core 完成前的一次表达，不是独立拟人组件；
  Output Runtime 只消费其结果并负责 TTS、文本或流式输出物化
- Core 只保存和转发通用 `effect_calls`；Motion、Live2D 等具体 effect 的解释与执行由插件负责，
  不属于 interaction 主流程的领域知识
- Persona effect 注册支持同步 `event_filter`；Persona 只把当前事件适用的 effect 编译进输出契约。无事件参数的注册表查询仅用于管理和诊断，不代表该 effect 对所有平台都可用
- **新增** `emit_output()` / `send_direct()` / `send_persona()`：`AstrMessageEvent` 上的最终插件输出 helper；`emit_progress()` / `send_progress()` 发送可见进度但不完成 turn，供随后 yield `ProviderRequest` 的插件使用。
- `router_agent` 是轻量固定枚举分类器：只判断 `silent` / `persona` / `hybrid`，不生成用户回复，不注册 tool-call，也不输出 effect；直播音频和协议命令走独立 Core bypass，不伪装成 Router 结果。Router 先完成分类，`silent` 不调用 Persona 或 Core，`persona` 和 `hybrid` 才调用统一 Persona Expression；当前 `hybrid` 仍在即时表达完成并发送后放行 Core，尚未实现目标态的并发协调与输出仲裁。Turn State 只保存 `InteractionRouteDecision`，即时回复和 effect 只随对应的 `PersonaExpressionResult` 进入输出链路，不并入 route。Router 的原生 system base 读取当前输入、`session.datetime`、当前说话者、裁剪后的聊天记录、interaction memory 和可选的本地插件目录；插件目录只保留 `name` / `description`，失败时跳过而不使 Router 降级。Router 不枚举或限制 Core 能力，也不理解具体插件协议；每轮记录 `parsed` / `fallback` 来源、失败原因、可选目录错误、模型原始标签和渲染上下文节点。
- `expression_agent` 已从 phase 驱动改为“visible reply material”驱动：
  prompt tree 通过 `astrbot/core/prompt` 组装材料，默认注册严格 `tool_call` 的 `persona_expression`，返回 `spoken_reply` / `effect_calls`；persona runtime 说明直接进入原生 `system.base`，`persona.prompt` 直接渲染为 `<persona>` 文本，当前轮待表达材料进入 `input.visible_reply_material`
- persona visible-reply 当前统一基线是协议级虚拟 tool-call；`prompt_only JSON` 仅作为 renderer/provider 不支持 tool-call 时的受控降级路径，自由文本仍不算成功
- 旧 `finalizer.py` 已删除；core final reply 不再走独立 finalizer provider
- stream interjection 不再在 `output_controller` 内独立拼 prompt 调模型生成文案，而是只通过统一 persona visible-reply 入口生成
- **origin 路由**：`send_wrapper` / `send_streaming_wrapper` 通过 `_interaction_output_origin` 区分 core/plugin 输出，
  `respond/stage.py` 中的 event.send / event.send_streaming 调用已加 CORE origin 标记；未标记的插件主动流式输出会走 plugin output path，不再记录为 `core_stream`
- 插件通过 `return/yield MessageEventResult` 交给 `RespondStage` 的非流式官方结果已按 plugin output 进入 interaction Output Runtime；core model result 和 core streaming result 仍通过 CORE origin 进入核心输出路径

当前仍需继续收口：

- output gateway：`capture_plugin_output()` 已建立，但 `event.send` / `event.send_streaming`
  interception 仍为 MethodType 替换形态，后续可演进为正式 Output Gateway
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
