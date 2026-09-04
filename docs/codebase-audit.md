# Executive Summary

审计日期：2026-09-04。范围为当前仓库，重点追踪 AG99 Interaction / Persona Runtime、Core
执行接入、输出、插件兼容与相邻 Prompt 边界；本报告不修改运行时代码。

当前系统的主模型已经清晰：**Persona Agent** 负责即时和最终的拟人化表达，**Core Agent**
负责实际工作；Router 与 Core Planner 是控制面，不是第三个对话 Agent。Prompt、Memory 和
Capability 的方向也已明显朝单一事实流收敛。

主要架构风险不在“双 Agent”设计本身，而在其与 AstrBot 旧 Event / Pipeline / Plugin 契约的
过渡处：输出仍通过 event 实例方法替换接管；默认 Handler 路径和默认关闭的并行 Plugin Job
路径并存；类型化 TurnState 向内部唯一事实源的迁移尚未完成，部分 event extra 仍承担兼容边界
职责。这些问题会放大一次输出、并发或插件兼容变更的修改范围。

| 维度 | 评分 | 说明 |
| --- | --- | --- |
| 架构清晰度 | 6/10 | 两条 Agent 主线清晰，但输出和插件接入仍有历史路径。 |
| 概念一致性 | 6/10 | `PersonaExpressionIntent` 已收敛表达语义；Event extra、输出 origin 和 callback 仍重叠。 |
| 单一职责 / 所有权 | 5/10 | Router、Planner、Persona、Core 的决策边界较好；输出和 turn 状态仍有多 owner。 |
| 变更可预测性 | 5/10 | 修改输出、插件接管或完成语义需跨 Middleware、OutputController、ProcessStage、branch wrapper。 |
| 可删除性 | 4/10 | 兼容层相互引用，尚不具备低风险批量删除条件。 |
| 可观测性 | 6/10 | 关键路径有大量 DIAG 日志和 turn id，但同一逻辑输出跨 wrapper / callback / event extra 的还原成本高。 |

AI 代码腐化风险：**Medium**。没有发现大量无调用的生成式抽象；风险主要来自连续迭代留下的
并行路径、兼容回退和魔法状态键。所有“可删除”结论均区分当前可证实的语义与仍需真实运行验证
的兼容契约。

# System Mental Model

## 主链

```text
Platform Adapter
  -> EventBus / official Pipeline / Handler discovery
  -> Personal Runtime admission + session lease
  -> normal path: Handler takeover, otherwise Persona + Router
       -> persona: immediate user-visible expression
       -> router: persona | hybrid | (bounded group only) silent
       -> hybrid -> Core Planner -> Core execution -> Persona final expression
  -> InteractionOutputController materializes / delivers / records output
```

证据：`docs/Yakumo/README.md` 的“当前主链”；
`astrbot/core/pipeline/process_stage/stage.py::ProcessStage`;
`astrbot/core/interaction/middleware.py::run_personal_task`,
`InteractionMiddleware::run_router_task`；
`astrbot/core/interaction/router_agent.py::InteractionRouterAgent`；
`astrbot/core/interaction/core_planner.py::CorePlannerAgent`。

当 `interaction_middleware.parallel_plugin_runtime_enabled` 打开时，
`InteractionTurnCoordinator` 从相同 `t0` 并发启动 Persona、Router 和 Official Plugin Job；
插件 Gate 决定是否接管，Core 仍走 Router + Planner。证据：
`astrbot/core/interaction/turn_coordinator.py::InteractionTurnCoordinator.start`，
`astrbot/core/interaction/types.py::InteractionAgentConfig.parallel_plugin_runtime_enabled`。

## 关键 owner

| 概念 | 当前 canonical owner | 备注 |
| --- | --- | --- |
| 是否需要 Core | Router 后的 `CorePlannerAgent` | Router 只给 `persona/hybrid/silent`，Planner 给 `execute/not_required`。 |
| 人格化用户可见文本 | `InteractionExpressionAgent` / `personal_expression` | 即时和 Core-final 都是同一执行面。 |
| Core 实质工作 | Native Core 执行路径 | 通过 `CoreExecutionSpec` 接入，第三方 backend 尚未接入该边界。 |
| 输出物化与投递 | `InteractionOutputController` | 但当前仍由 Middleware 替换 Event 方法进入该 owner。 |
| 每轮并发 task 与输出仲裁 | `InteractionTurnState` / `TurnExecutionScope` | 尚未完全取代 event extra。 |
| 持续主动 Persona 状态 | `PersonalRuntimeManager` + `PersonalStateRepository` | 运行时可变状态与持久化快照已分离。 |
| 插件 FunctionTool 可见性 | `CapabilityResolver` | `plugin_tool_targets`、工具声明、Persona 白名单共同决定。 |

# Top Problems

## P1: 输出控制仍通过 Event 实例方法替换，而不是明确的 Output Port

### Evidence

- `InteractionMiddleware::_install_core_output_interceptor` 保存原始
  `event.send`、`event.send_streaming`、`event.complete_visible_turn`，再以 `MethodType` 替换三者。
- 原方法经 `_interaction_original_send*` extra 反向暴露；
  `astrbot/core/platform/astr_message_event.py` 继续读取这些 key。
- `astrbot/core/pipeline/process_stage/plugin_branch.py` 为并行插件分支重复构造另一套
  `send`、`send_streaming`、`send_message_with_extras`、`complete_visible_turn`、临时文件和
  `stop_event` wrapper。
- 调用链：`ProcessStage -> middleware.prepare_pipeline_event ->
  _install_core_output_interceptor -> event.send()/send_streaming() ->
  InteractionOutputController`；并行插件支路则为 `ProcessStage -> PluginBranch -> MethodType wrapper
  -> OutputController / artifact path`。

### Why It Exists

这是保留官方插件和平台 Event API 的兼容边界：既有 Handler、Core、工具和平台 adapter 都能继续
调用 `event.send()`，但 Interaction 需要统一记录、Persona 化、TTS、effect 和完成语义。

### Why It Is Dangerous

输出语义被分散在 Event、Middleware、OutputController、PluginBranch 和 platform fallback 中。
新增一种输出类型或修复一次重复发送，需要同步理解 wrapper 安装顺序、origin extra、
`_has_send_oper`、原始方法和 completion 回调。Event 重载还让调试时的“谁真正发送了消息”不再
能从调用点直接看出。

### Recommended Direction

将 `InteractionOutputController` 后面的投递边界收敛为显式内部 Output Port / Envelope；保留
`event.send()` 仅作为向该 Port 投递的官方兼容适配器。先让 Core、Plugin Branch 和主动输出都
产生同一类内部输出请求，再逐步删除 `_interaction_original_send*` 与 MethodType 替换。

**Canonical owner：** `InteractionOutputController` 及其未来的 Output Dispatcher。

**Change risk：High。** 需对普通 Core、流式 Core、Handler 直接输出、插件 persona/direct 输出、
主动输出、TTS/媒体与至少一个平台 adapter 做真实投递验证。

**Confidence：Confirmed。** 这不是当前可直接删除的代码；是应先收口后再删除的兼容机制。

## P1: 默认 Handler 路径与默认关闭的并行 Plugin Job 路径长期并存

### Evidence

- `InteractionAgentConfig.parallel_plugin_runtime_enabled` 默认 `False`
  (`astrbot/core/interaction/types.py`)。
- `ProcessStage` 保留默认 Handler discovery / execution 语义，同时在启用开关时构造
  `PluginJobLaunch` 并委托 `InteractionTurnCoordinator`。
- `InteractionTurnCoordinator.start` 另行管理 module lease、PluginExecutionRuntime、Gate future、
  ProviderRequest bridge、T1 window、detached completion。
- 文档 `docs/Yakumo/README.md` 与
  `docs/Yakumo/dev/parallel-plugin-runtime-plan.md` 明确记录两条路径都仍存在。

### Why It Exists

并行路径是高风险迁移：它需要保留官方 Handler generator / ProviderRequest 语义、插件 reload
lease 与延迟结果投递，因此先默认关闭进行生产验证是合理的。

### Why It Is Dangerous

同一个“插件是否接管普通 turn”的规则在两套生命周期中表达。每次改动插件输出、Router silent、
Core Gate 或异常恢复，都必须同时验证 Handler 路径和 coordinated 路径。默认关闭还意味着大量
实现无法在常规运行中持续回归，容易形成测试驱动的平行系统。

### Recommended Direction

把此项视为有明确退出条件的迁移，而不是长期功能开关：先完成 bounded private/group trace 的
生产验收，明确是否以 coordinated path 为唯一主路径；若不采用则删除 Job / Gate / branch
运行时，若采用则把默认 Handler path 缩为该协调器的兼容 adapter。不要继续在两条路径同时添加
新编排规则。

**Canonical owner：** `InteractionTurnCoordinator`（若并行插件能力保留）。

**Change risk：High。** 需要真实插件 Handler、yield `ProviderRequest`、HANDLED/STOPPED/DELEGATED、
超时 detach、插件 reload、Core 异常与延迟投递验证。

**Confidence：Confirmed as duplication; Needs confirmation on which path should survive.**

## P2: 类型化 TurnState 迁移尚未完成，event extra 仍泄漏进内部协作

### Evidence

- `InteractionTurnState` 自身保存在 event 的单个 `_interaction_turn_state` extra 中；
  `ensure_interaction_turn_state()` 只向旧 `_turn_id` 做兼容镜像，而不是把全部领域字段双写。
- 本次 Phase 1 已将 Middleware 自己使用的准备完成标记迁入
  `InteractionTurnState.pipeline_event_prepared`，并删除无内部 consumer 的
  `_output_controller` 重复镜像；`prepare_pipeline_event` 仍写入
  `_interaction_enabled` 与 `_interaction_output_controller` 等兼容边界 extra。
- `_interaction_output_controller` 现由
  `platform/astr_message_event.py::INTERACTION_OUTPUT_CONTROLLER_EXTRA_KEY` 统一声明；
  Interaction、Pipeline 与平台实现均通过该平台层常量访问，未改变既有 extra 契约。
- 同模块、`output_controller.py`、`turn_coordinator.py`、`plugin_branch.py` 和平台 Event 类大量以
  `_interaction_*`、`_turn_id`、`_output_*` key 协作。
- `turn_state.py::InteractionTurnState` 已拥有 route、即时表达、stream、final output、failure、
  execution scope 等领域字段，但调用者仍频繁绕过它读取 / 写入 event extra。
- 规划文档 `docs/Yakumo/dev/execution-backend-preparation-plan.md` Phase 2 已明确把这项列为尚未
  完成的退出条件。

### Why It Exists

event extra 是对官方 Pipeline、Hook、Handler 和 platform adapter 的最低侵入式兼容媒介；
TurnState 则是新运行时的领域模型。两者在迁移期并存有现实需要。

### Why It Is Dangerous

核心 route、planner、stream、final output 与 failure 已主要写入 TurnState，因此不能把当前情况
定性为全面的双 SSOT。本次已移除一个无 consumer 的重复 controller 镜像；剩余风险在于兼容
extra 仍被内部流程直接读写，key 拼写不受类型系统保护。新增边界状态时，仍可能将本应属于
TurnState 的事实重新散落到 raw extra。

### Recommended Direction

完成文档既定的 Phase 2：以 `InteractionTurnState` / 后续类型化 Runtime Context 作为内部可写
owner，将 event extra 缩为明确列举的官方 Pipeline、Hook、Handler 与 platform adapter 兼容投影。
先枚举外部读取的 key；新内部状态不得直接新增 raw string key，也不得从兼容 extra 反向恢复
领域主状态。已完成的第一步是删除 `_output_controller` 并将
`_interaction_output_prepared` 收敛为 `pipeline_event_prepared`；后续不应跨越官方兼容边界做
批量删除。

**Canonical owner：** `InteractionTurnState`，后续演进为文档所述的类型化 Runtime Context。

**Change risk：High。** 需要 Plugin Hook、ProviderRequest delegation、平台发送、延迟 Plugin
delivery 和 lifecycle observer 的兼容测试及真实 trace。

**Confidence：Confirmed as incomplete typed-context migration, not a pervasive dual-SSOT defect.**

## P2: Core-final Persona 化存在 callback 回跳与独立 fallback，仍可进一步收敛

### Evidence

- `InteractionMiddleware::_handle_core_reply_via_persona` 通过
  `PersonaExpressionRequest.core_final()` 取得 Core-final request、处理 Persona 错误并调用
  `InteractionOutputController::deliver_prepared_core_reply` / `deliver_raw_core_reply`。
- `InteractionOutputController::_deliver_core_reply` 复用同一 factory，并通过
  `visible_reply_renderer` 再生成 Persona 文本；本次消除了三处 request 字段的重复声明。
- 初始化时 `InteractionMiddleware.__init__` 将
  `output_controller.core_reply_handler = self._handle_core_reply_via_persona`，所以正常配置绕开
  OutputController 自己的 `_deliver_core_reply`；后者是 fallback。
- `InteractionPersonaRuntime::render_core_reply` 也复用同一 factory；仓库内未找到生产调用，
  但模块文档将它定义为 thin wrapper，不能仅以仓库搜索判为死代码。

### Why It Exists

OutputController 需要独立可构造 / 兼容运行；Middleware 需要对 Persona provider failure 记录
Interaction failure 并决定 raw Core fallback。callback 使正常路径在此处完成职责分工，
OutputController 的实现则提供未注入 handler 时的 fallback。

### Why It Is Dangerous

这不是已证实的生产重复发送或行为错误。Core-final request 的字段已收敛，剩余风险是正常 callback
路径和独立 fallback 的 failure / raw-delivery 责任可能逐步漂移；`render_core_reply` 的公开包装定位
也使 canonical API 不够直观。

### Recommended Direction

先确认 `render_core_reply`、`core_reply_handler` 对外部插件 / API 的契约。Core-final request 已收敛；
之后应收敛正常路径和 fallback 的 failure / delivery 事务，保留一个明确命名、可独立使用的
fallback adapter（如确有独立构造需求）。表达结果与失败原因应使用明确 contract，投递及 raw Core
fallback 的事务应由 OutputController 统一处理；不应在未验证外部契约前直接删除 callback 或 wrapper。

**Canonical owner：Needs confirmation.** 从当前职责看，表达应由 Persona Runtime，投递及 raw
fallback 的事务应由 OutputController。

**Change risk：Medium。** 需覆盖 Core final、Persona timeout、provider failure、空 Core
message、effect attachment 和 streamed Core final。

**Confidence：Confirmed structural cleanup candidate; no immediate production defect established.**

## P2: `RuntimeControlSnapshot` 有字段镜像成本，但 Persona 领域组合模型仍在生产使用

### Evidence

- `persona_domain.py` 定义 `RuntimeControlSnapshot`，字段基本镜像
  `personal_state.py::PersonalStateSnapshot`；其 `from_personal_state_snapshot` 使用
  `dataclasses.fields` 逐字段复制。
- `adapt_personal_persistent_state` 再将 `PersonalPersistentState` 投影成 mapping。
- `EffectivePersonaContext` 在 `context_builder.py`、`personal_policy.py` 和
  `expression_agent.py` 的生产链路中使用，不能视为测试残留。
- `RuntimeControlSnapshot` 是从 `PersonalStateSnapshot` 单向构造的不可变 DTO；
  `adapt_personal_persistent_state` 在仓库内仅见导出与测试，尚未发现生产 caller。
- 文件注释本身称其为“snapshots only”和“compatibility adapters”。

### Why It Exists

它把 PersonaCollector、Memory PersonaState 和 Personal Runtime 可变状态组合成不可变、
可序列化的 Persona 领域视图；`EffectivePersonaContext` 的跨模块 Prompt 组合职责已经有明确价值。

### Why It Is Dangerous

`PersonalStateSnapshot` 增删字段时，`RuntimeControlSnapshot` 的镜像、验证、序列化与 adapter
仍可能需要同步，形成结构性维护成本。风险局限于 runtime-control DTO 边界，而不是
`persona_domain.py` 整体没有生产价值。

### Recommended Direction

保留 `PersonaDefinition`、`PersonaRelationshipState` 与 `EffectivePersonaContext`。先确认是否需要
跨模块 / API / 持久化边界的稳定 runtime-control schema；若需要，将 `RuntimeControlSnapshot`
收窄为 Prompt / diagnostics 真正需要的字段，避免全量镜像；若不需要，再在外部 API 复核后移除
该 DTO 与无调用的 `adapt_personal_persistent_state`，由 `PersonalStateSnapshot` 提供内部只读快照。

**Canonical owner：** `PersonalStateSnapshot`（运行时控制状态）与 `Memory PersonaState`
（关系状态）；`EffectivePersonaContext` 仅在组合边界存在。

**Change risk：Medium。** 必须先检查 dashboard / plugin API 是否通过 re-export 或反射消费该
模块；当前未发现生产调用不等于外部插件未使用。

**Confidence：RuntimeControlSnapshot is a narrowing candidate; EffectivePersonaContext is confirmed production model; external API use Needs confirmation.**

## P3: Context pack 的命名仍保留过渡 alias，掩盖“一个 plugin enrichment pack”的事实

### Evidence

- `context_builder.py` 的实际构建函数为
  `_get_or_build_interaction_plugin_context_pack`。
- `get_or_build_interaction_core_context_pack` 只是对该函数的历史 import alias；
  Persona 与 Core 都复用同一 plugin pack task。
- `docs/Yakumo/README.md` 已明确“Persona / Core 共用的 plugin enrichment”。

### Why It Exists

历史调用方和测试需要旧的 Core 名称；最近提交记录也说明该 alias 曾被主动恢复以保持兼容。

### Why It Is Dangerous

名字暗示 Persona pack、Core pack 是不同实体，容易促使未来实现再分叉，而当前语义是 base pack
上只有一份 single-flight plugin enrichment。

### Recommended Direction

将 alias 视为有期限的兼容项：文档和新调用方只使用 `plugin_context_pack` 语义，记录外部 import
是否存在；确认无外部引用后删除 `get_or_build_interaction_core_context_pack` alias。

**Canonical owner：** `_get_or_build_interaction_plugin_context_pack` / 公开的中性命名。

**Change risk：Low-Medium。** 需要 import compatibility 搜索、插件生态确认和 prompt-context
targeted tests。

**Confidence：Confirmed alias, deletion Needs confirmation.**

# Duplicate Concepts

| 概念 | 位置 / 名称 | 语义重叠 | 建议 canonical model |
| --- | --- | --- | --- |
| 用户可见输出入口 | `event.send*` wrapper、`PluginBranch` wrapper、`InteractionOutputController` | 都在决定一条消息如何进入 Interaction delivery。 | OutputController / future Output Port。 |
| Core-final Persona 化 | Middleware handler、OutputController fallback、PersonaRuntime helper | 三者已共享 request factory；callback、fallback 与 wrapper 的责任边界仍待收敛。 | Persona Runtime 表达 + OutputController 投递。 |
| Turn 状态边界 | `InteractionTurnState`、event `_interaction_*` extra | TurnState 已持有核心领域状态；extra 仍承载兼容引用、管线标记和诊断。 | TurnState，extra 只做明确的单向兼容投影。 |
| Persona runtime-control 快照 | `PersonalStateSnapshot`、`RuntimeControlSnapshot` | 后者逐字段镜像前者；`EffectivePersonaContext` 是独立的生产组合模型。 | PersonalStateSnapshot，或窄化 DTO。 |
| Plugin enrichment context | `get_or_build_interaction_persona_context_pack`、历史 `...core_context_pack` alias、实际 plugin pack | 最终都指向同一个 base + plugin enrichment 任务。 | 中性 `plugin_context_pack` 名称。 |
| Persona 表达时机 | 旧 `allow_plugin_tools`、当前 `PersonaExpressionIntent` | 前者已删除；后者是正确的统一概念。 | `PersonaExpressionIntent(kind, source, phase)`。 |

# Suspicious Compatibility Code

## Confirmed required compatibility today

- Event send interception：当前官方 Handler / 插件 / 平台仍以 Event 方法发送，不能直接删除。
- `PluginProviderRequestBridge`、module lease、DELEGATED / detached Job：保护官方插件 generator 与
  reload 生命周期，必须先完成真实插件 trace 才能裁剪。
- `get_or_build_interaction_core_context_pack` alias：当前仓库有测试和历史 import 兼容目的。

## Likely removable after convergence

- `_interaction_original_send`、`_interaction_original_send_streaming`、
  `_interaction_original_complete_visible_turn`：仅在显式 Output Port 取代 Event interception 后。
- `core_reply_handler` callback、fallback 或 `render_core_reply` wrapper：先确认外部 API，再收敛
  failure / delivery 责任；不能仅凭当前仓库调用关系删除任一 owner。
- `InteractionPersonaRuntime::render_core_reply`：当前未发现生产调用；需检查外部 API 后再删除。
- `adapt_personal_persistent_state`：当前仓库未见生产调用；需检查外部 API 后再删除。

## Needs confirmation

- `group_reply.py::select_legacy_active_reply_candidate` 名称中的 `legacy` 不构成删除证据；它仍被
  group arbitration 调用，需先确认群聊连续对话策略是否仍依赖该采样逻辑。
- 默认关闭的 parallel plugin runtime 应删除还是成为唯一主路径，取决于生产 trace 而非单元测试。

# Excessive Defensive Programming

1. **输出路径的重复兜底。** Middleware 在 Persona final 失败时送 raw Core；OutputController 自己
   也有 `core_reply_handler` 有无的两条路径；Event / PluginBranch wrapper 再处理不同 origin。
   这些并非无意义，但“最终 Core 结果如何退化”应由一个 delivery transaction owner 决定。

2. **运行配置多层读取与合并。** `InteractionMiddleware::_get_runtime_config` 反复通过
   `plugin_context.get_config`、`Mapping` 检查和 `_merge_runtime_config` 组装；多个调用点再
   `load_interaction_agent_config`。此为 session 覆盖的真实需求，但应在 turn admission 形成一次
   冻结配置，而不是由 Persona / Output / Pipeline 各自读取。

3. **Context fallback 链。** compact Persona 使用已完成 plugin pack 或 base pack；Core 等待同一
   task；文档已清楚说明该性能取舍。它不是应删除的防御代码，但所有 fallback reason 应进入统一
   context snapshot diagnostics，而不是只依赖分散日志。

# Excessive Abstraction

- `InteractionPersonaRuntime` 对 `InteractionExpressionAgent` 的 `express_visible_reply` 是薄包装，
  但它目前承担 plugin output / core reply / interjection 的入口命名，尚有领域价值；不建议单独
  删除。
- `InteractionOutputController` 的 callback 注入 (`visible_reply_renderer`、`core_reply_handler`、
  `lifecycle_callback`) 形成 `OutputController -> callback -> Middleware -> PersonaRuntime ->
  ExpressionAgent -> OutputController` 的回跳。每个能力都真实存在，但组合后隐藏了 owner，优先
  用直接的 expression / delivery contract 替代 callback，而不是继续增加 callback。
- `RuntimeControlSnapshot` 的镜像 DTO 是最值得收窄的抽象；`EffectivePersonaContext` 已有生产
  consumer，不能与未见调用的 persistent-state adapter 一并判为冗余。

# Dead / Legacy Code Candidates

| 候选 | 证据 | 删除信心 | 所需确认 |
| --- | --- | --- | --- |
| `InteractionPersonaRuntime::render_core_reply` | 仓库搜索未找到生产调用；Middleware / OutputController 自行构造 Core-final request，但模块文档将其定位为 thin wrapper。 | Low-Medium | 外部插件 / API 是否导入或反射调用，以及 fallback 收敛后的公共入口。 |
| `adapt_personal_persistent_state` | 生产搜索未发现调用，仅 re-export 与 unit test。 | Medium | 同上。 |
| `get_or_build_interaction_core_context_pack` alias | 已明确是历史 import alias。 | Medium | 外部 import、下游插件。 |
| Event interception extras | 有当前消费者，不能现在删除。 | Low now / High after Output Port migration | 完成统一输出路径验证。 |

# Single Source of Truth Violations

1. **输出 owner：** OutputController 是语义 owner，但 Event wrappers 和 PluginBranch 仍参与决定
   入口、origin、completion 与发送标记。
2. **Turn-state 边界：** 核心领域状态已在 `InteractionTurnState`，但 event extra 仍被内部代码用于
   controller 引用、管线标记、插件产物与诊断；Phase 2 的兼容投影边界尚未完成。
3. **Core-final 表达：** 正常 callback 与未注入 handler 时的 fallback 已共享 Persona request，
   但仍分别承担部分 failure / delivery 行为；这是清理候选，不是当前已经确认的双重业务 owner。
4. **运行配置：** session runtime config 每次按 event 获取并合并，未在 turn 开始冻结为单一配置
   快照；对同一 turn 的配置变更可见性需要明确。
5. **Persona runtime-control snapshot：** `RuntimeControlSnapshot` 镜像 `PersonalStateSnapshot`；
   `EffectivePersonaContext` 则是独立的组合边界，不属于这一重复。

# Observability Gaps

- 单条消息目前可由 turn id 和 DIAG 日志大致还原，但无法从一个标准化 record 直接得知：
  “哪一个 wrapper 接管、原始 `event.send` 是否被调用、哪个 output reservation 赢得仲裁、最终
  raw / Persona / plugin direct 选择为何”。
- 同一 Persona turn 的 immediate 与 final 现在可从 `ProviderRequest.metadata` 中被 Hook 识别，
  这是已改善项；但日志未见统一输出每段持久关联 `PersonaExpressionIntent` 的明确要求。
- parallel plugin runtime 有 job diagnostics，但默认关闭路径与启用路径的同一组核心指标没有统一
  对照面板，因此很难以数据决定迁移是否结束。

# Architectural Simplification Opportunities

1. **收敛输出入口，随后删除 Event MethodType interception。**
2. **为 parallel plugin runtime 设定生产验收和明确终局：删除或成为主路径。**
3. **完成类型化 Turn / Runtime Context 的内部收敛，event extra 仅保留列明的兼容投影。**
4. **在外部 API 复核后，收敛已共享 Core-final request 之上的 fallback transaction。**
5. **收窄 `RuntimeControlSnapshot` 的全量镜像；保留 `EffectivePersonaContext`。**
6. **重命名并最终删除 Core context-pack 历史 alias。**
7. **在 turn admission 冻结运行配置与可观察配置版本，减少跨层动态读取。**

# Potential Delete List

这不是立即删除清单；每项均依赖前述收敛。

- `_interaction_original_send*` / original completion extra：依赖统一 Output Port。
- `PluginBranch` 内的输出 MethodType wrappers：依赖统一 Output Port。
- `core_reply_handler` callback、fallback 或 `render_core_reply` wrapper：依赖外部 API 复核和
  fallback 收敛，当前不应直接删除。
- `adapt_personal_persistent_state`：依赖确认外部 DTO 需求。
- `get_or_build_interaction_core_context_pack`：依赖外部 import 迁移。
- 未被选中的并行插件 orchestration path：依赖生产验收后的架构决策。

# Refactoring Order

1. **证据补齐，不改行为。** 对默认与并行 Plugin path 采集同格式 trace；枚举所有外部读取的
   event extra、Persona domain export 和 context-pack alias。
2. **状态收敛。** 让 TurnState / Runtime Context 成为内部唯一可写状态，保留经枚举的 event extra
   单向兼容投影和运行时断言。
3. **输出收敛。** 定义内部 OutputIntent / Envelope，先接入 Core、Plugin Branch、主动输出；
   在原 Event API 之上做 adapter，不修改外部插件 API。
4. **Core-final 收敛。** request 构造已共享；先核对外部 callback / wrapper 契约，再在 Output
   transaction 内明确 fallback、effect attachment 和 completion 的责任分工，删除确认无用的 helper。
5. **插件路径决策。** 基于真实 trace 决定 coordinated path 的最终地位；删除另一条编排实现，
   不再双写规则。
6. **低风险删除。** 在外部 contract 确认后删除 Core context alias、无调用 helper 与
   `adapt_personal_persistent_state`；仅在无需稳定 DTO 时才移除或收窄 runtime-control 镜像。
7. **补充设计 rationale / observability。** 为保留的兼容 adapter 写明外部契约、退出条件和
   一个统一 turn/output trace 视图。

## Reverse Check

本审计未把 Router / Planner 当作额外对话 Agent；未把仍有 caller 的兼容代码判为死代码；未建议
通过新增 manager 掩盖现有复杂度。建议顺序优先删除或收敛既有路径，只有在输出和状态边界稳定后
才考虑新执行 backend 或更广泛抽象。
