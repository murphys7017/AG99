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
- `prompt` 模块已经形成唯一的 collect/build/target projection/render profile/layout/prompt tree/provider render/apply 主链路。主 Agent 只准备运行能力和事实，不再另行拼接模型可见 Prompt；目标投影是确定性代码策略，不使用 LLM Selector。
- builtin 群聊上下文只通过动态 prompt extension collector 提供结构化 `conversation.group_recent`；滚动记录不会因一次渲染被消费，该层只提供群聊上下文材料，不接管 Yakumo memory。
- `PromptRenderEngine` 先强制过滤 `llm_exposure="never"`，对显式目标再执行 target projection，然后应用 `PromptRenderProfile`。`PromptLayoutInterface.render_group(...)` 是 Builder 依赖的唯一 group 落位接口；`DefaultPromptLayout` 当前仍在内部委托 `BasePromptRenderer` 的既有落位实现，但动态方法契约已经移除。Provider renderer 只按 `prompt_renderer_family` 编译已完成的树。
- prompt 输出约束已收口为 `OutputContract -> CompiledOutputContract -> ProviderRequest -> provider` 链路；当前 interaction fast router 不使用结构化输出契约，只返回固定路由词；persona visible-reply 使用统一的 `persona_expression` 虚拟 tool-call 契约，只有 renderer/provider 明确不支持协议工具时才受控降级为 prompt-only JSON
- 当前图片输入遵循固定策略：主对话 provider 声明支持 image 时直接传图；不支持时仅使用已配置且可用的图片转述 provider；未配置或不可用时跳过图片输入，不自动切换到图像能力 fallback provider。
- runner 层 LLM 压缩已改为按对话轮次与 token 比例保留最近上下文，压缩请求会按压缩模型的 modalities 清洗多模态/工具内容；这是最终 request/messages 层优化，不参与 `astrbot/core/memory/*` 的记忆生成或召回。
- prompt collector 默认保持 required/fail-fast；只有显式 optional collector 才会局部失败并记录 `collector_failures`。当前 `MemoryCollector` 为 optional，long-term embedding/检索失败只清空长期召回，仍保留本地 Topic、ShortTerm、Experience 与 PersonaState。
- 当前 Prompt 剩余问题集中在默认 Layout 实现的物理迁移、Provider renderer 与输出契约能力、Prompt tool schema 与实际 `func_tool` 双轨、DeepSeek 首轮 Marker、ContextPack 可变表面和 Context Catalog 契约。Interaction 的跨阶段 enrichment 已统一经 `PromptContextBuilder(base=...)` 生成版本化派生快照。处理顺序见 `prompt-development-plan.md`。

### 2.5 Interaction Middleware

- `astrbot/core/interaction/*`
- `astrbot/core/voice/*`
- `astrbot/core/memory/postprocessor.py`
- `astrbot/core/postprocess/*`

职责：

- 在官方 EventBus / Pipeline 完成过滤、权限与插件处理后、核心 Agent 开始前维护 interaction turn state
- 处理入站媒体与 STT，由 Prompt 层统一采集完整事实并形成规范 `ContextPack`；Router、Core Planner、Persona 和 Core 只读取各自投影
- 在 interaction turn 中接管 `event.send(...)` / `event.send_streaming(...)` 的语义输出
- 统一 visible-reply persona layer、result contributor、TTS、t2i、stream observation、stream interjection、utterance ledger 与 finalized turn material
- 将 turn completion 收口为：middleware 产出 finalized material，先按 `turn_id` 同步幂等提交规范 Conversation，再标记 completed 并调度 postprocess；Memory Service 在 `AFTER_TURN_COMPLETED` 阶段异步消费 finalized material。Core 工具调用、结果和错误不写入可见 Conversation，而是进入独立 Core Execution Ledger
- 对普通 core 非 interaction 事件保留原 pipeline STT/TTS 兼容路径

当前已完成：

- `InteractionTurnState`、`InteractionUtterance`、`InteractionStreamState` 已成为主状态模型
- prompt / result / stream 插件扩展点已收口到只读阶段视图；通用 lifecycle observer 可读取
  `received` / `routing` / `delegated` / `speaking` / `completed` / `failed` / `cancelled`
  状态，`thinking` / `tool_running` 已作为后续执行器可上报的通用协议状态预留
- turn completion 已具有 `active` / `completed` / `failed` / `cancelled` 显式状态；
  visible output snapshot 复用 utterance 的 `message_id` / `delivered_message_ids`
- PERSONA / HYBRID 主链路由 Personal Runtime 持有 admission、session lease 和 turn task scope；middleware 负责本轮编排，`silent` 仅保留为当前 Prompt 不可达的内部类型
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
- 显式 `persona` 模式的插件流式输出会缓冲为一个完整语义文本，再经一次 Persona 表达发送；不会先透传原始流再追加改写回复。`direct` 流保持实时输出兼容。
- 插件 Handler `yield ProviderRequest` 时，ProcessStage 委托同一 turn 执行 Core；Core 返回后继续恢复插件生成器的 post-yield 逻辑和剩余 Handler，随后结束 delegated turn，不再重复进入默认 Core 路径。
- ProcessStage 在插件 Handler 前取得 Personal Runtime lease；Router、Persona、Context Material 和 Stream Observation task 由 `TurnExecutionScope` 持有，lease 释放前统一完成或取消。
- `PersonalSessionRuntime` 不再在 turn 结束后立即删除。它现在持有进程内 `PersonalState`，按 `config_id + persona_id + audience_key + privacy_scope` 跨 turn 复用；空闲实例通过 24 小时 TTL 和最多 1024 条的 LRU 边界惰性回收。Core stop 会在插件和 Provider 释放前关闭 Runtime Manager 与 PostProcessManager。窄化的 `PersonalStateRepository` 使用独立 `personal_runtime_states` 表，只恢复最近表达、冷却、静音和每日用量等重启安全控制字段；Inbox、active turn、attention、临时 Prompt 和 diagnostics 不持久化。Turn lease 释放时会从规范 turn state 和物理投递回执形成一次 `CompletionFeedback`；只有存在 `delivered_message_ids` 的可见输出才更新并持久化 `last_expression_at`。
- `PersonalRuntimeManager.submit_observation()` 是独立的系统事实入口。它按官方会话人格、session rule、配置默认人格和统一隐私规则解析同一个 RuntimeKey；不要求目标支持主动发送，不创建 `AstrMessageEvent`，也不进入 EventBus、Pipeline、Router、Planner、Core 或 Output。
- 每个 `PersonalSessionRuntime` 独占最多 64 条待处理 Observation 和一个 1.5 秒固定聚合窗口 task。显式 `coalesce_key` 按 `kind + source + coalesce_key` 保留最新事实；入队先清理过期项，满载后丢弃最旧项并记录稳定 reason。窗口内的新事实不会延长截止时间，避免持续输入导致 batch 饥饿。batch 关闭后由确定性 Gate 计算可验证 features，并按 expiry、有效材料、目标能力、mute、quiet hours、Runtime busy、冷却和预算返回 `evaluate / hold / reject`。只有 `evaluate` 可以进入默认关闭的 Personal Policy；Policy 使用独立 Provider、严格 tool-call 契约和 fail-closed `observe`。`express` 生成仅含 action ID 与表达意图的内部 `ActionIntent`，再复用同一 Runtime 的 `RuntimeObservationEvent -> Persona Expression -> Output Controller` 链路；`defer` 只写入持久化的无动作截止时间，等待后续 Observation 重新评估；`execute` 会携带完整 ObservationBatch 接受独立 Core Planner 复核，并只在生成 CoreTaskSpec 后复用既有 Core-only Pipeline，不重新进入 EventBus、普通输入阶段、Router 或插件 Handler。调用期间到达的新事实会由同一 Runtime 顺序调度为下一批。`hold` batch 会恢复到 Inbox，busy hold 在当前 turn settle 后重新评估。待处理事实和 task 存在时 Runtime 不可回收，shutdown 会取消并等待 task。
- `PromptTarget.PERSONAL_POLICY` 只投影人格摘要、有限 Conversation history、必要 Memory 和 Runtime facts；不投影工具、Skills、知识库、effect、Router 或 Planner 临时决策。`personal_policy_enabled` 默认关闭，Provider 必须显式选择；每日调用计数在 Provider 请求前先写入 Personal State Repository，持久化失败时以 `policy_usage_persistence_error` fail closed，且不会发起 Provider 请求。Action 的冷却与每日主动输出只在可见消息确认送达后更新。
- Immediate 与 Final 使用同一 turn lock 原子预留输出槽。Final 先到时取消 pending Persona；Immediate 已提交时保留 Hybrid 的双阶段输出语义。
- `Context.send_message()` 的主动纯文本输出进入 Personal Runtime；当前 session 的 Core 工具输出作为 progress，跨 session 输出建立独立 proactive turn。assistant-only 输出可进入后续 Prompt 与 Memory history。
- `platform_settings.proactive_message_target` 保存默认主动消息目标，WebUI 从已有会话中选择完整 UMO，并只展示当前支持主动消息的 Adapter。`Context.send_message(None, ...)` 与未携带 `session` 的主动 Cron 读取该目标；显式目标优先，运行时会再次校验 Adapter 是否仍可用。
- `router_agent` 是轻量二分类器：当前只判断 `persona` / `hybrid`，不生成用户回复，不注册 tool-call，也不输出 effect；`silent` 类型暂时保留但未向模型开放。直播音频和协议命令走独立 Core bypass，不伪装成 Router 结果。Router 只消费规范 `ContextPack` 的极简投影，不参与事实采集。
- Router 与 Persona Expression 在输入完成 materialization 后并发启动。Turn State 用 `pending / committed / emitted / suppressed / failed` 仲裁推测式 Persona 输出；Core 最终结果先提交时可以抑制尚未提交的即时表达。
- `core_planner` 只在 Router 选择 `hybrid` 后独立调用：它不读取 Router 的模型决策或 Prompt，只从同一事实包的 Planner 投影判断 `execute` / `not_required`。execute 生成 `CoreTaskSpec` 后才允许 Core；`not_required` 终止 Core 路径并保留并发 Persona 表达。Planner 不向即时 Persona 注入 task summary 或短回复指令。Planner 失败仍禁止 Core；若 Persona 已成功 emitted，则保留失败记录并按 Persona-only 完成本轮，否则 fail-fast。
- Core 执行上下文只声明本轮存在独立的 Persona 快速回复分支，并要求 Core 跳过寒暄、确认和进度填充，直接返回实质结果材料；Persona 的运行状态和已发送文本不进入 Core Prompt。
- Native Core 当前按 `ContextPack -> CoreExecutionSpec -> Native 目标渲染 -> RenderResult -> NativeExecutionAdapter -> ProviderRequest` 进入官方 AgentRunner。`CoreExecutionSpec` 只保存执行身份、TaskSpec、规范 ContextPack、执行历史和能力快照，不包含渲染结果或 Provider 请求。它目前仍在 Native `build_main_agent` 内形成，不是完整 Backend API；官方 `OnLLMRequest` 仍在最终 `ProviderRequest` 形成后、执行前运行。
- `CoreCapabilitySnapshot` 不再把 SubAgent 建模为一等通用能力。Native Core 仍通过 `SubagentCollector`、`SubAgentOrchestrator` 和 `HandoffTool` 兼容承载，当前 Native ContextPack 和 ToolSet 因此仍会携带 handoff 信息；未来 Backend 不需要实现 AstrBot SubAgent，新增专业能力优先注册为插件 Tool。
- Core Execution Ledger 以 `execution_id` 独立保存 task、attempt、有限工具证据、结果、错误和 token usage，并仅投影给 Core。当前记录生成仍位于 Native InternalAgentSubStage；统一 Execution Event、取消和第三方 Backend 回流尚未完成。
- Interaction 的 Prompt Contributor 在规范事实包构建阶段统一运行一次，贡献项通过 `meta.targets` 进入目标投影；Router、Planner、Persona 不再按 purpose 分别触发采集。完整事实由默认 Collector 统一收集，Core 在同一 Pack 上加入阶段性的 `CoreTaskSpec` 后投影为 Core 视图。
- `expression_agent` 已从 phase 驱动改为“visible reply material”驱动：
  prompt tree 通过 `astrbot/core/prompt` 组装材料，默认注册严格 `tool_call` 的 `persona_expression`，返回 `spoken_reply` / `effect_calls`；persona runtime 指令与输出契约由 Render Profile 提供，`persona.prompt` 直接渲染为 `<persona>` 文本，当前轮待表达材料由 Collector 进入 `input.visible_reply_material`
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
- 默认主动目标同时限定首个 Heartbeat 和群聊环境 Observation 的范围。Heartbeat 由生命周期任务以该目标实际命中的 Runtime 配置读取开关与间隔，并只调用通用 `submit_observation()`；群聊环境观察默认关闭，启用后仅放行同一默认目标中的非唤醒群聊文本，经官方白名单和会话状态检查后转换为不含原文的 `conversation_activity` fact，并在进入限流、插件、Router 和 Core 前停止原事件。两类 Source 都不构造平台事件、不直接调用 Persona/Core/Output。插件可通过 `Context.register_runtime_observation_sensor()` 注册受限的结构化事实来源；Context 只解析目标并经 Lifecycle dispatcher 交给已有 Runtime Manager，注册随插件卸载清理。多目标 registry 和内置的其他 Sensor 仍未实现。
- `CompletionFeedback` 已接入真实 turn completion。最后一份不可变反馈进入 Runtime diagnostics；`defer` 立即写入不动作冷却，带 `ActionIntent/action_id` 的 `express` 只有在可见输出确认送达后才写回复冷却并递增主动输出预算，普通被动回复不会被误算。

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
