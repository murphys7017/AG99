# Executive Summary

审计日期：2026-09-08。范围为当前工作区的 AstrBot/AG99 Runtime、Interaction、Persona、Core
执行、Prompt、插件编排、主动唤醒、输出边界，以及相邻 Cron、平台 Event 和 Dashboard 入口。
原始审计阶段只读，不修改业务代码、配置、测试或治理文件。随后开始的整改已单独记录在
`docs/Yakumo/架构整改实施记录.md`；本报告同步反映当前工作区的已收敛边界与仍待验证的问题。
工作区已有用户未提交改动仍视为当前状态背景，不将无关改动误记为本轮修复结果。

系统主意图是连贯的：Persona Agent 负责快速初期响应和最终拟人化表达，Core Agent 负责工作与
工具执行，Router/Planner 只负责控制决策。主要腐化发生在这个模型与 AstrBot 旧 Event、Handler、
ProviderRequest 和主动消息契约的交界处。新旧路径都能工作，但仍通过 raw event extra、方法拦截、
callback 和多个 fallback 维持兼容，导致同一输出或状态规则需要在多处理解和修改。

| 维度 | 评分 | 依据 |
| --- | --- | --- |
| 架构清晰度 | 6/10 | 双 Agent 与控制面边界已有文档和类型，但输出、插件和主动唤醒仍跨旧契约。 |
| 概念一致性 | 6/10 | Personal、Persona、expression、runtime-control 和 Core/plugin context 仍有过渡命名。 |
| 单一职责归属 | 5/10 | TurnState、OutputController、Middleware、Event wrapper 和 PluginBranch 共同参与输出生命周期。 |
| 变更可预测性 | 5/10 | 一个 capability、Prompt 约束或 fallback 需要同步多个 DTO、extra 和 adapter。 |
| 可删除性 | 5/10 | 已识别 alias、镜像 DTO 和 wrapper 候选，但外部使用仍需确认。 |
| 可观测性 | 6/10 | turn/route/deadline/表达诊断较丰富，但缺统一输出事务和新旧插件路径对照。 |
| AI 代码腐化风险 | Medium-High | 过渡层、宽泛异常、重复 DTO/别名和隐式 callback 容易诱发继续叠加 fallback。 |

结论：当前不应继续横向增加抽象。优先完成输出与 Turn 状态的边界收敛，给协调 Plugin Runtime
建立真实生产验收，再删除已确认的 alias、镜像 DTO 和旧 wrapper。

# System Mental Model

启动链为 main.py -> InitialLoader.start() -> AstrBotCoreLifecycle.initialize()/start()。
CoreLifecycle 装配 ProviderManager、PlatformManager、ConversationManager、PluginManager、
PipelineScheduler、EventBus、CronJobManager、SubAgentOrchestrator、InteractionOutputController、
InteractionMiddleware、PersonalRuntimeManager、PersonalHeartbeatSource 与唤醒调度器；插件通过
Context 获得平台、会话、Provider、Cron、主动输出和运行时观察入口。

普通消息链为 Platform/EventBus -> PipelineScheduler -> waking/whitelist/session/rate/content/
preprocess -> ProcessStage。ProcessStage 先调用 PersonalRuntimeManager.submit_platform_event()，
再按条件选择：

1. 默认 default_handler：PluginHandlerExecutor.process() 运行官方 Handler，必要时进入
   InternalAgentSubStage。
2. 开启 parallel_plugin_runtime_enabled 且事件合格时：建立 PluginBranchResult 和官方 Plugin Job，
   由 InteractionTurnCoordinator 与 Persona、Router 并行控制，Core 仅在 route/gate 允许后启动。

InteractionMiddleware 启动 Persona 与 Router。Router 返回 persona、hybrid 或群聊候选可用的 silent；
Core Planner 仅在 hybrid 时决定工作。Persona 负责即时表达，Core 结果再经 Persona 生成最终表达。

Core 链为 InternalAgentSubStage -> build_main_agent()。后者负责 Provider、能力解析、PromptContext、
CoreExecutionSpec、渲染适配和 AgentRunner。Prompt 采用 base Context Material single-flight，再共享
plugin enrichment；Persona 可等待或 best-effort，Core 等待同一 enrichment task。

Interaction 可见输出主要由 InteractionOutputController 物化、仲裁、持久化并交给平台。官方 event.send*
拦截现已封装在 InteractionEventOutputAdapter，但 PluginBranch sink、RespondStage、延迟插件投递和
主动消息仍是独立入口。Cron/后台结果另走
CronMessageEvent -> build_main_agent() -> send_message_to_user。Personal Runtime 的观察和 idle
initiation 不创建平台 Event，而是进入 RuntimeObservationEvent 和 Personal session runtime。

关键 owner：路由由 Router/Planner 决定；表达由 InteractionPersonaRuntime/
InteractionExpressionAgent 负责；工作由 Core Agent/AgentRunner 负责；内部 turn 状态由
InteractionTurnState 负责；可见输出事务由 InteractionOutputController 负责；连续对话、观察、idle
和主动人格状态由 PersonalRuntimeManager 负责。

# Top Problems

## P1: 输出生命周期仍由多个入口共同拥有

### Evidence

- output_adapter.py 的 InteractionEventOutputAdapter 保存原始 event.send、send_streaming、
  complete_visible_turn，再按 OUTPUT_ORIGIN_EXTRA_KEY 分流；middleware.py 仅安装该 adapter。
- output_controller.py:335-487 的 capture_message_chain 同时处理即时、Core progress、streaming
  finish、Core final 和重复抑制；:769-790 又通过 original completion wrapper 完成回合。
- personal_runtime.py:1876-1967、plugin_branch.py:120-388 和 delayed_plugin_delivery.py 各自拥有
  主动或插件输出入口。
- 典型链为 InternalAgentSubStage -> event.send -> InteractionEventOutputAdapter ->
  OutputController.capture_message_chain -> _deliver_core_reply -> PersonaRuntime renderer ->
  OutputController.deliver_prepared_core_reply -> _deliver_core_final_message。

### Why It Exists

官方 Handler、插件和平台仍依赖 Event 发送方法；OutputController 是事务层，显式 adapter 保留
官方 Event 兼容，Persona renderer 是表达边界。

### Why It Is Dangerous

新增输出类型必须同时理解 origin、reservation、completion、artifact、effect、stream 和 wrapper。
虽然 Core-final callback 回跳已移除，但 PluginBranch、主动输出和 Cron 仍绕开同一事务；输出入口、
completion owner 与延迟投递语义仍可能漂移。

### Recommended Direction

将已有 Event adapter 扩展到内部单一 OutputIntent/Envelope，让 Core、Persona、PluginBranch、Cron
和主动输出先进入 OutputController 事务。迁移完成前不删除 Event interception，但新状态不得再直接
新增 raw output key。

Canonical owner：OutputController 负责投递事务，PersonaRuntime 负责表达内容。
Change risk：High。Confidence：Confirmed。验证需覆盖即时/最终/流式、插件 artifact、effect、
Persona 失败和平台 completion。

## P1: 默认 Handler 路径与协调 Plugin Runtime 并存，迁移终局没有证据门槛

### Evidence

- config/default.py:216-219 与 interaction/types.py:192-220 将 parallel_plugin_runtime_enabled
  默认设为 False。
- process_stage/stage.py:185-237 按开关与资格选择路径；:542-637 保留串行 Handler-first；:269-496
  运行带 gate、Plugin Job、detach、delayed delivery 的协调路径。
- 两条路径都调用 PluginHandlerExecutor，但默认路径传 run_agent_turn，协调路径传
  delegate_provider_request，并由 InteractionTurnCoordinator 决定 PASSED/HANDLED/STOPPED/
  DELEGATED/EXPIRED。
- data/logs/astrbot.log 可见大量 path=default_handler 和少量 path=coordinated_plugin_runtime，
  证明分叉正在运行，不证明协调路径已可替代默认路径。

### Why It Exists

协调路径要保护官方 Handler generator、ProviderRequest、module lease、reload 和延迟 artifact；
默认关闭是有意的兼容策略。

### Why It Is Dangerous

同一插件行为在两条路径上可能有不同的停止、Core delegation、取消和延迟投递结果。只维护一条
路径的测试会把生产问题表现成偶发多回复、无回复或任务未结束。

### Recommended Direction

建立新旧路径同一组 production trace：Handler 终态、ProviderRequest 数、gate、detach、artifact、
completion 和 reload。达成验收后删除另一套编排，不再增加第三条分支。

Canonical owner：PluginHandlerExecutor 负责 Handler/ProviderRequest 生命周期，单一协调器负责 turn gate。
Change risk：High。Confidence：Confirmed structural fork；迁移结果 Needs confirmation。

## P1: TurnState 与 event extra 仍是双向可写边界

### Evidence

- turn_state.py:232-460 的 InteractionTurnState 已包含 route、即时表达、stream、completion、
  failure、execution scope 和 Core delegation。
- middleware.py:333-371 同时写入 _interaction_enabled、_turn_id、_interaction_output_controller；
  process_stage/stage.py:397-403 又把 gate 与 Core 延迟写入 extra；plugin_branch.py:304-306 保存
  original send keys。
- runtime_context_collector.py、context_builder.py 和平台/插件代码仍直接读取或写入
  _interaction_*；类型 helper 只覆盖部分 key。

### Why It Exists

Event extra 是官方 Pipeline/Hook/Platform 的低侵入兼容媒介，TurnState 是新内部模型，迁移尚未完成。

### Why It Is Dangerous

核心事实可能在 typed state 与 raw extra 间漂移，key 拼写没有类型保护；新贡献者容易把兼容投影误当
内部主状态，继续扩大隐式耦合。

### Recommended Direction

让 TurnState/类型化 Runtime Context 成为唯一内部可写 owner；列举并冻结必须投影给官方边界的 extra，
只允许单向写出，不允许从 extra 恢复领域主状态。完成枚举和真实插件 trace 后再删除 original send keys。

Canonical owner：InteractionTurnState。Change risk：High。Confidence：Confirmed incomplete migration。

## P2: Core 构建集中了过多决策，能力、Prompt 与 ProviderRequest 存在转换链

### Evidence

- astr_main_agent.py:954-1264 的 build_main_agent 同时负责 Provider 选择、请求初始化、persona/
  subagent 排除、知识库/网页/电脑/Cron 工具、CapabilityResolver、ContextPack、CoreExecutionSpec、
  Prompt render、NativeExecutionAdapter、fallback provider 和 AgentRunner reset。
- capabilities.py 的 CapabilitySnapshot 转成 ToolSet；astr_main_agent.py:1075-1096 再解析 ToolSet，
  :1174-1199 从 ContextPack 构造 CoreExecutionSpec 并适配回 ProviderRequest。
- InternalAgentSubStage.process()（agent_sub_stages/internal.py:170-470）还负责 request lifecycle、
  Provider 安全检查、streaming 和历史落库。

### Why It Exists

这是从旧 Main Agent 入口向类型化 Core execution boundary 迁移时的集中装配点，各子步骤都有真实协议
或生命周期价值。

### Why It Is Dangerous

增加一个 capability、Prompt 约束或 provider fallback 需要同步多个 DTO/适配器；转换顺序变化可能让
模型看到的工具、Prompt 与实际执行边界不一致。

### Recommended Direction

明确 CoreExecutionSpec 为 Core 内部 canonical contract，ProviderRequest 只作 Provider adapter；
能力解析、Prompt build/render、runner lifecycle 分成稳定阶段，但不新增总管式 manager。

Canonical owner：CoreExecutionSpec、CapabilityResolver、PromptContextBuilder 分别拥有 Core contract、
能力决策和上下文构建。Change risk：High。Confidence：Strong candidate。

## P2: Persona/Personal 命名与 runtime-control DTO 重叠

### Evidence

- persona_runtime.py:20-126 的 InteractionPersonaRuntime 是表达入口；personal_runtime.py:1447-2580
  的 PersonalRuntimeManager 同时拥有会话、观察、idle、连续对话、主动输出和生命周期。
- persona_domain.py:359-461 的 RuntimeControlSnapshot 通过 dataclasses.fields 全量镜像
  personal_state.py::PersonalStateSnapshot；:517-537 的 adapt_personal_persistent_state 只暴露持久化字段。
- EffectivePersonaContext（persona_domain.py:472-575）被 Prompt/Persona 组合使用；InteractionPersonaRuntime
  的 render_core_reply 仓库内未找到生产 caller，但模块文档仍将其定义为 wrapper。

### Why It Exists

Persona 是表达领域，Personal Runtime 是连续状态/观察领域；不可变 DTO 用来避免把可变 owner 直接交给
Prompt。当前问题是命名和镜像范围没有把边界表达清楚。

### Why It Is Dangerous

新增状态可能同时修改两套 snapshot，或把表达策略塞进 Personal Runtime；schema/version 漂移会被镜像掩盖。

### Recommended Direction

保留 PersonaDefinition、关系状态和 EffectivePersonaContext；先确认外部 API 是否需要完整
RuntimeControlSnapshot，否则收窄为 Prompt/诊断真正需要的字段。

Canonical owner：PersonalStateSnapshot（运行控制）+ EffectivePersonaContext（表达组合）。
Change risk：Medium。Confidence：Strong candidate；外部 API 使用 Needs confirmation。

## P2: 后台任务结果与 Cron 主动唤醒仍有独立的主动输出策略

### Evidence

- astr_agent_tool_exec.py 的后台 handoff/function 与 cron/manager.py 的 active-agent job 都调用
  `proactive_agent_turn.py::run_proactive_agent_turn`。
- 该 helper 统一创建 `CronMessageEvent`、恢复会话历史、挂载可选 `SendMessageToUserTool`、调用
  `build_main_agent` 并驱动 `runner.step_until_done(30)`；调用方保留各自的 Prompt、extras、角色、
  直接投递资格和 summary 持久化。
- Interaction 用户轮次在 astr_agent_tool_exec.py:161-224 将 background handoff/function 强制 foreground；
  这保护当前 Interaction 轮，但不能消除非 Interaction 后台路径。

### Why It Exists

Cron 与 detached background job 没有普通平台 Event 生命周期，必须能在原 turn 结束后唤醒 Main Agent。

### Why It Is Dangerous

合成 Core 的基础生命周期已不再复制，但两端仍不经过 InteractionOutputController；发送工具未调用时
仍可能没有可见结果，且主动输出与 Persona 统一表达的关系尚未由显式策略定义。

### Recommended Direction

以 `run_proactive_agent_turn` 作为合成 Core 生命周期的 canonical owner。后续定义
ProactiveResult/delivery contract 时，Cron 与后台任务只提交事实和目标，由一个明确策略决定 Persona 或
直接输出、投递与持久化；保留无 Event 生命周期边界，不重新复制 wake 逻辑。

Canonical owner：`run_proactive_agent_turn`（合成 Core 生命周期）；主动投递策略仍 Needs confirmation。
Change risk：High。Confidence：基础重复已收敛；是否所有结果都必须 Persona 化 Needs confirmation。

## P2: 运行配置在多个层级动态读取，冻结边界不完整

### Evidence

- interaction/config.py:20-110 将 mapping 转为 InteractionAgentConfig；Middleware 在四个
  Interaction admission 点已合并会话覆盖并将完整配置深拷贝为独立快照到
  `InteractionTurnState.runtime_config_snapshot`。
- `PersonalTurnContext`、Output Controller 和 InternalAgentSubStage 已优先读取该 typed
  快照；Core builder 将其投影为当轮 `MainAgentBuildConfig`，网页搜索、子代理装配和 Handoff
  也使用该投影。`_astrbot_config` 仅保留同一快照的兼容投影。
- Core 委派时的 Provider ID 已写入 TurnState 并由 `build_main_agent()` 优先使用；非 Interaction
  兼容路径，以及快照版本/来源诊断仍未收敛。

### Why It Exists

官方配置允许会话覆盖，Provider/TTS/平台 adapter 尚未全部迁移到 Interaction turn contract。

### Why It Is Dangerous

Interaction 的 Middleware、Persona、Output、Core 构建、Core Provider 和网页搜索运行时的配置
重读漂移已消除，但平台及其他非 Interaction 兼容入口仍可能读取不同运行设置，故障日志也难证明
实际生效配置。

### Recommended Direction

保留现有 admission 快照，将其使用范围继续扩展到平台兼容层；补充版本和来源，避免各自重新合并。

Canonical owner：InteractionTurnState 的 `interaction_config` 与 `runtime_config_snapshot`，由
admission builder 写入。
Change risk：Medium-High。Confidence：Confirmed partial convergence（Interaction 的 Core 构建
配置和 Provider 选择已收敛）。

## P3: Context pack 历史 alias 制造 Persona/Core 双实体错觉

### Evidence

- context_builder.py:292-310 的 get_or_build_interaction_core_context_pack 只是
  get_or_build_interaction_core_plugin_context_pack 的历史 alias；最终都调用
  _get_or_build_interaction_plugin_context_pack。
- Persona 与 Core 实际共享同一 plugin enrichment task，文档也定义为共用 enrichment。

### Why It Exists

历史 import 和测试需要旧名称，避免一次性破坏外部集成。

### Why It Is Dangerous

名称暗示存在独立 Persona pack/Core pack，未来贡献可能再次分叉收集逻辑。

### Recommended Direction

新代码只使用中性 plugin_context_pack 语义；枚举外部 import 后删除 alias，并同步文档与测试命名。

Canonical owner：_get_or_build_interaction_plugin_context_pack 及其中性公开接口。
Change risk：Low-Medium。Confidence：Confirmed alias；删除 Needs confirmation。

# Duplicate Concepts

| 概念 | 竞争位置 | 实际重叠 | 建议 canonical |
| --- | --- | --- | --- |
| 可见输出入口 | Event send*、PluginBranchOutputSink、InteractionOutputController、Cron/后台 wake | 都决定消息何时、以何种 origin、是否 finalize 进入平台 | OutputController + 一个 Event adapter |
| Core-final 拟人化 | Middleware core_reply_handler、OutputController _deliver_core_reply、PersonaRuntime render_core_reply | 都基于 core_final request 生成表达；callback 与 fallback 分担事务 | PersonaRuntime 表达，OutputController 投递/退化 |
| Turn 状态 | InteractionTurnState、event.extra 的 _interaction_* | route、stream、completion、gate、原始方法引用分散 | TurnState 可写，extra 单向投影 |
| Persona/Personal 状态 | PersonalStateSnapshot、RuntimeControlSnapshot、EffectivePersonaContext | runtime-control 全量镜像，表达组合另建模型 | PersonalStateSnapshot + 窄化组合模型 |
| 插件上下文 | Persona/Core helper、plugin enrichment task、Core alias | 最终都是 base + 一次 plugin enrichment | 中性 plugin context pack |
| 主动结果 | CronJobManager._woke_main_agent、FunctionToolExecutor._wake_main_agent_for_background_result | 都手工组装历史、Prompt、Main Agent 和 send_message_to_user | ProactiveResult contract |

# Suspicious Compatibility Code

## Confirmed required today

- platform/astr_message_event.py:46-47,389-459 的 original send/streaming/completion extra：官方
  Event API、Handler 和平台仍依赖方法语义。
- PluginProviderRequestBridge、module lease、detached/delegated Job 和 PluginHandlerExecutor：保护
  官方插件 generator、ProviderRequest 和 reload 生命周期。
- parallel_plugin_runtime_enabled=False：有意的迁移保护，不应仅凭默认关闭删除。
- Cron/后台使用 CronMessageEvent：没有普通平台 Event 生命周期，当前边界仍有现实需求。

## Likely removable after convergence

- original send* extra 和 PluginBranch 中仅承担 Event forwarding 的 wrapper：Output adapter 接管全部
  外部发送后。
- get_or_build_interaction_core_context_pack alias：外部 import 迁移完成后。
- adapt_personal_persistent_state：外部 DTO/API 确认无需求后。
- RuntimeControlSnapshot 的未使用镜像字段：确认 Prompt/诊断真实需求后收窄。

## Needs confirmation

- InteractionPersonaRuntime.render_core_reply 是否被插件或外部 API 反射调用；仓库内未发现生产 caller。
- core_reply_handler callback 与 OutputController fallback 哪一个应成为唯一 Core-final transaction owner。
- 默认 Handler 与协调 Plugin Runtime 的最终取舍，必须用生产 trace 决定。
- group_reply.py::select_legacy_active_reply_candidate 的 legacy 命名不能作为删除证据，它仍在群聊仲裁链。

# Excessive Defensive Programming

1. 输出 fallback 链：Middleware Persona final 失败后送 raw Core；OutputController 无 callback 时还有
   _deliver_core_reply；Event/PluginBranch wrapper 再处理 origin 和 completion。每层有边界价值，但最终
   退化规则应由一个 delivery transaction owner 统一记录。
2. 配置重复合并：Middleware、OutputController、Core builder 各自从 runtime mapping、plugin context
   和 provider settings 取值并默认化。会话覆盖是真需求，但同一轮不应多次重算。
3. Context best-effort fallback：context_builder.py:225-270 在 plugin task pending/failed/cancelled
   时回退 base pack。这是明确性能策略，应保留，但 fallback reason 应进入统一 Context snapshot。
4. 宽泛异常与静默继续：astr_main_agent.py:1110-1117、:1120-1125 等 trace 记录用 broad
   except Exception: pass；部分平台/Provider 读取也以 getattr/get_extra 静默默认。边界 telemetry 可
   容忍失败，业务契约失败不应被同样吞掉。

# Excessive Abstraction

- OutputController -> visible_reply_renderer/core_reply_handler -> Middleware -> PersonaRuntime ->
  ExpressionAgent -> OutputController 是真实职责的 callback 回跳，但隐藏 owner；应改成明确
  expression/delivery contract，不再增加 callback。
- InteractionPersonaRuntime 对 InteractionExpressionAgent 的 wrapper 有领域命名价值，只有外部 API
  确认没有独立调用才考虑合并。
- RuntimeControlSnapshot 是最值得收窄的抽象：它全量镜像可变 owner，却没有证据表明所有字段跨边界需要。
- main.py -> InitialLoader -> CoreLifecycle -> Context -> Managers 的装配层具有生命周期价值，不应为
  减少类而删除；问题在 build_main_agent 决策过密。

# Dead / Legacy Code Candidates

| 候选 | 当前证据 | 删除信心 | 提升信心所需验证 |
| --- | --- | --- | --- |
| InteractionPersonaRuntime.render_core_reply | 仓库内未找到生产调用；同类路径由 Middleware/OutputController 直接构造 request | Low-Medium | 外部插件/API import、反射调用和 fallback 收敛后的入口复核 |
| adapt_personal_persistent_state | 仅 re-export/单元测试证据，未发现生产 caller | Medium | 外部 DTO、dashboard、插件生态搜索 |
| get_or_build_interaction_core_context_pack | 明确指向 plugin helper 的历史 alias | Medium | 外部 import、下游插件迁移 |
| _interaction_original_send* | 仍被平台 Event/测试和 wrapper 读取 | Low now / High after adapter | 完成统一 Output adapter 和真实插件 trace |
| 默认/协调插件编排之一 | 两条均有当前 caller，日志也显示两者 | Low | 生产验收、reload/detach/artifact/completion 对照 |

# Single Source of Truth Violations

1. 输出事务：OutputController 负责大部分语义，但 Middleware callback、Event wrapper、PluginBranch
   和主动 wake 仍参与最终入口、origin、completion 与发送。
2. Turn 状态：TurnState 已是内部主模型，event extra 仍承担兼容引用、gate、管线标记、插件产物和
   诊断；这是未完成迁移，而非所有字段完全双写。
3. Core-final 内容与投递：PersonaRuntime/Middleware 决定表达，OutputController fallback 也能重建
   表达并投递；request 已共享，但 failure/raw fallback 责任未完全单一化。
4. 运行配置：Turn admission 有 InteractionAgentConfig 快照，OutputController/Middleware/Core 仍可
   动态读取原始 mapping。
5. 能力与请求 schema：CapabilitySnapshot -> ToolSet -> Prompt Context -> CoreExecutionSpec ->
   NativeExecutionAdapter -> ProviderRequest 是多个内部形状，尚未证明每个转换都是协议边界。
6. 主动结果模型：Cron 与 background executor 各自构建 wake event、历史 Prompt、工具和 summary。

# Observability Gaps

- 单条 turn 可通过 turn id、route、deadline、expression 和 plugin diagnostics 大致重建，但没有一个
  标准 record 同时说明哪个 wrapper 接管、original send 是否调用、哪个 output reservation 获胜、
  raw/Persona/plugin direct 选择原因和最终 completion。
- process_stage.py:239-267 的 interaction.pipeline_path 已区分 default_handler 与
  coordinated_plugin_runtime；日志中两者并存，但没有统一对照指标证明新路径覆盖所有 Handler 语义。
- immediate/final、route 和 failure 已有 TurnState；effect、artifact、delayed delivery 与平台
  message id 却横跨 Event extra、OutputController 和 PluginBranch，无法用一个 turn snapshot 还原完整事务。
- data/logs/astrbot.log 同时包含实际平台和 demo/test 条目；不按 platform、session、turn 与时间窗过滤，
  容易把测试失败当成生产故障。
- 日志能看见 Persona provider fallback、Core raw fallback 和 background foreground forcing，但缺
  Prompt/Capability snapshot 版本，难以证明模型实际拿到的工具集合与构建决策一致。

# Architectural Simplification Opportunities

按 delete > merge > converge > refactor > new abstraction 排序：

1. 外部契约确认后删除 Core context alias 和无生产 caller 的 persistent-state adapter。
2. 收敛 OutputController、PluginBranch、主动输出到单一 OutputIntent/事务，最终删除 Event method interception。
3. 让 TurnState/Runtime Context 成为唯一内部可写状态，event extra 只保留枚举后的兼容投影。
4. 将 Core-final Persona 表达、raw fallback、effect attachment、completion 收敛到一个事务。
5. 以 CoreExecutionSpec 作为 Core contract，减少 Capability/ToolSet/ProviderRequest 的无协议转换。
6. 为 Cron 与 background result 建共享 ProactiveResult contract，避免两份 Main Agent wake 逻辑。
7. 为协调 Plugin Runtime 建立生产验收门槛，最终只保留一套插件编排实现。
8. 将配置冻结、来源和版本写入 turn snapshot，减少跨层动态读取。

# Potential Delete List

这不是立即执行清单；每项都依赖 caller、外部 API 或生产 trace 验证：

- get_or_build_interaction_core_context_pack 历史 alias。
- adapt_personal_persistent_state，若无外部 DTO/API 使用。
- InteractionPersonaRuntime.render_core_reply，若无外部调用且 Core-final 入口已收敛。
- RuntimeControlSnapshot 未被 Prompt/诊断使用的镜像字段，或整个 DTO（仅在不需要稳定 schema 时）。
- _interaction_original_send、_interaction_original_send_streaming、_interaction_original_complete_visible_turn，
  仅在 Output adapter 覆盖官方 Event API 后。
- PluginBranch 内仅承担 Event forwarding 的 wrapper。
- 默认 Handler-first 或 coordinated Plugin Runtime 中未被选中的整条编排路径，必须由生产验收决定。
- Cron/background 中重复的历史拼接、Main Agent wake 和 summary 投递代码，共享 contract 后删除。

# Refactoring Order

1. 证据补齐，不改行为：记录真实 turn 的 pipeline path、Handler 终态、ProviderRequest 数、route/gate、
   Persona 状态、Core start、output reservation、effect/artifact、completion、detach、reload 和最终
   平台 message id；过滤 demo/test 日志。
2. 状态边界收敛：枚举所有 _interaction_* consumer，让 TurnState/Runtime Context 成为唯一内部 owner，
   保留单向兼容投影并加入运行时断言。
3. 输出边界收敛：定义 OutputIntent/Envelope，接入 Core、Persona、PluginBranch、Cron 和主动输出；在
   官方 Event API 上只保留一个 adapter。
4. Core contract 收敛：固定 CoreExecutionSpec、CapabilitySnapshot 和 PromptContext 边界，ProviderRequest
   只作外部 adapter；拆分 build_main_agent 阶段，但不新增全局 manager。
5. Core-final 与主动结果收敛：统一 Persona/raw fallback、effect、completion 和 ProactiveResult delivery；
   先确认外部 callback、Cron、后台插件契约。
6. 插件路径决策：以真实 trace 验收 coordinated runtime；确认后删除另一套编排，不再双写 gate/stop 规则。
7. 低风险删除：删除 alias、无调用 adapter、未使用 helper 和已被 Output adapter 替代的 original send keys。
8. 补充 rationale 与观测：为保留的兼容 adapter 写明外部契约、退出条件、版本来源和统一 turn/output trace。

# Validation and Scope Notes

- 本轮只进行了源码、调用者、配置、Git 历史线索和现有日志的静态审计；没有运行全量测试，也没有进行真实
  OLV/插件生产回放，因为用户要求只审计、不修改业务代码。
- Confirmed 只表示当前代码和调用链已足以证明结构事实；外部插件、第三方平台或反射 API 无法由仓库文本
  证明的部分均标为 Needs confirmation。
- 当前工作区的 Python、Dashboard、测试和诊断改动未被回滚；本报告没有把这些未提交改动声称为已审计修复。

# Reverse Check

- 审计按入口、控制面、执行面、输出、状态、配置和副作用建立了系统模型，没有把 Router/Planner 当作第三个
  对话 Agent。
- 没有因为类名看起来专业就赋予其价值；callback、DTO、alias 和 wrapper 均追踪了 caller 与边界作用。
- 没有把仍有 caller 的兼容代码直接判为死代码，也没有把日志中的测试条目当生产证据。
- 简化建议优先删除/合并/收敛现有路径；只有在输出和状态边界稳定后才建议进一步抽象。
